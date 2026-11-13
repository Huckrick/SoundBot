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
_editors: set[threading.Event] = set()
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


def _worker(connection, path, rate, channels, values, state, editor):
    # Native plugin code runs in this worker. Keep application credentials out
    # of the environment before any external code is loaded.
    for key in tuple(os.environ):
        if key.startswith("SOUNDBOT_") or any(
            word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "PROXY", "COOKIE", "AUTHORIZATION")
        ):
            os.environ.pop(key, None)
    try:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        from pedalboard import load_plugin

        plugin = load_plugin(str(_plugin_binary(path)), initialization_timeout=8.0)
        if not plugin.is_effect:
            raise ValueError("仅支持音频效果插件")
        if state is not None:
            plugin.raw_state = state
        parameters = plugin.parameters
        if any(key not in parameters for key in values):
            raise ValueError("插件参数已变化，请重新添加插件")
        for key, value in values.items():
            parameters[key].raw_value = value
        description = {
            "name": str(plugin.name)[:200],
            "parameters": [
                {"key": key, "name": str(param.name)[:120], "value": float(param.raw_value)}
                for key, param in list(parameters.items())[:128]
            ],
        }
        connection.send_bytes(b"J" + json.dumps(description, allow_nan=False).encode())
        if editor:
            before = plugin.raw_state
            close_window = threading.Event()

            def wait_for_close():
                try:
                    connection.recv_bytes(1)
                except (EOFError, OSError):
                    pass
                finally:
                    close_window.set()

            threading.Thread(target=wait_for_close, daemon=True).start()
            plugin.show_editor(close_window)
            after = plugin.raw_state
            if len(after) > _MAX_STATE:
                connection.send_bytes(b"L")
            else:
                connection.send_bytes(b"S" + after if before != after else b"S")
            return
        if not rate:
            return
        while data := connection.recv_bytes(_MAX_MESSAGE):
            audio = np.frombuffer(data, dtype=np.float32).reshape(-1, channels).T.copy()
            rendered = np.asarray(plugin.process(audio, rate, reset=False), dtype=np.float32)
            output = np.ascontiguousarray(rendered.reshape(channels, -1).T)
            connection.send_bytes(b"A" + output.tobytes())
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        try:
            connection.send_bytes(b"E")
        except OSError:
            pass
    finally:
        connection.close()


def _receive(connection, deadline, max_message=_MAX_MESSAGE):
    if not connection.poll(deadline):
        raise ValueError("VST3 插件响应超时")
    try:
        payload = connection.recv_bytes(max_message)
    except (OSError, EOFError) as exc:
        raise ValueError("VST3 插件进程异常退出") from exc
    if payload[:1] == b"L":
        raise ValueError("插件状态超出保存上限")
    if payload[:1] not in {b"A", b"J", b"S"}:
        raise ValueError("VST3 插件无法加载或处理音频，请检查兼容性")
    return payload[:1], payload[1:]


@contextmanager
def _host(path, rate=0, channels=0, values=None, *, state=None, editor=False):
    if not available():
        raise ValueError("VST3 宿主组件不可用")
    slots = _EDITOR_SLOTS if editor else _HOST_SLOTS
    if not slots.acquire(blocking=False):
        raise ValueError("请先关闭一个 VST3 编辑窗口" if editor else "VST3 渲染已占满，请减少同时启用的插件")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    worker = context.Process(target=_worker, args=(child, str(path), rate, channels, values or {}, state, editor), daemon=True)
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
        for closed in _editors:
            closed.set()


def edit(plugin_id, token=None, values=None):
    path = _registered_path(plugin_id)
    state = _decode_state(plugin_id, token)
    closed = threading.Event()
    with _editor_lock:
        if _editors_closing:
            raise ValueError("无法打开 VST3 编辑窗口")
        _editors.add(closed)
    try:
        with _host(path, values=values, state=state, editor=True) as (connection, _):
            while not connection.poll(0.2):
                if closed.is_set():
                    try:
                        connection.send_bytes(b"X")
                    except (BrokenPipeError, OSError):
                        pass
                    return {"changed": False, "state": token}
            kind, updated = _receive(connection, 0, _MAX_STATE + 1)
            if kind != b"S":
                raise ValueError("无法读取 VST3 编辑器状态")
        return {"changed": bool(updated), "state": _encode_state(plugin_id, updated) if updated else token}
    finally:
        with _editor_lock:
            _editors.discard(closed)


def process(blocks, rate, channels, plugin_id, values, token=None):
    if channels not in {1, 2}:
        raise ValueError("VST3 效果链支持单声道与立体声")
    state = _decode_state(plugin_id, token)
    with _host(_registered_path(plugin_id), rate, channels, values, state=state) as (connection, _):
        def render(block):
            connection.send_bytes(np.ascontiguousarray(block, dtype=np.float32).tobytes())
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
