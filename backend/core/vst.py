"""Local VST3 registry and crash-isolated preview, editing, and export."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import logging
import multiprocessing
import os
import secrets
import subprocess
import sys
import threading
import time
from collections import OrderedDict, deque
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from itertools import chain, islice
from pathlib import Path
from uuid import uuid4

import numpy as np

from core import plugin_catalog
from core.database import get_db_manager


_logger = logging.getLogger("soundbot")
_HOST_SLOTS = threading.BoundedSemaphore(8)
_WORKER_START_LOCK = threading.Lock()
_MAX_MESSAGE = 512 * 1024
_MAX_STATE = 1024 * 1024
_AUDIO_FRAMES = 8192
_DSP_FRAMES = 512
_RESPONSE_TIMEOUT = 8.0
MAX_STATE_TOKEN = ((_MAX_STATE + 2) // 3) * 4 + 65
_state_key = secrets.token_bytes(32)
_EDITOR_SLOTS = threading.BoundedSemaphore(4)
_editor_lock = threading.Lock()
_editors = {}
_editor_states = OrderedDict()
_editors_closing = False
_catalog_lock = threading.Lock()
_candidates: dict[str, plugin_catalog.Plugin] = {}
_non_effects = OrderedDict()
_NON_EFFECT_TYPES = {"instrument", "sampler", "synth", "generator"}


class _NonEffect(ValueError):
    def __init__(self, kind="instrument"):
        super().__init__("仅支持音频效果插件")
        self.kind = kind if kind in _NON_EFFECT_TYPES else "instrument"


def available():
    return importlib.util.find_spec("pedalboard") is not None


def initialize(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS vst_plugins (
        id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        parameters TEXT NOT NULL)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS vst_plugin_classes (
        id TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL,
        vendor TEXT NOT NULL, types TEXT NOT NULL, parameters TEXT NOT NULL,
        UNIQUE(path, name))""")


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


def _raw_parameters(plugin):
    # Read normalized parameters without Pedalboard's text/range inference.
    # These binding names are covered by the packaged VST3 smoke test.
    from pedalboard._pedalboard import PARAMETER_NAME_REGEXES_TO_IGNORE, to_python_parameter_name

    return {key: param for param in plugin._parameters
            if not any(regex.match(param.name) for regex in PARAMETER_NAME_REGEXES_TO_IGNORE)
            if (key := to_python_parameter_name(param))}


def _set_values(plugin, values):
    if not values:
        return
    parameters = _raw_parameters(plugin)
    if any(key not in parameters for key in values):
        raise ValueError("插件参数已变化，请重新添加插件")
    for key, value in values.items():
        parameters[key].raw_value = value


def _freq_echo_on_mac(plugin):
    return (sys.platform == "darwin" and plugin.name.casefold() == "valhallafreqecho"
            and plugin.manufacturer_name.casefold().startswith("valhalla dsp"))


def _load_effect(module, effect_name):
    from pedalboard import ExternalPluginReloadType, VST3Plugin

    # The pinned binding calls this hook even without initial values, probing
    # 1,001 display strings per parameter. Only override it inside this worker,
    # during construction. Normalized values are applied explicitly afterwards.
    def init_without_values(self, values):
        if values:
            raise ValueError("VST3 参数无效")

    def verify(plugin):
        kind, _ = plugin_catalog.categories(plugin.category)
        if plugin.is_instrument or not plugin.is_effect or kind in _NON_EFFECT_TYPES:
            raise _NonEffect(kind)
        if (_freq_echo_on_mac(plugin)
                and plugin._reload_type == ExternalPluginReloadType.ClearsAudioOnReset):
            # The pinned host's reset-detection probe leaves this processor's
            # wet signal invalid but classifies it as safe to reset in place.
            # Recreate once before opening an editor. The recreation path
            # skips the probe when the reset policy is already known.
            plugin._reload_type = ExternalPluginReloadType.PersistsAudioOnReset
            plugin.reset()
        return plugin

    original = VST3Plugin.__set_initial_parameter_values__
    VST3Plugin.__set_initial_parameter_values__ = init_without_values
    try:
        try:
            # This timeout controls the MIDI warm-up for instruments. The
            # effects rack has no instrument input.
            plugin = VST3Plugin(module, plugin_name=effect_name, initialization_timeout=0)
        except ValueError as exc:
            if effect_name is not None or "contains" not in str(exc) or "plugin_name" not in str(exc):
                raise
            for name in VST3Plugin.get_plugin_names_for_file(module):
                plugin = VST3Plugin(module, plugin_name=name, initialization_timeout=0)
                try:
                    return verify(plugin)
                except _NonEffect:
                    continue
            raise _NonEffect() from None
        return verify(plugin)
    finally:
        VST3Plugin.__set_initial_parameter_values__ = original


class _Resample:
    def __init__(self, source_rate, target_rate, channels):
        from av import AudioFrame, AudioResampler

        self.frame_type = AudioFrame
        self.source_rate = source_rate
        self.layout = "mono" if channels == 1 else "stereo"
        self.resampler = AudioResampler(format="fltp", layout=self.layout, rate=target_rate)

    def __call__(self, block):
        if not len(block):
            return block
        frame = self.frame_type.from_ndarray(np.ascontiguousarray(block.T), format="fltp", layout=self.layout)
        frame.sample_rate = self.source_rate
        frames = self.resampler.resample(frame)
        if not frames:
            return block[:0]
        return np.concatenate([frame.to_ndarray().T for frame in frames])


class _PluginAudio:
    """Audio blocks for one processor, always called on its owning thread."""

    def __init__(self, plugin):
        self.plugin = plugin
        # FreqEcho's recreated processor is stable at 48 kHz. Keep that
        # processing rate when the source changes, including during delivery.
        self.fixed_rate = 48000 if _freq_echo_on_mac(plugin) else None
        self.resamplers = None
        self.rate = self.channels = 0
        self.stereo_adapter = False
        self.has_audio = False
        self.silent_frames = 0
        self.skip_frames = 0
        self.values = {}

    def begin(self, rate, channels, values):
        from pedalboard import ExternalPluginReloadType

        if self.has_audio:
            if self.plugin._reload_type == ExternalPluginReloadType.ClearsAudioOnReset:
                self.plugin.reset()
            else:
                # Pedalboard would replace this processor on reset, invalidating
                # its open editor. Drain its delay lines within the tail budget.
                silence = np.zeros((_AUDIO_FRAMES, self.channels), dtype=np.float32)
                remaining = max(0, 12 * self.rate - self.silent_frames)
                for offset in range(0, remaining, len(silence)):
                    self._render(silence[:min(len(silence), remaining - offset)])
                # This processor's counter is still warm. Drop its initial
                # delayed samples explicitly on the next source or seek.
                self.skip_frames = self.plugin.reported_latency_samples
        self.has_audio = False
        self.silent_frames = 0
        self.resamplers = None
        processing_rate = self.fixed_rate or rate
        if processing_rate != rate:
            self.resamplers = (_Resample(rate, processing_rate, channels), _Resample(processing_rate, rate, channels))
        if (self.rate, self.channels) != (processing_rate, channels):
            self.stereo_adapter = bool(self.fixed_rate and channels == 1)
        self.rate, self.channels = processing_rate, channels
        if values != self.values:
            _set_values(self.plugin, values)
            self.values = values

    def render(self, block):
        if self.resamplers is None:
            return self._render(block)
        incoming, outgoing = self.resamplers
        return outgoing(self._render(incoming(block)))

    def _render(self, block):
        channels = self.channels
        count = len(block)
        if not count:
            return block

        def run(stereo):
            planar = np.ascontiguousarray(block.T)
            if stereo:
                planar = np.repeat(planar, 2, axis=0)
            # Pedalboard infers the layout. Remove ambiguity for very short
            # clips and the single-sample final block of a render.
            padded = max(3, count)
            if padded != count:
                planar = np.pad(planar, ((0, 0), (0, padded - count)))
            return self.plugin.process(planar, self.rate, buffer_size=_DSP_FRAMES, reset=False)

        try:
            output = run(self.stereo_adapter)
        except ValueError as exc:
            if channels != 1 or self.stereo_adapter or not any(message in str(exc) for message in (
                "does not support 1-channel", "but provided audio data contained 1 channel",
            )):
                raise
            output = run(True)
            self.stereo_adapter = True
        self.has_audio = True
        self.silent_frames = 0 if np.any(block) else self.silent_frames + count
        if self.stereo_adapter:
            output = output.mean(axis=0, keepdims=True)
        skip = min(self.skip_frames, output.shape[1])
        self.skip_frames -= skip
        return np.ascontiguousarray(output[:, skip:count].T, dtype=np.float32)

    def command(self, connection, data):
        kind = data[:1]
        if kind == b"B":
            self.begin(**json.loads(data[1:]))
            connection.send_bytes(b"B")
        elif kind == b"A":
            audio = np.frombuffer(data, offset=1, dtype=np.float32).reshape(-1, self.channels)
            connection.send_bytes(b"A" + self.render(audio).tobytes())
        elif kind == b"T":
            if self.has_audio:
                self._render(np.zeros((_DSP_FRAMES, self.channels), dtype=np.float32))
            connection.send_bytes(b"T")
        else:
            raise ValueError("VST3 返回的音频格式无效")


def _report_worker_error(connection, phase, exc):
    if isinstance(exc, _NonEffect):
        connection.send_bytes(b"K" + exc.kind.encode())
        return
    message = str(exc)
    expected = {"仅支持音频效果插件", "插件参数已变化，请重新添加插件",
                "无法将 VST3 窗口关联到 SoundBot"}
    if message in expected:
        connection.send_bytes(b"E" + message.encode())
    else:
        connection.send_bytes(b"D" + f"{phase}: {type(exc).__name__}".encode())


def _show_editor(plugin, connection, dark, owner, dsp):
    from core.vst_window import editor_chrome

    initial = plugin.raw_state
    style = editor_chrome(str(plugin.name), dark, owner)
    ticks = 0
    focus = None
    ready = False
    class EditorEvents:
        # Pedalboard calls this from the native GUI thread.
        def is_set(self):
            nonlocal ticks, ready, focus
            if connection.poll():
                data = connection.recv_bytes(_MAX_MESSAGE)
                if data == b"X":
                    return True
                if data == b"F":
                    if focus is not None:
                        focus()
                else:
                    try:
                        dsp.command(connection, data)
                    except (RuntimeError, ValueError) as exc:
                        _report_worker_error(connection, "live audio", exc)
            ticks += 1
            if ticks > 1 and not ready:
                focus = style() if style is not None else None
                if style is None or focus is not None:
                    connection.send_bytes(b"R")
                    ready = True
            return False

    try:
        plugin.show_editor(EditorEvents())
    except RuntimeError as exc:
        if str(exc) not in {"Plugin has no available editor UI.", "Failed to create plugin editor UI."}:
            raise
        connection.send_bytes(b"N")
        return
    final = plugin.raw_state
    if len(final) > _MAX_STATE:
        connection.send_bytes(b"L")
    elif final != initial:
        connection.send_bytes(b"S" + final)
    else:
        connection.send_bytes(b"S")


def _worker(connection, path, rate, channels, values, state, editor, dark, owner, effect_name):
    dll_directory = None
    phase = "host initialization"
    try:
        binary = _plugin_binary(path)
        # Keep dependent Windows DLLs discoverable for the lifetime of the worker.
        dll_directory = os.add_dll_directory(str(binary.parent)) if os.name == "nt" else None
        # Pedalboard's Windows scanner expects the module file. Retain the
        # original bundle path in the registry and use the module only to load.
        module = str(binary) if os.name == "nt" else path

        phase = "effect loading"
        plugin = _load_effect(module, effect_name)
        if state is not None:
            plugin.raw_state = state
        dsp = _PluginAudio(plugin)
        if rate:
            dsp.begin(rate, channels, values or {})
        else:
            _set_values(plugin, values)
            dsp.values = values or {}
        description = {
            "name": str(plugin.name)[:200],
            "vendor": str(plugin.manufacturer_name)[:200],
            "types": plugin_catalog.categories(plugin.category)[1],
            "parameters": [] if rate else [
                {"key": key, "name": str(param.name)[:120], "value": float(param.raw_value)}
                for key, param in islice(_raw_parameters(plugin).items(), 128) if np.isfinite(param.raw_value)
            ],
        }
        connection.send_bytes(b"J" + json.dumps(description, allow_nan=False).encode())
        if editor:
            phase = "editor"
            _show_editor(plugin, connection, dark, owner, dsp)
        elif not rate:
            return
        # A preview already attached to the processor can finish after the
        # editor closes. The parent closes the pipe as soon as its lease ends.
        phase = "audio processing"
        while data := connection.recv_bytes(_MAX_MESSAGE):
            # A focus/close request can have been queued during native close.
            if data not in {b"F", b"X"}:
                dsp.command(connection, data)
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            _report_worker_error(connection, phase, exc)
        except OSError:
            pass
    finally:
        connection.close()
        if dll_directory is not None:
            dll_directory.close()


def worker_entry(args):
    if len(args) != 1 or not args[0].isascii() or not args[0].isdecimal():
        return 2
    from multiprocessing import connection

    handle = int(args[0])
    if os.name == "nt":
        os.set_handle_inheritable(handle, False)
        endpoint = connection.PipeConnection(handle)
    else:
        endpoint = connection.Connection(handle)
    with endpoint:
        request = json.loads(endpoint.recv_bytes(_MAX_MESSAGE))
        state = endpoint.recv_bytes(_MAX_STATE + 1)
        if state[:1] != b"S":
            return 2
        _worker(endpoint, state=state[1:] or None, **request)
    return 0


def _start_worker(endpoint):
    handle = endpoint.fileno()
    command = [sys.executable]
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            executable = Path(sys.executable)
            helper = executable.with_name("soundbot-vst-worker")
            if helper.is_file():
                command[0] = str(helper)
            elif any(parent.suffix == ".app" for parent in executable.parents):
                raise ValueError("VST3 宿主组件不可用")
    else:
        command.append(str(Path(__file__).resolve().parents[1] / "main.py"))
    command.extend(("--vst-worker", str(handle)))
    # Remove credentials before any worker code runs. Preserve the Windows
    # runtime paths and PyInstaller's shared-runtime environment.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("SOUNDBOT_")
           and not any(word in key.upper() for word in
                       ("KEY", "TOKEN", "SECRET", "PASSWORD", "PROXY", "COOKIE", "AUTHORIZATION"))}
    options = dict(env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    if os.name != "nt":
        return subprocess.Popen(command, pass_fds=(handle,), **options)
    startup = subprocess.STARTUPINFO(lpAttributeList={"handle_list": [handle]})
    # Windows requires handles in the explicit list to be inheritable during
    # CreateProcess. Serialize worker starts and restore that bit immediately.
    with _WORKER_START_LOCK:
        os.set_handle_inheritable(handle, True)
        try:
            return subprocess.Popen(command, startupinfo=startup, close_fds=True,
                                    creationflags=subprocess.CREATE_NO_WINDOW, **options)
        finally:
            os.set_handle_inheritable(handle, False)


def _receive(connection, deadline, max_message=_MAX_MESSAGE):
    try:
        if not connection.poll(deadline):
            raise ValueError("VST3 插件响应超时")
        payload = connection.recv_bytes(max_message)
    except (OSError, EOFError) as exc:
        raise ValueError("VST3 插件进程异常退出") from exc
    if payload[:1] == b"L":
        raise ValueError("插件状态超出保存上限")
    if payload[:1] == b"K":
        raise _NonEffect(payload[1:].decode(errors="replace"))
    if payload[:1] == b"E" and payload[1:]:
        raise ValueError(payload[1:].decode())
    if payload[:1] == b"D":
        _logger.warning("VST3 worker failed during %s", payload[1:].decode(errors="replace")[:200])
    if payload[:1] not in {b"A", b"B", b"J", b"N", b"R", b"S", b"T"}:
        raise ValueError("VST3 插件无法加载或处理音频，请检查兼容性")
    return payload[:1], payload[1:]


@contextmanager
def _host(path, rate=0, channels=0, values=None, *, state=None, editor=False, dark=False, owner=None,
          effect_name=None):
    if not available():
        raise ValueError("VST3 宿主组件不可用")
    slots = _EDITOR_SLOTS if editor else _HOST_SLOTS
    if not slots.acquire(blocking=False):
        raise ValueError("请先关闭一个 VST3 编辑窗口" if editor else "VST3 渲染已占满，请减少同时启用的插件")
    try:
        parent, child = multiprocessing.Pipe()
        with parent, child:
            worker = _start_worker(child)
            try:
                child.close()
                parent.send_bytes(json.dumps(dict(path=str(path), rate=rate, channels=channels,
                                                  values=values or {}, editor=editor, dark=dark, owner=owner,
                                                  effect_name=effect_name)).encode())
                parent.send_bytes(b"S" + (state or b""))
                kind, raw = _receive(parent, 16.0)
                if kind != b"J":
                    raise ValueError("VST3 插件未完成初始化")
                yield parent, json.loads(raw)
            finally:
                parent.close()
                try:
                    worker.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=2)
    finally:
        slots.release()


def register(path, *, candidate=None):
    source = Path(path).resolve(strict=True)
    _plugin_binary(source)
    with _host(source, effect_name=candidate.class_name if candidate else None) as (_, info):
        pass
    return _save_plugin(source, info, candidate)


def _save_plugin(source, info, candidate=None):
    vendor = info.get("vendor") or (candidate.vendor if candidate else "")
    types = (candidate.types if candidate else ()) or info.get("types", ())
    with get_db_manager().get_cursor() as cursor:
        initialize(cursor)
        legacy = cursor.execute("SELECT id FROM vst_plugins WHERE path=? AND name=?",
                                (str(source), info["name"])).fetchone()
        row = cursor.execute("""INSERT INTO vst_plugin_classes(id,path,name,vendor,types,parameters)
            VALUES(?,?,?,?,?,?) ON CONFLICT(path,name) DO UPDATE SET
            vendor=excluded.vendor,types=excluded.types,parameters=excluded.parameters RETURNING id""",
            (legacy["id"] if legacy else uuid4().hex, str(source), info["name"], vendor,
             json.dumps(types), json.dumps(info["parameters"]))).fetchone()
    return {**info, "id": row["id"], "vendor": vendor, "types": types}


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
            known = {}
            for row in cursor.execute("""SELECT path,name,'' AS vendor,'[]' AS types FROM vst_plugins
                    UNION ALL SELECT path,name,vendor,types FROM vst_plugin_classes"""):
                known.setdefault(Path(row["path"]), {})[row["name"]] = row
        units = plugin_catalog.audio_unit_metadata()
        plugins = {}
        excluded = {}

        def add(entry):
            identifier = hashlib.sha256(os.fsencode(os.path.normcase(entry.path)) + b"\0"
                                        + (entry.class_name or "").encode()).hexdigest()
            kind = _non_effects.get((identifier, entry.path.stat().st_mtime_ns), entry.kind)
            if kind in _NON_EFFECT_TYPES:
                excluded[identifier] = {"name": entry.name, "vendor": entry.vendor, "kind": kind}
                return
            plugins[identifier] = entry

        candidates = chain(_installed_packages(), known)
        seen = set()
        for path in candidates:
            try:
                source = path.resolve(strict=True)
                if source in seen or source.suffix.lower() != ".vst3":
                    continue
                seen.add(source)
                if sys.platform == "darwin" and not (
                    source.is_dir() and (source / "Contents/MacOS").is_dir()
                ):
                    continue
                binary = _plugin_binary(source)
                if not (source.is_file() or source.is_dir()):
                    continue
                for entry in plugin_catalog.vst3_entries(source, binary, units):
                    registered = known.get(source, {})
                    if entry.kind == "unknown" and entry.class_name is None and registered:
                        for name, row in registered.items():
                            add(replace(entry, name=name, class_name=name, vendor=row["vendor"] or entry.vendor,
                                        kind="effect", types=tuple(json.loads(row["types"]))))
                    else:
                        row = registered.get(entry.class_name) if entry.class_name else next(iter(registered.values()), None)
                        if row and entry.kind not in _NON_EFFECT_TYPES:
                            entry = replace(entry, kind="effect", vendor=entry.vendor or row["vendor"],
                                            types=entry.types or tuple(json.loads(row["types"])))
                        add(entry)
            except (OSError, RuntimeError, ValueError):
                continue
        _candidates.clear()
        _candidates.update(plugins)
        return {
            "available": True,
            "plugins": [
                {"id": key, "name": plugin.name, "vendor": plugin.vendor, "kind": plugin.kind,
                 "types": plugin.types}
                for key, plugin in sorted(plugins.items(), key=lambda item: (item[1].name.casefold(), item[0]))
            ],
            "excluded": sorted(excluded.values(), key=lambda item: item["name"].casefold()),
        }


def load_discovered(plugin_id):
    candidate = _candidate(plugin_id)
    try:
        return register(candidate.path, candidate=candidate)
    except _NonEffect as exc:
        _remember_non_effect(plugin_id, candidate.path, exc.kind)
        raise


def _candidate(plugin_id):
    with _catalog_lock:
        candidate = _candidates.get(plugin_id)
    if candidate is None:
        raise ValueError("插件列表已变化，请重新扫描 VST3")
    if candidate.path.resolve(strict=True) != candidate.path:
        raise ValueError("插件路径已变化，请重新扫描 VST3")
    return candidate


def _remember_non_effect(plugin_id, path, kind):
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        return
    with _catalog_lock:
        _candidates.pop(plugin_id, None)
        _non_effects[(plugin_id, modified)] = kind
        if len(_non_effects) > 512:
            _non_effects.popitem(last=False)


def _registered_plugin(plugin_id):
    with get_db_manager().get_cursor() as cursor:
        initialize(cursor)
        item = cursor.execute("""SELECT path,name FROM vst_plugin_classes WHERE id=?
            UNION ALL SELECT path,name FROM vst_plugins WHERE id=? LIMIT 1""", (plugin_id, plugin_id)).fetchone()
    if not item:
        raise ValueError("所用 VST3 插件已移除")
    return item["path"], item["name"]


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
        for editor in _editors.values():
            editor.close_requested.set()


def close_editor(instance):
    with _editor_lock:
        if editor := _editors.get(instance):
            editor.close_requested.set()
            return True
    return False


def editor_status(instance):
    with _editor_lock:
        editor = _editors.get(instance)
        ready = bool(editor and editor.ready.is_set() and not editor.finished)
        return {"ready": ready, "plugin": editor.registered if ready else None}


def focus_editor(instance):
    with _editor_lock:
        if editor := _editors.get(instance):
            editor.focus_requested.set()
            return True
    return False


def _editor_state(plugin_id, instance, fallback):
    if instance is None:
        return fallback
    with _editor_lock:
        current = _editor_states.get(instance)
        return current[1] if current and current[0] == plugin_id else fallback


class _EditorSession:
    def __init__(self, plugin_id):
        self.plugin_id = plugin_id
        self.registered = None
        self.connection = None
        self.ready = threading.Event()
        self.close_requested = threading.Event()
        self.focus_requested = threading.Event()
        self.io = threading.Lock()
        self.lease = threading.Lock()
        self.streaming = False
        self.finished = None
        self.host = None

    def notice(self, kind, data):
        if kind == b"R":
            self.ready.set()
        elif kind in {b"N", b"S"}:
            self.finished = kind, data
        else:
            raise ValueError("无法读取 VST3 编辑器状态")

    def exchange(self, payload, expected):
        with self.io:
            try:
                self.connection.send_bytes(payload)
                deadline = time.monotonic() + _RESPONSE_TIMEOUT
                while True:
                    kind, raw = _receive(self.connection, max(0, deadline - time.monotonic()), _MAX_STATE + 1)
                    if kind == expected:
                        return raw
                    self.notice(kind, raw)
            except (OSError, EOFError) as exc:
                raise ValueError("VST3 插件进程异常退出") from exc


@contextmanager
def _live_editor(plugin_id, instance):
    with _editor_lock:
        editor = _editors.get(instance)
        if editor and (editor.plugin_id != plugin_id or not editor.ready.is_set() or editor.finished):
            editor = None
    if editor is None:
        yield None
        return
    # A seek or a new source can arrive while the cancelled stream unwinds.
    # Give it time to release this rack slot instead of routing audio to a copy.
    if not editor.lease.acquire(timeout=2.0):
        raise ValueError("VST3 插件响应超时")
    try:
        with editor.io:
            live = editor if not editor.finished and not editor.close_requested.is_set() else None
            editor.streaming = live is not None
        try:
            yield live
        finally:
            with editor.io:
                editor.streaming = False
                host, editor.host = editor.host, None
            if host:
                host.close()
    finally:
        editor.lease.release()


def edit(plugin_id, token=None, values=None, *, instance=None, dark=False, owner=None, candidate=False):
    selection = _candidate(plugin_id) if candidate else None
    path, name = (selection.path, selection.class_name) if selection else _registered_plugin(plugin_id)
    state = None if candidate else _decode_state(plugin_id, token)
    editor = _EditorSession(plugin_id)
    instance = instance or uuid4().hex
    with _editor_lock:
        if _editors_closing or instance in _editors:
            raise ValueError("无法打开 VST3 编辑窗口")
        _editors[instance] = editor

    try:
        with ExitStack() as host:
            connection, info = host.enter_context(_host(path, values=values, state=state, editor=True,
                                                        dark=dark, owner=owner, effect_name=name))
            registered = _save_plugin(path, info, selection) if selection else None
            editor.connection = connection
            editor.registered = registered
            editor.plugin_id = plugin_id = registered["id"] if registered else plugin_id
            close_deadline = None
            open_deadline = time.monotonic() + 12
            while True:
                with editor.io:
                    if editor.finished:
                        kind, updated = editor.finished
                        if kind == b"N":
                            return {"plugin": registered, "editor_unavailable": True}
                        if editor.streaming:
                            editor.host = host.pop_all()
                        break
                    now = time.monotonic()
                    if editor.close_requested.is_set() and close_deadline is None:
                        connection.send_bytes(b"X")
                        close_deadline = now + _RESPONSE_TIMEOUT
                    elif editor.focus_requested.is_set() and editor.ready.is_set() and close_deadline is None:
                        editor.focus_requested.clear()
                        connection.send_bytes(b"F")
                    if ((close_deadline is not None and now >= close_deadline)
                            or (not editor.ready.is_set() and now >= open_deadline)):
                        raise ValueError("VST3 插件响应超时")
                    if connection.poll():
                        editor.notice(*_receive(connection, 0, _MAX_STATE + 1))
                        continue
                time.sleep(0.02)
        if updated:
            with _editor_lock:
                _editor_states[instance] = (plugin_id, updated)
                _editor_states.move_to_end(instance)
                if len(_editor_states) > 16:
                    _editor_states.popitem(last=False)
        return {"changed": bool(updated), "state": _encode_state(plugin_id, updated) if updated else token,
                "plugin": registered}
    except _NonEffect as exc:
        if selection:
            _remember_non_effect(plugin_id, path, exc.kind)
        raise
    finally:
        with _editor_lock:
            _editors.pop(instance, None)


def _render_blocks(blocks, channels, render):
    def chunks():
        carry = None
        for block in blocks:
            if carry is not None:
                block = np.concatenate((carry, block))
                carry = None
            for offset in range(0, len(block), _AUDIO_FRAMES):
                chunk = block[offset:offset + _AUDIO_FRAMES]
                if len(chunk) >= 3:
                    yield len(chunk), chunk
                else:
                    carry = chunk
        if carry is not None:
            yield len(carry), np.pad(carry, ((0, 3 - len(carry)), (0, 0)))

    expected = delivered = 0
    for frames, chunk in chunks():
        expected += frames
        if expected - delivered > 64 * 4096:
            raise ValueError("VST3 未返回完整的渲染音频")
        audio = render(chunk)
        if len(audio) > expected - delivered + len(chunk) - frames:
            raise ValueError("VST3 返回的音频长度无效")
        audio = audio[:expected - delivered]
        delivered += len(audio)
        if len(audio):
            yield audio
    silence = np.zeros((4096, channels), dtype=np.float32)
    for _ in range(64):
        if delivered == expected:
            return
        audio = render(silence)[:expected - delivered]
        delivered += len(audio)
        if len(audio):
            yield audio
    if delivered != expected:
        raise ValueError("VST3 未返回完整的渲染音频")


@contextmanager
def _audio_connection(rate, channels, plugin_id, values, baseline, instance):
    with _live_editor(plugin_id, instance) as editor:
        if editor:
            setup = json.dumps(dict(rate=rate, channels=channels, values=values or {})).encode()
            editor.exchange(b"B" + setup, b"B")
            try:
                yield editor.exchange
            except GeneratorExit:
                pass
            # Feed silence on pause so an idle editor can clear its meter.
            if not editor.finished:
                try:
                    editor.exchange(b"T", b"T")
                except (OSError, ValueError):
                    pass
            return

        state = _editor_state(plugin_id, instance, baseline)
        path, name = _registered_plugin(plugin_id)
        with _host(path, rate, channels, None if state is not baseline else values, state=state,
                   effect_name=name) as (connection, _):
            def exchange(payload, expected):
                connection.send_bytes(payload)
                kind, raw = _receive(connection, _RESPONSE_TIMEOUT)
                if kind != expected:
                    raise ValueError("VST3 返回的音频格式无效")
                return raw

            yield exchange


def _process(blocks, rate, channels, plugin_id, values, token, instance=None):
    if channels not in {1, 2}:
        raise ValueError("VST3 效果链支持单声道与立体声")
    baseline = _decode_state(plugin_id, token)
    with _audio_connection(rate, channels, plugin_id, values, baseline, instance) as exchange:
        def render(block):
            raw = exchange(b"A" + np.ascontiguousarray(block, dtype=np.float32).tobytes(), b"A")
            if len(raw) % (4 * channels):
                raise ValueError("VST3 返回的音频格式无效")
            audio = np.frombuffer(raw, dtype=np.float32).reshape(-1, channels)
            if not np.isfinite(audio).all():
                raise ValueError("VST3 返回无效采样，请旁通该插件")
            return audio

        yield from _render_blocks(blocks, channels, render)



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
