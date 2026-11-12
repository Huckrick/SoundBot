"""Local VST3 registry and crash-isolated offline plugin processing."""

from __future__ import annotations

import importlib.util
import json
import multiprocessing
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import numpy as np

from core.database import get_db_manager


_HOST_SLOTS = threading.BoundedSemaphore(8)
_MAX_MESSAGE = 512 * 1024


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


def _worker(connection, path, rate, channels, values):
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


def _receive(connection, deadline):
    if not connection.poll(deadline):
        raise ValueError("VST3 插件响应超时")
    try:
        payload = connection.recv_bytes(_MAX_MESSAGE)
    except (OSError, EOFError) as exc:
        raise ValueError("VST3 插件进程异常退出") from exc
    if payload[:1] not in {b"A", b"J"}:
        raise ValueError("VST3 插件无法加载或处理音频，请检查兼容性")
    return payload[:1], payload[1:]


@contextmanager
def _host(path, rate=0, channels=0, values=None):
    if not available():
        raise ValueError("VST3 宿主组件不可用")
    if not _HOST_SLOTS.acquire(blocking=False):
        raise ValueError("VST3 渲染已占满，请减少同时启用的插件")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    worker = context.Process(target=_worker, args=(child, str(path), rate, channels, values or {}), daemon=True)
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
        _HOST_SLOTS.release()


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


def list_plugins():
    with get_db_manager().get_cursor() as cursor:
        initialize(cursor)
        return [{"id": row["id"], "name": row["name"], "parameters": json.loads(row["parameters"])}
                for row in cursor.execute("SELECT id,name,parameters FROM vst_plugins ORDER BY name COLLATE NOCASE")]


def unregister(plugin_id):
    with get_db_manager().get_cursor() as cursor:
        initialize(cursor)
        cursor.execute("DELETE FROM vst_plugins WHERE id=?", (plugin_id,))


def process(blocks, rate, channels, plugin_id, values):
    if channels not in {1, 2}:
        raise ValueError("VST3 效果链支持单声道与立体声")
    with get_db_manager().get_cursor() as cursor:
        initialize(cursor)
        item = cursor.execute("SELECT path FROM vst_plugins WHERE id=?", (plugin_id,)).fetchone()
    if not item:
        raise ValueError("所用 VST3 插件已移除")
    with _host(item["path"], rate, channels, values) as (connection, _):
        def render(block):
            connection.send_bytes(np.ascontiguousarray(block, dtype=np.float32).tobytes())
            kind, raw = _receive(connection, 8.0)
            if kind != b"A" or len(raw) % (4 * channels):
                raise ValueError("VST3 返回的音频格式无效")
            return np.frombuffer(raw, dtype=np.float32).reshape(-1, channels)

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
