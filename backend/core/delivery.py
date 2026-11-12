"""Register render files and publish audio without invalidating host references."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import re
import shutil
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import config
from core.database import get_db_manager
from core.editing import write_wave

_PUBLISH_LOCK = threading.Lock()

def initialize(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS render_files (
        path TEXT PRIMARY KEY, state TEXT NOT NULL,
        target TEXT, before_hash TEXT, after_hash TEXT, backup TEXT)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS wwise_backups (
        id TEXT PRIMARY KEY, target TEXT NOT NULL, backup TEXT NOT NULL,
        before_hash TEXT NOT NULL, after_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS daw_deliveries (
        directory TEXT NOT NULL, digest TEXT NOT NULL, path TEXT NOT NULL,
        PRIMARY KEY (directory, digest))""")


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _sync_file(path):
    with Path(path).open("rb") as handle:
        os.fsync(handle.fileno())


def _sync_directory(path):
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


@contextmanager
def staging(directory, extension="wav"):
    path = directory / f".soundbot-{uuid4().hex}.{extension}.part"
    db = get_db_manager()
    with db.get_cursor() as cur:
        initialize(cur)
        cur.execute("INSERT INTO render_files(path,state) VALUES (?,'active')", (str(path),))
    try:
        with path.open("xb"):
            pass
        yield path
    finally:
        with db.get_cursor() as cur:
            row = cur.execute("SELECT * FROM render_files WHERE path=?", (str(path),)).fetchone()
            if row and row["state"] != "replacing":
                if not _cleanup_row(row):
                    cur.execute("UPDATE render_files SET state='orphan' WHERE path=?", (str(path),))
                else:
                    cur.execute("DELETE FROM render_files WHERE path=?", (str(path),))


def _install_new(stage, target):
    if os.name == "nt":
        os.rename(stage, target)
    elif sys.platform == "darwin":
        rename = ctypes.CDLL(None, use_errno=True).renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(os.fsencode(stage), os.fsencode(target), 0x00000004):
            code = ctypes.get_errno()
            if code in {errno.ENOTSUP, errno.ENOSYS, errno.EINVAL}:
                os.link(stage, target)
            else:
                raise OSError(code, os.strerror(code), str(target))
    else:
        os.link(stage, target)


def publish_daw(directory, name, tracks, rate, channels, duration, bits):
    return publish_wave(directory, name, tracks, rate, channels, duration, bits)


def publish_wave(directory, name, tracks, rate, channels, duration, bits, *, kind="clip", validate=None):
    directory = Path(directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("DAW 媒体目录不可用" if kind == "clip" else "请选择有效的工程素材目录")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(". ")[:80] or "SoundBot"
    if validate is not None:
        validate()
    with staging(directory) as stage:
        write_wave(stage, tracks, rate, channels, duration, bits)
        _sync_file(stage)
        digest = sha256(stage)
        with _PUBLISH_LOCK, get_db_manager().get_cursor() as cur:
            if validate is not None:
                validate()
            previous = cur.execute("SELECT path FROM daw_deliveries WHERE directory=? AND digest=?",
                                   (str(directory), digest)).fetchone()
            if previous:
                path = Path(previous["path"])
                if path.parent == directory and path.is_file() and not path.is_symlink() and sha256(path) == digest:
                    return path
            for suffix in (digest[:12], digest[:24], uuid4().hex):
                target = directory / f"{stem}_{kind}_{suffix}.wav"
                try:
                    _install_new(stage, target)
                except FileExistsError:
                    if not target.is_file() or target.is_symlink() or sha256(target) != digest:
                        continue
                _sync_directory(directory)
                cur.execute("""INSERT INTO daw_deliveries(directory,digest,path) VALUES(?,?,?)
                    ON CONFLICT(directory,digest) DO UPDATE SET path=excluded.path""",
                    (str(directory), digest, str(target)))
                return target
    raise ValueError("无法分配 DAW 成品文件名" if kind == "clip" else "无法分配中间件成品文件名")


def backup_and_replace(stage, target, before_hash):
    db = get_db_manager()
    target = Path(target)
    mode = stat.S_IMODE(target.stat().st_mode)
    root = config.get_user_data_dir() / "wwise-backups"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_id = uuid4().hex
    backup = root / f"{backup_id}.wav"
    after_hash = sha256(stage)
    with db.get_cursor() as cur:
        cur.execute("UPDATE render_files SET backup=? WHERE path=?", (str(backup), str(stage)))
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle, target.open("rb") as source:
        shutil.copyfileobj(source, handle)
        handle.flush()
        os.fsync(handle.fileno())
    if sha256(backup) != before_hash or sha256(target) != before_hash:
        backup.unlink(missing_ok=True)
        raise ValueError("源文件在渲染期间发生变化")
    _sync_directory(root)
    os.chmod(stage, mode)
    _sync_file(stage)
    if sha256(target) != before_hash:
        raise ValueError("源文件在渲染期间发生变化")
    with db.get_cursor() as cur:
        cur.execute("""UPDATE render_files SET state='replacing',target=?,before_hash=?,after_hash=?,backup=?
            WHERE path=?""", (str(target), before_hash, after_hash, str(backup), str(stage)))
    os.replace(stage, target)
    _sync_directory(target.parent)
    return backup_id, backup, after_hash


def finalize_replacement(stage, target, before_hash, result):
    backup_id, backup, after_hash = result
    with get_db_manager().get_cursor() as cur:
        cur.execute("""INSERT INTO wwise_backups(id,target,backup,before_hash,after_hash)
            VALUES (?,?,?,?,?)""", (backup_id, str(target), str(backup), before_hash, after_hash))
        cur.execute("DELETE FROM render_files WHERE path=?", (str(stage),))


def _restore_bytes(target, backup, expected):
    if sha256(target) != expected:
        raise ValueError("目标文件已被其他程序修改，已保留备份")
    with staging(Path(target).parent) as stage:
        shutil.copyfile(backup, stage)
        os.chmod(stage, stat.S_IMODE(Path(target).stat().st_mode))
        _sync_file(stage)
        if sha256(target) != expected:
            raise ValueError("目标文件已被其他程序修改，已保留备份")
        os.replace(stage, target)
        _sync_directory(Path(target).parent)


def rollback_replacement(stage, target, result):
    _, backup, after_hash = result
    _restore_bytes(target, backup, after_hash)
    with get_db_manager().get_cursor() as cur:
        cur.execute("DELETE FROM render_files WHERE path=?", (str(stage),))
    backup.unlink(missing_ok=True)


def _cleanup_row(row):
    path = Path(row["path"])
    if not re.fullmatch(r"\.soundbot-[a-f0-9]{32}\.(?:wav|f32)\.part", path.name):
        return False
    try:
        if row["state"] == "replacing":
            target = Path(row["target"])
            if target.is_symlink():
                return False
            actual = sha256(target)
            if actual == row["after_hash"]:
                _restore_bytes(target, row["backup"], row["after_hash"])
            elif actual != row["before_hash"]:
                return False
        if row["backup"]:
            backup = Path(row["backup"])
            if (backup.parent != config.get_user_data_dir() / "wwise-backups"
                or not re.fullmatch(r"[a-f0-9]{32}\.wav", backup.name)):
                return False
            backup.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        return True
    except (OSError, ValueError):
        return False


def recover_managed_files():
    db = get_db_manager()
    with db.get_cursor() as cur:
        cur.execute("UPDATE render_files SET state='orphan' WHERE state='active'")
        rows = cur.execute("SELECT * FROM render_files WHERE state IN ('orphan','replacing')").fetchall()
    for row in rows:
        if _cleanup_row(row):
            with db.get_cursor() as cur:
                cur.execute("DELETE FROM render_files WHERE path=?", (row["path"],))


def cleanup(*, execute=False, paths=None):
    from core.audio_service import get_audio_service

    db = get_db_manager()
    with db.get_cursor() as cur:
        rows = cur.execute("SELECT * FROM render_files WHERE state='orphan'").fetchall()
    files = []
    for row in rows:
        path = Path(row["path"])
        try:
            size = path.lstat().st_size
        except OSError:
            size = 0
        removed = execute and (paths is None or str(path) in paths) and _cleanup_row(row)
        if removed:
            with db.get_cursor() as cur:
                cur.execute("DELETE FROM render_files WHERE path=? AND state='orphan'", (str(path),))
        files.append({"path": str(path), "bytes": size, "reason": "未完成的音频暂存",
                      "removed": removed, "eligible": True})
    files.extend(get_audio_service().cleanup_playback_cache(execute=execute, paths=paths))
    with db.get_cursor() as cur:
        backups = [dict(row) for row in cur.execute(
            "SELECT id,target,created_at FROM wwise_backups ORDER BY created_at DESC LIMIT 200")]
        recovery = [dict(row) for row in cur.execute(
            "SELECT target,backup FROM render_files WHERE state='replacing'")]
    return {"files": files, "backups": backups, "recovery": recovery}


def restore_backup(backup_id):
    db = get_db_manager()
    with db.get_cursor() as cur:
        row = cur.execute("SELECT * FROM wwise_backups WHERE id=?", (backup_id,)).fetchone()
    if not row:
        raise ValueError("备份不存在")
    if sha256(row["backup"]) != row["before_hash"]:
        raise ValueError("备份校验失败")
    if Path(row["target"]).is_symlink():
        raise ValueError("目标路径已改变")
    _restore_bytes(row["target"], row["backup"], row["after_hash"])
