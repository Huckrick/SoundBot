"""WAAPI reads and reversible replacement of a selected Wwise original."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import struct
import wave
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from websockets.asyncio.client import connect as open_websocket

from core import delivery
from core.editing import write_wave


@dataclass(frozen=True)
class Selection:
    project: str
    project_id: str
    source_id: str
    parent_id: str
    target: Path
    digest: str
    rate: int
    channels: int
    bits: int
    duration: float


_url: str | None = None
_connected_project: tuple[str, str] | None = None
_selected: tuple[str, Selection] | None = None
_lock = asyncio.Lock()
_SOURCE_FIELDS = ["id", "name", "type", "path", "parent", "originalWavFilePath"]
# Wwise exposes its effective object color as a palette index.
_COLORS = (
    None, '#373ec8', '#1a55cb', '#086868', '#186d18', '#56730d', '#787211',
    '#795815', '#78440d', '#72392c', '#892424', '#7d267d', '#732b97', '#5937ae',
    '#6b6fc2', '#6383c5', '#438989', '#539353', '#80983e', '#a09827', '#ab7a42',
    '#ae7a42', '#ae6656', '#b95b5b', '#aa505b', '#9b56bd', '#8760e2',
)


def _local_url(url):
    parts = urlparse(url)
    if (parts.scheme != "ws" or parts.hostname not in {"127.0.0.1", "::1", "localhost"} or
            parts.path != "/waapi" or not parts.port or parts.username or parts.password or
            parts.query or parts.fragment):
        raise ValueError("请输入本机 WAAPI 地址，例如 ws://127.0.0.1:8080/waapi")
    return f"ws://127.0.0.1:{parts.port}/waapi" if parts.hostname == "localhost" else url


class _WAAPI:
    def __init__(self, socket):
        self.socket = socket
        self.request_id = 0

    async def call(self, method, args=None, fields=None):
        self.request_id += 1
        args = dict(args or {})
        options = {"return": fields} if fields else {}
        await self.socket.send(json.dumps([48, self.request_id, options, method, [], args]))
        result = json.loads(await asyncio.wait_for(self.socket.recv(), 12))
        if result[:2] == [50, self.request_id]:
            return result[4] if len(result) > 4 else (result[3][0] if len(result) > 3 and result[3] else {})
        raise ValueError(f"WAAPI 查询失败 ({method})")

    async def objects(self, waql, fields):
        return (await self.call("ak.wwise.core.object.get", {"waql": waql}, fields)).get("return", [])

    async def project(self):
        projects = await self.objects("$ from type Project", ["id", "filePath", "name"])
        if len(projects) != 1:
            raise ValueError("Wwise 未打开可识别的工程")
        item = projects[0]
        path = Path(item.get("filePath") or "")
        if not path.is_absolute() or path.suffix.lower() != ".wproj" or not path.is_file():
            raise ValueError("无法确认当前 Wwise 工程路径")
        return {"id": item["id"], "path": str(path.resolve()), "name": item.get("name") or path.stem}

    async def sources(self):
        return await self.objects("$ from type AudioFileSource", _SOURCE_FIELDS)

    async def sounds(self, *, colors=False):
        fields = ["id", "path", "@IsVoice"]
        if colors:
            try:
                return await self.objects("$ from type Sound", [*fields, "@@Color"])
            except ValueError:
                pass  # Older authoring versions may not expose the property.
        return await self.objects("$ from type Sound", fields)

    async def explorer_objects(self, sources):
        roots = {source["path"].split("\\")[1] for source in sources if source["path"].startswith("\\")}
        if not roots:
            roots = {"Actor-Mixer Hierarchy", "Containers"}
        fields = ["id", "type", "path"]
        result = []
        for root in sorted(roots):
            # The hierarchy name changed in Wwise 2025.1. The authoring tool
            # returns an empty result (or a query error) for an absent root.
            if '"' in root:
                continue
            query = f'$ from object "\\{root}" select this, descendants'
            for colors in (["@@Color", "@Color"], ["@@Color"], ["@Color"], []):
                try:
                    result.extend(await self.objects(query, fields + colors))
                    break
                except ValueError:
                    continue
        return result


@asynccontextmanager
async def _connection(url):
    try:
        async with open_websocket(url, subprotocols=["wamp.2.json"], proxy=None,
                                  open_timeout=3, close_timeout=1, max_size=32 * 1024 * 1024) as socket:
            await socket.send(json.dumps([1, "realm1", {"roles": {"caller": {}}}]))
            welcome = json.loads(await asyncio.wait_for(socket.recv(), 4))
            if not welcome or welcome[0] != 2:
                raise ValueError("WAAPI 拒绝连接")
            yield _WAAPI(socket)
    except (TimeoutError, OSError) as exc:
        raise ValueError("无法连接 Wwise，请确认已开启本机 WAAPI") from exc
    except Exception as exc:
        from websockets.exceptions import WebSocketException

        if isinstance(exc, WebSocketException):
            raise ValueError("WAAPI 连接已中断") from exc
        raise


async def connect(url):
    global _url, _connected_project, _selected
    async with _lock:
        target_url = _local_url(url)
        async with _connection(target_url) as client:
            project = await client.project()
            rows = await client.sources()
            sounds = {item["id"]: item for item in await client.sounds(colors=True) if item.get("@IsVoice") is False}
            sources = []
            nodes = {}
            for item in rows:
                sound = sounds.get(_parent(item))
                if not sound or not item.get("originalWavFilePath"):
                    continue
                color = _color(sound)
                sources.append({"id": item["id"], "name": item["name"], "path": item["path"],
                                "type": "AudioFileSource", "color": color})
                if sound.get("path"):
                    nodes[sound["path"]] = {"id": sound["id"], "type": "Sound", "color": color}
            visible_types = {"PhysicalFolder", "WorkUnit", "Folder", "ActorMixer", "RandomSequenceContainer",
                             "SwitchContainer", "BlendContainer", "Container"}
            for item in await client.explorer_objects(sources):
                path, kind = item.get("path"), item.get("type")
                if not path or (kind not in visible_types and path not in nodes and path.count("\\") != 1):
                    continue
                previous = nodes.get(path, {})
                nodes[path] = {"id": item.get("id"), "type": kind, "color": _color(item) or previous.get("color")}
        _url = target_url
        _connected_project = project["path"], project["id"]
        _selected = None
        return {"project": project, "sources": sources, "objects": nodes}


def _color(item):
    for key in ("@@Color", "@Color"):
        index = item.get(key)
        if type(index) is int and 0 < index < len(_COLORS):
            return _COLORS[index]
    return None


async def disconnect():
    global _url, _connected_project, _selected
    async with _lock:
        _url = None
        _connected_project = None
        _selected = None


def _parent(item):
    parent = item.get("parent") or {}
    return parent.get("id") if isinstance(parent, dict) else parent


def _resolve_target(project, source):
    original = Path(source.get("originalWavFilePath") or "")
    root = (Path(project["path"]).parent / "Originals").resolve()
    if not original.is_absolute() or original.suffix.lower() != ".wav" or original.is_symlink():
        raise ValueError("请选择工程 Originals 中已有的 WAV 源")
    path = original.resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("无法验证 Wwise Originals 下的目标路径")
    return path


def _inspect(path, *, writing=False):
    file_stat = path.stat()
    if writing and (file_stat.st_nlink != 1 or not file_stat.st_mode & stat.S_IWUSR or not os.access(path, os.W_OK)):
        raise ValueError("Wwise 源不可写或使用共享文件")
    try:
        with wave.open(str(path), "rb") as reader:
            rate, channels, bits = reader.getframerate(), reader.getnchannels(), reader.getsampwidth()*8
            duration = reader.getnframes()/rate
            if channels not in {1, 2} or bits not in {16, 24} or not duration:
                raise ValueError("Wwise 写回支持单声道或立体声 16/24-bit PCM WAV")
        if writing:
            with path.open("rb") as handle:
                handle.seek(12)
                end = file_stat.st_size
                while header := handle.read(8):
                    if len(header) != 8:
                        raise ValueError("目标 WAV 数据块不完整")
                    chunk, size = struct.unpack("<4sI", header)
                    if chunk in {b"cue ", b"smpl", b"plst", b"acid"}:
                        raise ValueError("目标 WAV 包含循环或定位标记")
                    next_chunk = handle.tell() + size
                    if next_chunk > end:
                        raise ValueError("目标 WAV 数据块不完整")
                    handle.seek(next_chunk + (size & 1))
    except (wave.Error, EOFError) as exc:
        raise ValueError("目标 WAV 规格无法验证") from exc
    return rate, channels, bits, duration


async def _binding(client, source_id, *, writing=False):
    project = await client.project()
    rows = await client.sources()
    source = next((row for row in rows if row["id"] == source_id), None)
    if not source:
        raise ValueError("Wwise 源已切换或不存在")
    parent = _parent(source)
    sounds = await client.sounds()
    if not any(row["id"] == parent and row.get("@IsVoice") is False for row in sounds):
        raise ValueError("请选择 Sound SFX 的音频源")
    target = _resolve_target(project, source)
    if writing:
        for other in rows:
            if other["id"] == source_id:
                continue
            other_path = other.get("originalWavFilePath")
            if other_path and os.path.realpath(other_path).casefold() == str(target).casefold():
                raise ValueError("该 WAV 被多个 Wwise 音频源引用")
    return project, source, target


async def select(source_id):
    global _selected
    async with _lock:
        if not _url:
            raise ValueError("请先连接 Wwise")
        async with _connection(_url) as client:
            project, source, target = await _binding(client, source_id)
        if (project["path"], project["id"]) != _connected_project:
            raise ValueError("Wwise 工程已切换，请刷新工程")
        rate, channels, bits, duration = await asyncio.to_thread(_inspect, target)
        digest = await asyncio.to_thread(delivery.sha256, target)
        token = uuid4().hex
        _selected = token, Selection(project["path"], project["id"], source_id, _parent(source), target,
                                     digest, rate, channels, bits, duration)
        return {"token": token, "name": source["name"], "duration": duration,
                "sample_rate": rate, "channels": channels, "bits": bits, "project": project}


def selection(token):
    if not _selected or token != _selected[0]:
        raise ValueError("请重新选择 Wwise 音频源")
    return _selected[1]


async def writeback(token, tracks, duration):
    global _selected
    async with _lock:
        chosen = selection(token)
        if not _url:
            raise ValueError("请先连接 Wwise")
        target = chosen.target
        expected = (chosen.project, chosen.project_id, chosen.parent_id, target)
        async with _connection(_url) as client:
            project, source, resolved = await _binding(client, chosen.source_id, writing=True)
            if (project["path"], project["id"], _parent(source), resolved) != expected:
                raise ValueError("Wwise 工程或源绑定发生变化")
            await asyncio.to_thread(_inspect, target, writing=True)
            if await asyncio.to_thread(delivery.sha256, target) != chosen.digest:
                raise ValueError("源文件已改变，请重新选择")
            with delivery.staging(target.parent) as stage:
                await asyncio.to_thread(write_wave, stage, tracks, chosen.rate, chosen.channels, duration, chosen.bits)
                project, source, resolved = await _binding(client, chosen.source_id, writing=True)
                if (project["path"], project["id"], _parent(source), resolved) != expected:
                    raise ValueError("渲染期间 Wwise 工程或源绑定发生变化")
                result = await asyncio.to_thread(delivery.backup_and_replace, stage, target, chosen.digest)
                try:
                    project, source, resolved = await _binding(client, chosen.source_id, writing=True)
                    if (project["path"], project["id"], _parent(source), resolved) != expected:
                        raise ValueError("写回后未能核对原有源绑定")
                    await asyncio.to_thread(delivery.finalize_replacement, stage, target, chosen.digest, result)
                except BaseException:
                    await asyncio.to_thread(delivery.rollback_replacement, stage, target, result)
                    raise
        _selected = None
        return {"status": "source_updated", "conversion": "unverified", "backup_id": result[0]}


async def restore(backup_id):
    global _selected
    async with _lock:
        await asyncio.to_thread(delivery.restore_backup, backup_id)
        _selected = None
