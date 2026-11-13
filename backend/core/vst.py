"""Local VST3 registry and crash-isolated offline plugin processing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import multiprocessing
import os
import secrets
import sys
import threading
import time
from collections import OrderedDict, deque
from contextlib import contextmanager
from itertools import chain
from pathlib import Path
from uuid import uuid4

import numpy as np

from core.database import get_db_manager


_HOST_SLOTS = threading.BoundedSemaphore(8)
_MAX_MESSAGE = 512 * 1024
_MAX_STATE = 1024 * 1024
MAX_STATE_TOKEN = ((_MAX_STATE + 2) // 3) * 4 + 65
_state_key = secrets.token_bytes(32)
_EDITOR_SLOTS = threading.BoundedSemaphore(4)
_editor_lock = threading.Lock()
_editors = {}
_editor_states = OrderedDict()
_editors_closing = False
_catalog_lock = threading.Lock()
_candidates: dict[str, Path] = {}


def available():
    return importlib.util.find_spec("pedalboard") is not None


def initialize(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS vst_plugins (
        id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        parameters TEXT NOT NULL)""")


def _plugin_binary(path):
    candidate = Path(path).resolve(strict=True)
    if candidate.suffix.lower() != ".vst3":
        raise ValueError("请选择本地 .vst3 文件或插件包")
    if os.name == "nt" and candidate.is_dir():
        expected = candidate / "Contents" / "x86_64-win" / candidate.name
        resolved = expected.resolve(strict=True)
        if not resolved.is_relative_to(candidate) or not resolved.is_file():
            raise ValueError("插件包缺少 Windows x64 VST3 文件")
        return resolved
    return candidate


def _show_editor(plugin, connection, dark):
    from core.vst_window import editor_chrome

    initial = previous = plugin.raw_state
    style = editor_chrome(str(plugin.name), dark)
    next_sample = 0
    if 0 < len(initial) <= _MAX_STATE:
        connection.send_bytes(b"U" + initial)

    class EditorEvents:
        # Pedalboard calls this from the native GUI thread.
        def is_set(self):
            nonlocal next_sample, previous
            if connection.poll():
                return connection.recv_bytes(1) == b"X"
            if style is not None:
                style()
            now = time.monotonic()
            if now >= next_sample:
                next_sample = now + 0.3
                current = plugin.raw_state
                if current != previous:
                    previous = current
                    if 0 < len(current) <= _MAX_STATE:
                        connection.send_bytes(b"U" + current)
            return False

    plugin.show_editor(EditorEvents())
    final = plugin.raw_state
    if len(final) > _MAX_STATE:
        connection.send_bytes(b"L")
    elif final != initial:
        connection.send_bytes(b"S" + final)
    else:
        if final and final != previous:
            connection.send_bytes(b"U" + final)
        connection.send_bytes(b"S")


def _worker(connection, path, rate, channels, values, state, editor, dark):
    # Native plugin code runs in this worker. Keep application credentials out
    # of the environment before any external code is loaded.
    for key in tuple(os.environ):
        if key.startswith("SOUNDBOT_") or any(
            word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PROXY", "COOKIE", "AUTHORIZATION")
        ):
            os.environ.pop(key, None)
    dll_directory = None
    try:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        from pedalboard import load_plugin

        binary = _plugin_binary(path)
        # Keep dependent Windows DLLs discoverable for the lifetime of the worker.
        dll_directory = os.add_dll_directory(str(binary.parent)) if os.name == "nt" else None

        def load():
            plugin = load_plugin(str(binary), initialization_timeout=8.0)
            if not plugin.is_effect:
                raise ValueError("仅支持音频效果插件")
            if state is not None:
                plugin.raw_state = state
            if values:
                parameters = plugin.parameters
                if any(key not in parameters for key in values):
                    raise ValueError("插件参数已变化，请重新添加插件")
                for key, value in values.items():
                    parameters[key].raw_value = value
            return plugin

        plugin = load()
        description = {
            "name": str(plugin.name)[:200],
            "parameters": [
                {"key": key, "name": str(param.name)[:120], "value": float(param.raw_value)}
                for key, param in list(plugin.parameters.items())[:128] if np.isfinite(param.raw_value)
            ],
        }
        connection.send_bytes(b"J" + json.dumps(description, allow_nan=False).encode())
        if editor:
            _show_editor(plugin, connection, dark)
            return
        if not rate:
            return
        stereo_adapter = False
        first_block = True
        while data := connection.recv_bytes(_MAX_STATE + 1):
            if data[:1] == b"S":
                state = data[1:]
                plugin.raw_state = state
                connection.send_bytes(b"S")
                continue
            if data[:1] != b"A":
                raise ValueError("VST3 返回的音频格式无效")
            audio = np.frombuffer(data, offset=1, dtype=np.float32).reshape(-1, channels).T.copy()
            if first_block:
                first_block = False
                try:
                    rendered = plugin.process(audio, rate, buffer_size=512, reset=False)
                except ValueError as exc:
                    detail = str(exc)
                    if channels != 1 or not any(text in detail for text in (
                        "does not support 1-channel", "but provided audio data contained 1 channel",
                    )):
                        raise
                    plugin = load()
                    stereo_adapter = True
                    rendered = plugin.process(np.repeat(audio, 2, axis=0), rate, buffer_size=512, reset=False)
            else:
                adapted = np.repeat(audio, 2, axis=0) if stereo_adapter else audio
                rendered = plugin.process(adapted, rate, buffer_size=512, reset=False)
            if stereo_adapter:
                rendered = np.asarray(rendered).reshape(2, -1).mean(axis=0, keepdims=True)
            output = np.ascontiguousarray(np.asarray(rendered, dtype=np.float32).reshape(channels, -1).T)
            connection.send_bytes(b"A" + output.tobytes())
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            message = str(exc)
            expected = {"仅支持音频效果插件", "插件参数已变化，请重新添加插件"}
            connection.send_bytes(b"E" + (message.encode() if message in expected else b""))
        except OSError:
            pass
    finally:
        connection.close()
        if dll_directory is not None:
            dll_directory.close()


def _receive(connection, deadline, max_message=_MAX_MESSAGE):
    if not connection.poll(deadline):
        raise ValueError("VST3 插件响应超时")
    try:
        payload = connection.recv_bytes(max_message)
    except (OSError, EOFError) as exc:
        raise ValueError("VST3 插件进程异常退出") from exc
    if payload[:1] == b"L":
        raise ValueError("插件状态超出保存上限")
    if payload[:1] == b"E" and payload[1:]:
        raise ValueError(payload[1:].decode())
    if payload[:1] not in {b"A", b"J", b"S", b"U"}:
        raise ValueError("VST3 插件无法加载或处理音频，请检查兼容性")
    return payload[:1], payload[1:]


@contextmanager
def _host(path, rate=0, channels=0, values=None, *, state=None, editor=False, dark=False):
    if not available():
        raise ValueError("VST3 宿主组件不可用")
    slots = _EDITOR_SLOTS if editor else _HOST_SLOTS
    if not slots.acquire(blocking=False):
        raise ValueError("请先关闭一个 VST3 编辑窗口" if editor else "VST3 渲染已占满，请减少同时启用的插件")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    worker = context.Process(target=_worker, args=(child, str(path), rate, channels, values or {}, state, editor, dark), daemon=True)
    try:
        worker.start()
        child.close()
        kind, raw = _receive(parent, 16.0)
        if kind != b"J":
            raise ValueError("VST3 插件未完成初始化")
        yield parent, json.loads(raw)
    finally:
        child.close()
        parent.close()
        if worker.pid is not None:
            worker.join(timeout=0.25)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
            worker.close()
        slots.release()


def register(path):
    source = Path(path).resolve(strict=True)
    _plugin_binary(source)
    with _host(source) as (_, info):
        pass
    with get_db_manager().get_cursor() as cursor:
        initialize(cursor)
        row = cursor.execute("""INSERT INTO vst_plugins(id,path,name,parameters) VALUES(?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET name=excluded.name,parameters=excluded.parameters RETURNING id""",
            (uuid4().hex, str(source), info["name"], json.dumps(info["parameters"]))).fetchone()
    return {"id": row["id"], **info}


def _plugin_roots():
    if sys.platform == "darwin":
        return (Path.home() / "Library/Audio/Plug-Ins/VST3", Path("/Library/Audio/Plug-Ins/VST3"))
    if sys.platform == "win32":
        user = os.environ.get("LOCALAPPDATA")
        common = os.environ.get("CommonProgramW6432") or os.environ.get("CommonProgramFiles")
        return tuple(path for path in (
            Path(user) / "Programs/Common/VST3" if user else None,
            Path(common) / "VST3" if common else None,
        ) if path is not None)
    return (Path.home() / ".vst3", Path("/usr/local/lib/vst3"), Path("/usr/lib/vst3"))


def _installed_packages():
    for root in _plugin_roots():
        for directory, folders, files in os.walk(root):
            # A bundle is a single plug-in. Its internal binary must not show as a second entry.
            packages = [name for name in folders if name.lower().endswith(".vst3")]
            folders[:] = [name for name in folders if not name.lower().endswith(".vst3")]
            yield from (Path(directory) / name for name in packages)
            yield from (Path(directory) / name for name in files if name.lower().endswith(".vst3"))


def discover():
    """List installed candidates without loading any native code."""
    if not available():
        return {"available": False, "plugins": []}
    with _catalog_lock:
        with get_db_manager().get_cursor() as cursor:
            initialize(cursor)
            known = {Path(row["path"]): row["name"]
                     for row in cursor.execute("SELECT path,name FROM vst_plugins")}
        paths = {}
        names = {}
        for path in chain(_installed_packages(), known):
            try:
                source = path.resolve(strict=True)
                if sys.platform == "darwin" and not (
                    source.is_dir() and (source / "Contents/MacOS").is_dir()
                ):
                    continue
                _plugin_binary(source)
                if not (source.is_file() or source.is_dir()):
                    continue
            except (OSError, RuntimeError, ValueError):
                continue
            candidate_id = hashlib.sha256(os.fsencode(os.path.normcase(source))).hexdigest()
            paths[candidate_id] = source
            names[candidate_id] = known.get(source) or path.stem
        _candidates.clear()
        _candidates.update(paths)
        return {"available": True, "plugins": [
            {"id": plugin_id, "name": name}
            for plugin_id, name in sorted(names.items(), key=lambda item: (item[1].casefold(), item[0]))
        ]}


def load_discovered(plugin_id):
    with _catalog_lock:
        source = _candidates.get(plugin_id)
    if source is None:
        raise ValueError("插件列表已变化，请重新扫描 VST3")
    if source.resolve(strict=True) != source:
        raise ValueError("插件路径已变化，请重新扫描 VST3")
    return register(source)


def _registered_path(plugin_id):
    with get_db_manager().get_cursor() as cursor:
        initialize(cursor)
        item = cursor.execute("SELECT path FROM vst_plugins WHERE id=?", (plugin_id,)).fetchone()
    if not item:
        raise ValueError("所用 VST3 插件已移除")
    return item["path"]


def _sign_state(plugin_id, state):
    return hmac.new(_state_key, plugin_id.encode() + b"\0" + state, hashlib.sha256).hexdigest()


def _encode_state(plugin_id, state):
    return _sign_state(plugin_id, state) + "." + base64.b64encode(state).decode("ascii")


def _decode_state(plugin_id, token):
    if token is None:
        return None
    if len(token) > MAX_STATE_TOKEN:
        raise ValueError("插件状态超出保存上限")
    signature, _, encoded = token.partition(".")
    try:
        state = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("插件状态无效，请重新打开编辑窗口") from exc
    if (len(signature) != 64 or not signature.isascii() or not 0 < len(state) <= _MAX_STATE
            or not hmac.compare_digest(signature, _sign_state(plugin_id, state))):
        raise ValueError("插件状态无效，请重新打开编辑窗口")
    return state


def close_editors():
    global _editors_closing
    with _editor_lock:
        _editors_closing = True
        for closed in _editors.values():
            closed.set()


def close_editor(instance):
    with _editor_lock:
        if closed := _editors.get(instance):
            closed.set()
            return True
    return False


def _editor_state(plugin_id, instance, fallback):
    if instance is None:
        return fallback
    with _editor_lock:
        current = _editor_states.get(instance)
        return current[1] if current and current[0] == plugin_id else fallback


def edit(plugin_id, token=None, values=None, *, instance=None, dark=False):
    path = _registered_path(plugin_id)
    state = _decode_state(plugin_id, token)
    closed = threading.Event()
    instance = instance or uuid4().hex
    with _editor_lock:
        if _editors_closing or instance in _editors:
            raise ValueError("无法打开 VST3 编辑窗口")
        _editors[instance] = closed
        _editor_states.pop(instance, None)

    def update(raw):
        with _editor_lock:
            existing = _editor_states.get(instance)
            if existing is not None and raw == existing[1]:
                return
            _editor_states[instance] = (plugin_id, raw)
            _editor_states.move_to_end(instance)
            if len(_editor_states) > 16:
                _editor_states.popitem(last=False)

    try:
        with _host(path, values=values, state=state, editor=True, dark=dark) as (connection, _):
            close_deadline = None
            while True:
                if closed.is_set() and close_deadline is None:
                    connection.send_bytes(b"X")
                    close_deadline = time.monotonic() + 8
                if close_deadline is not None and time.monotonic() >= close_deadline:
                    raise ValueError("VST3 插件响应超时")
                if not connection.poll(0.1):
                    continue
                kind, updated = _receive(connection, 0, _MAX_STATE + 1)
                if kind == b"U" and updated:
                    update(updated)
                    continue
                if kind != b"S":
                    raise ValueError("无法读取 VST3 编辑器状态")
                if updated:
                    update(updated)
                break
        return {"changed": bool(updated), "state": _encode_state(plugin_id, updated) if updated else token}
    except (OSError, ValueError):
        with _editor_lock:
            _editor_states.pop(instance, None)
        raise
    finally:
        with _editor_lock:
            _editors.pop(instance, None)


def _process(blocks, rate, channels, plugin_id, values, token, instance=None):
    if channels not in {1, 2}:
        raise ValueError("VST3 效果链支持单声道与立体声")
    baseline = _decode_state(plugin_id, token)
    state = _editor_state(plugin_id, instance, baseline)
    if state is not baseline:
        values = None
    with _host(_registered_path(plugin_id), rate, channels, values, state=state) as (connection, _):
        def render(block):
            nonlocal state
            latest = _editor_state(plugin_id, instance, baseline)
            if latest is not None and latest != state:
                connection.send_bytes(b"S" + latest)
                kind, _ = _receive(connection, 8.0)
                if kind != b"S":
                    raise ValueError("无法读取 VST3 编辑器状态")
                state = latest
            connection.send_bytes(b"A" + np.ascontiguousarray(block, dtype=np.float32).tobytes())
            kind, raw = _receive(connection, 8.0)
            if kind != b"A" or len(raw) % (4 * channels):
                raise ValueError("VST3 返回的音频格式无效")
            audio = np.frombuffer(raw, dtype=np.float32).reshape(-1, channels)
            if not np.isfinite(audio).all():
                raise ValueError("VST3 返回无效采样，请旁通该插件")
            return audio

        expected = delivered = 0
        for block in blocks:
            for offset in range(0, len(block), 8192):
                chunk = block[offset:offset + 8192]
                expected += len(chunk)
                if expected - delivered > 64 * 4096:
                    raise ValueError("VST3 未返回完整的渲染音频")
                audio = render(chunk)
                if len(audio) > expected - delivered:
                    raise ValueError("VST3 返回的音频长度无效")
                delivered += len(audio)
                if len(audio):
                    yield audio
        silence = np.zeros((4096, channels), dtype=np.float32)
        for _ in range(64):
            if delivered == expected:
                break
            audio = render(silence)[:expected - delivered]
            delivered += len(audio)
            if len(audio):
                yield audio
        if delivered != expected:
            raise ValueError("VST3 未返回完整的渲染音频")



def process(blocks, rate, channels, plugin_id, values, token=None, *, instance=None, on_error=None):
    if on_error is None:
        yield from _process(blocks, rate, channels, plugin_id, values, token)
        return

    # Keep only the input still awaiting plug-in output. If a plug-in fails,
    # resume with the corresponding dry samples so the preview keeps its time.
    pending = deque()
    source = iter(blocks)

    class InputFailure(Exception):
        pass

    def tapped():
        try:
            for block in source:
                pending.append(block.copy())
                yield block
        except (OSError, ValueError) as exc:
            raise InputFailure from exc

    rendered = _process(tapped(), rate, channels, plugin_id, values, token, instance)
    try:
        while True:
            try:
                audio = next(rendered)
            except StopIteration:
                return
            except InputFailure as exc:
                raise exc.__cause__ from None
            except (OSError, ValueError) as exc:
                on_error(plugin_id, str(exc))
                yield from pending
                yield from source
                return
            frames = len(audio)
            while frames and pending:
                dry = pending.popleft()
                if frames < len(dry):
                    pending.appendleft(dry[frames:])
                    break
                frames -= len(dry)
            yield audio
    finally:
        rendered.close()
        if close := getattr(source, "close", None):
            close()
