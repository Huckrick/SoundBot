"""One active middleware project. Folder hosts never change project metadata or sources."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from core import delivery, wwise
from core.audio_service import get_audio_service

Host = Literal["wwise", "fmod", "cri"]
_FOLDER_FORMATS = {
    "fmod": (".fspro", frozenset({".wav", ".aif", ".aiff", ".mp3", ".ogg", ".wma", ".flac"})),
    "cri": (".atmcproject", frozenset({".wav", ".aif", ".aiff"})),
}
MAX_SOURCES = 20_000
MAX_ENTRIES = 100_000
_PROJECT_CHANGED = "中间件工程或素材目录已改变，请刷新工程"
_SOURCE_CHANGED = "源文件已改变，请重新选择"
_LOCK = asyncio.Lock()


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino


def _fingerprint(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(frozen=True)
class FolderProject:
    host: Host
    project: Path
    directory: Path
    project_identity: tuple[int, int]
    directory_identity: tuple[int, int]
    sources: dict[str, Path]
    truncated: bool

    @property
    def public(self) -> dict:
        return {"id": str(self.project), "path": str(self.project), "name": self.project.stem}

    def check(self) -> None:
        try:
            if (self.project.resolve(strict=True) == self.project
                    and self.directory.resolve(strict=True) == self.directory
                    and self.project.is_file() and self.directory.is_dir()
                    and _identity(self.project) == self.project_identity
                    and _identity(self.directory) == self.directory_identity):
                return
        except OSError:
            pass
        raise ValueError(_PROJECT_CHANGED)

    def resolve_source(self, source_id: str) -> Path:
        self.check()
        path = self.sources.get(source_id)
        if path is not None:
            try:
                if path.is_file() and path.resolve(strict=True) == path:
                    return path
            except OSError:
                pass
        raise ValueError(_SOURCE_CHANGED)


@dataclass(frozen=True)
class FolderSelection:
    project: FolderProject
    source_id: str
    target: Path
    fingerprint: tuple[int, ...]
    rate: int
    channels: int
    bits: int
    duration: float

    def check(self) -> None:
        path = self.project.resolve_source(self.source_id)
        try:
            if path == self.target and _fingerprint(path) == self.fingerprint:
                return
        except OSError:
            pass
        raise ValueError(_SOURCE_CHANGED)


_host: Host | None = None
_project: FolderProject | None = None
_selected: tuple[str, FolderSelection] | None = None


def _load_folder(host: Host, project_path: str, source_directory: str) -> FolderProject:
    suffix, formats = _FOLDER_FORMATS[host]
    project, directory = Path(project_path), Path(source_directory)
    if not project.is_absolute() or project.suffix.lower() != suffix or not project.is_file():
        raise ValueError("请选择有效的中间件工程文件")
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("请选择有效的工程素材目录")
    project = project.resolve(strict=True)
    directory = directory.resolve(strict=True)
    if project.suffix.lower() != suffix or directory == Path(directory.anchor):
        raise ValueError("请选择有效的工程素材目录")
    project_identity, directory_identity = _identity(project), _identity(directory)
    paths: list[Path] = []
    stack = [directory]
    seen = 0
    truncated = False
    while stack and not truncated:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                seen += 1
                if seen > MAX_ENTRIES or len(paths) >= MAX_SOURCES:
                    truncated = True
                    break
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                candidate = Path(entry.path)
                if candidate.is_junction():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if candidate.resolve(strict=True) == candidate:
                        stack.append(candidate)
                elif candidate.suffix.lower() in formats and entry.is_file(follow_symlinks=False):
                    paths.append(candidate)
    paths.sort(key=lambda path: (path.relative_to(directory).as_posix().casefold(), str(path)))
    session = FolderProject(host, project, directory, project_identity, directory_identity,
                            {uuid4().hex: path for path in paths}, truncated)
    session.check()
    return session


async def connect(host: Host, *, url: str = "", project_path: str = "", source_directory: str = "") -> dict:
    global _host, _project, _selected
    async with _LOCK:
        if host == "wwise":
            result = await wwise.connect(url)
            _project = None
            result.update(host=host, delivery="replace")
        else:
            project = await asyncio.to_thread(_load_folder, host, project_path, source_directory)
            result = {
                "host": host, "delivery": "new_wave", "project": project.public, "truncated": project.truncated,
                "sources": [{"id": id_, "name": path.name, "path": path.relative_to(project.directory).as_posix()}
                            for id_, path in project.sources.items()],
            }
            await wwise.disconnect()
            _project = project
        _host, _selected = host, None
        return result


async def disconnect() -> None:
    global _host, _project, _selected
    async with _LOCK:
        await wwise.disconnect()
        _host = _project = _selected = None


def _open_selection(project: FolderProject, source_id: str) -> FolderSelection:
    path = project.resolve_source(source_id)
    before = _fingerprint(path)
    info = get_audio_service().probe(path)
    if not info.duration or not 0 < info.duration <= 86400 or info.channels not in {1, 2}:
        raise ValueError("请选择可读取的单声道或立体声音频源")
    item = FolderSelection(project, source_id, path, before, 48000, info.channels, 24, info.duration)
    item.check()
    return item


async def select(source_id: str) -> dict:
    global _selected
    async with _LOCK:
        if _host == "wwise":
            return {**await wwise.select(source_id), "host": "wwise", "delivery": "replace"}
        if _project is None:
            raise ValueError("请先打开中间件工程")
        item = await asyncio.to_thread(_open_selection, _project, source_id)
        token = uuid4().hex
        _selected = token, item
        return {"token": token, "name": item.target.name, "project": item.project.public, "host": item.project.host,
                "delivery": "new_wave", "duration": item.duration, "sample_rate": item.rate,
                "channels": item.channels, "bits": item.bits}


def selection(token: str) -> wwise.Selection | FolderSelection:
    if _host == "wwise":
        return wwise.selection(token)
    chosen = _selected
    if chosen is None or chosen[0] != token:
        raise ValueError("请重新选择中间件音频源")
    item = chosen[1]
    item.check()
    return item


async def deliver(token, tracks, duration) -> dict:
    async with _LOCK:
        item = selection(token)
        if isinstance(item, wwise.Selection):
            return await wwise.writeback(token, tracks, duration)
        path = await asyncio.to_thread(
            delivery.publish_wave, item.project.directory, item.target.stem, tracks,
            item.rate, item.channels, duration, item.bits,
            kind="mix", validate=item.check,
        )
        return {"status": "file_delivered", "path": str(path), "name": path.name, "registration": "pending"}
