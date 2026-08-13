# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Nagisa_Huckrick (胡杨)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
# SQLite 数据库管理模块

"""
SQLite 持久化层：存储音频文件元数据和波形峰值数据。

表结构:
- path (TEXT PRIMARY KEY) - 文件唯一路径
- filename (TEXT) - 文件名
- duration (REAL) - 时长（秒）
- sample_rate (INTEGER) - 采样率
- channels (INTEGER) - 声道数
- file_size (INTEGER) - 文件大小（字节）
- peaks_json (TEXT) - 波形峰值 JSON（约2000个点）
- tags (TEXT) - 标签 JSON 数组
- created_at (TEXT) - 创建时间
- updated_at (TEXT) - 更新时间
"""

import sqlite3
import json
import ntpath
import os
import shutil
import threading
import hashlib
import math
import re
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any, Iterable, Tuple
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime

logger = None  # 延迟初始化

SCHEMA_VERSION = 3
ARTIFACT_KINDS = ("waveform", "audio_vector", "text_vector")
ARTIFACT_STATES = ("pending", "processing", "ready", "failed", "stale")
SAFE_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WINDOWS_RESERVED_PROJECT_IDS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def is_safe_project_id(project_id: str) -> bool:
    """Return whether an identifier is safe to use as an application key/path part."""
    value = str(project_id or "").strip()
    windows_stem = value.split(".", 1)[0].upper()
    return bool(
        value not in {"", ".", ".."}
        and not value.endswith(".")
        and windows_stem not in WINDOWS_RESERVED_PROJECT_IDS
        and SAFE_PROJECT_ID_RE.fullmatch(value)
    )


def _is_windows_style_path(path: str) -> bool:
    """Recognize native and foreign Windows drive/UNC paths."""
    raw = os.fspath(path)
    drive, _tail = ntpath.splitdrive(raw)
    return os.name == "nt" or bool(drive) or raw.startswith("\\\\")


def canonicalize_path(path: str) -> str:
    """Create a physical-path lookup key without changing the displayed path.

    POSIX aliases must resolve to the same key (notably macOS ``/tmp`` and
    ``/private/tmp``).  Foreign Windows drive and UNC paths are normalized with
    :mod:`ntpath` so testing or migration on macOS never prepends the POSIX
    working directory to a Windows path.
    """
    raw = os.fspath(path)
    if not raw:
        return ""
    if _is_windows_style_path(raw):
        if os.name == "nt":
            normalized = os.path.realpath(os.path.abspath(os.path.expanduser(raw)))
        else:
            normalized = ntpath.normpath(raw.replace("/", "\\"))
        return ntpath.normcase(normalized).replace("\\", "/")
    expanded = os.path.expanduser(raw)
    return os.path.normcase(os.path.realpath(os.path.abspath(expanded)))


def canonical_path_is_within(path: str, folder_path: str) -> bool:
    """Return whether *path* is *folder_path* or one of its descendants.

    Both operands use the same Windows-aware canonical key as SQLite file
    identity.  The explicit separator boundary avoids the classic
    ``C:/audio/foo`` versus ``C:/audio/foobar`` prefix bug and does not rely on
    SQL ``LIKE`` (where ``%`` and ``_`` have special meaning).
    """
    candidate_key = canonicalize_path(path)
    folder_key = canonicalize_path(folder_path)
    if not candidate_key or not folder_key:
        return False
    if folder_key in {"/", "\\"}:
        return candidate_key.startswith(("/", "\\"))
    candidate = candidate_key.rstrip("/\\")
    folder = folder_key.rstrip("/\\")
    return candidate == folder or candidate.startswith(f"{folder}/")


def make_source_fingerprint(path: str) -> str:
    """Fast invalidation fingerprint; this deliberately is not a content hash."""
    try:
        stat = os.stat(path)
        payload = f"{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        payload = "missing"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_file_uuid(project_id: str, canonical_path: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"soundbot:{project_id}:{canonical_path}"))


def _get_logger():
    """延迟获取 logger，避免循环导入"""
    global logger
    if logger is None:
        from utils.logger import get_logger
        logger = get_logger()
    return logger


# SQL 建表语句
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 工程表
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    temp_dir TEXT,  -- 工程特定的临时文件目录
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    settings_json TEXT DEFAULT '{}'  -- 工程特定配置（JSON格式）
);

-- 插入默认工程
INSERT OR IGNORE INTO projects (id, name, description) VALUES ('default', '默认工程', '系统默认工程');

-- 音频文件表
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_uuid TEXT NOT NULL,
    path TEXT NOT NULL,
    canonical_path TEXT NOT NULL,
    project_id TEXT DEFAULT 'default',
    filename TEXT NOT NULL,
    duration REAL DEFAULT 0,
    sample_rate INTEGER DEFAULT 0,
    channels INTEGER DEFAULT 0,
    file_size INTEGER DEFAULT 0,
    source_fingerprint TEXT,
    peaks_json TEXT,
    waveform_fingerprint TEXT,
    waveform_version INTEGER DEFAULT 0,
    tags TEXT DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, canonical_path),
    UNIQUE(file_uuid),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_canonical_path ON files(project_id, canonical_path);
CREATE INDEX IF NOT EXISTS idx_filename ON files(filename);
CREATE INDEX IF NOT EXISTS idx_created_at ON files(created_at);
CREATE INDEX IF NOT EXISTS idx_duration ON files(duration);

-- 最近工程列表（用于快速切换）
CREATE TABLE IF NOT EXISTS recent_projects (
    project_id TEXT PRIMARY KEY,
    opened_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- 用户自定义文件夹表
CREATE TABLE IF NOT EXISTS user_folders (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    color TEXT DEFAULT '#3b82f6',  -- 文件夹颜色（用于UI显示）
    sort_order INTEGER DEFAULT 0,   -- 排序顺序
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_folders_project ON user_folders(project_id);
CREATE INDEX IF NOT EXISTS idx_user_folders_order ON user_folders(sort_order);

-- 导入文件夹与用户文件夹的关联表（用于分类管理）
CREATE TABLE IF NOT EXISTS imported_folder_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    folder_path TEXT NOT NULL,      -- 导入的文件夹路径
    user_folder_id TEXT,            -- 关联的用户自定义文件夹ID（可为空表示未分类）
    folder_name TEXT,               -- 文件夹显示名称
    file_count INTEGER DEFAULT 0,   -- 文件数量
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (user_folder_id) REFERENCES user_folders(id) ON DELETE SET NULL,
    UNIQUE(project_id, folder_path)
);

CREATE INDEX IF NOT EXISTS idx_imported_mappings_project ON imported_folder_mappings(project_id);
CREATE INDEX IF NOT EXISTS idx_imported_mappings_user_folder ON imported_folder_mappings(user_folder_id);

CREATE TABLE IF NOT EXISTS file_artifacts (
    project_id TEXT NOT NULL,
    file_uuid TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('waveform', 'audio_vector', 'text_vector')),
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK(state IN ('pending', 'processing', 'ready', 'failed', 'stale')),
    source_fingerprint TEXT,
    engine_fingerprint TEXT,
    error_code TEXT,
    error_message TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, file_uuid, kind),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (file_uuid) REFERENCES files(file_uuid) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_file_artifacts_state
ON file_artifacts(project_id, kind, state);

CREATE TABLE IF NOT EXISTS index_manifests (
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('audio_vector', 'text_vector')),
    collection_name TEXT,
    engine_fingerprint TEXT,
    model_id TEXT,
    model_revision TEXT,
    dimensions INTEGER,
    preprocessing_version TEXT,
    metric TEXT NOT NULL DEFAULT 'cosine',
    revision INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, kind),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    stage TEXT,
    processed INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_project_state ON jobs(project_id, state);
"""


def _execute_sql_statements(cursor: sqlite3.Cursor, script: str) -> None:
    """Execute a SQL script without sqlite3.executescript's implicit commit."""
    pending: List[str] = []
    for character in script:
        pending.append(character)
        if character != ";":
            continue
        statement = "".join(pending)
        if not sqlite3.complete_statement(statement):
            continue
        cursor.execute(statement)
        pending.clear()

    remainder = "".join(pending).strip()
    if remainder:
        raise sqlite3.ProgrammingError("incomplete SQL statement in schema definition")


@dataclass
class AudioFileRecord:
    """音频文件数据库记录"""
    path: str
    filename: str
    duration: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    file_size: int = 0
    peaks_json: Optional[str] = None
    tags: str = '[]'
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    file_uuid: Optional[str] = None
    project_id: str = 'default'
    canonical_path: Optional[str] = None
    source_fingerprint: Optional[str] = None
    waveform_fingerprint: Optional[str] = None
    waveform_version: int = 0

    def get_peaks(self) -> Optional[List[float]]:
        """获取波形峰值列表"""
        if self.peaks_json:
            try:
                peaks = json.loads(self.peaks_json)
                if (
                    isinstance(peaks, list)
                    and peaks
                    and all(isinstance(value, (int, float)) and math.isfinite(value) for value in peaks)
                ):
                    return peaks
                return None
            except (json.JSONDecodeError, TypeError, ValueError):
                return None
        return None

    def get_tags(self) -> List[str]:
        """获取标签列表"""
        if self.tags:
            try:
                return json.loads(self.tags)
            except json.JSONDecodeError:
                return []
        return []

    def set_peaks(self, peaks: List[float]):
        """设置波形峰值"""
        self.peaks_json = json.dumps(peaks)

    def set_tags(self, tags: List[str]):
        """设置标签"""
        self.tags = json.dumps(tags)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = asdict(self)
        result['peaks'] = self.get_peaks()
        result['tag_list'] = self.get_tags()
        return result


class DatabaseManager:
    """SQLite 数据库管理器（线程安全）"""

    def __init__(self, db_path: str):
        """
        初始化数据库管理器

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        # A thread-local must belong to this manager instance. A class-level
        # local could reuse a connection from a different project/database.
        self._local = threading.local()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        _get_logger().info(f"DatabaseManager 初始化完成: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """获取线程局部的数据库连接"""
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            try:
                conn = sqlite3.connect(
                    self.db_path,
                    check_same_thread=False,
                    timeout=30.0
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.execute("PRAGMA synchronous=NORMAL")
                self._local.conn = conn
            except sqlite3.Error as e:
                _get_logger().error(f"数据库连接失败: {e}")
                self._repair_database()
                raise
        return conn

    def _repair_database(self):
        """Run a read-only integrity diagnostic; never delete WAL/SHM files."""
        try:
            _get_logger().warning(f"数据库完整性诊断: {self.db_path}")
            diagnostic = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                result = diagnostic.execute("PRAGMA quick_check").fetchone()
                _get_logger().warning(f"数据库 quick_check: {result[0] if result else 'unknown'}")
            finally:
                diagnostic.close()
        except Exception as e:
            _get_logger().error(f"数据库诊断失败: {e}")

    @contextmanager
    def get_cursor(self):
        """获取数据库游标的上下文管理器"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def _init_db(self):
        """Initialize safely, restoring the immutable snapshot on failure."""
        self._migration_backup_in_progress: Optional[str] = None
        try:
            self._init_db_once()
        except Exception as exc:
            backup_path = self._migration_backup_in_progress
            connection = getattr(self._local, "conn", None)
            if connection is not None:
                try:
                    connection.rollback()
                    connection.close()
                finally:
                    self._local.conn = None
            if backup_path and os.path.isfile(backup_path):
                try:
                    shutil.copy2(backup_path, self.db_path)
                    _get_logger().error(
                        "数据库迁移失败，已从只读升级快照恢复原库；备份保留在 %s",
                        backup_path,
                    )
                except Exception as restore_error:
                    _get_logger().critical(
                        "数据库迁移与自动恢复均失败；原始备份仍保留在 %s: %s",
                        backup_path,
                        restore_error,
                    )
            raise RuntimeError("数据库迁移失败，应用已停止以保护用户数据") from exc
        finally:
            self._migration_backup_in_progress = None

    def _init_db_once(self):
        """初始化数据库，并且每个 schema 版本只迁移一次。"""
        with self.get_cursor() as cursor:
            schema_version_pending = False
            # 检查 files 表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
            files_table_exists = cursor.fetchone() is not None

            # 检查 projects 表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
            projects_table_exists = cursor.fetchone() is not None

            if not files_table_exists:
                # 全新数据库，直接创建所有表
                cursor.execute("BEGIN IMMEDIATE")
                _execute_sql_statements(cursor, CREATE_TABLE_SQL)
                integrity = cursor.execute("PRAGMA quick_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise sqlite3.DatabaseError(
                        f"database initialization quick_check failed: {integrity}"
                    )
                schema_version_pending = True
                _get_logger().info("数据库初始化完成：创建新表")
            else:
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_meta'"
                )
                if cursor.fetchone():
                    version_row = cursor.execute(
                        "SELECT value FROM schema_meta WHERE key='schema_version'"
                    ).fetchone()
                    current_version = int(version_row[0]) if version_row else 0
                else:
                    current_version = 0

                if current_version < SCHEMA_VERSION:
                    backup_path = self._create_migration_backup(current_version)
                    self._migration_backup_in_progress = backup_path
                    _get_logger().info(
                        f"数据库从 v{current_version} 迁移到 v{SCHEMA_VERSION}; "
                        f"原始备份保留在 {backup_path}"
                    )
                    if current_version < 2:
                        self._migrate_db_v2(
                            cursor, projects_table_exists, current_version
                        )
                        current_version = 2
                    if current_version < 3:
                        # v2 already owns artifact/index state.  Its path-key
                        # upgrade must be incremental and must not rebuild the
                        # files table or reset ready artifacts.
                        self._migrate_db_v3(cursor)
                        current_version = 3
                    schema_version_pending = True

            # A process can stop between claiming an artifact/job and recording
            # its result.  Recover those durable states on the next startup so
            # repair jobs can safely resume instead of leaving them stuck.
            now = datetime.now().isoformat()
            cursor.execute("""
                UPDATE file_artifacts
                SET state='pending', error_code='interrupted',
                    error_message='Previous process stopped during processing',
                    updated_at=?
                WHERE state='processing'
            """, (now,))
            cursor.execute("""
                UPDATE jobs
                SET state='failed', stage='interrupted',
                    error_code='job_interrupted',
                    error_message='Previous process stopped before the job completed',
                    updated_at=?
                WHERE state IN ('pending', 'running')
            """, (now,))
            if schema_version_pending:
                # This must be the final write in the schema transaction.  A
                # failed migration must never advertise the new version.
                cursor.execute("""
                    INSERT INTO schema_meta(key, value, updated_at)
                    VALUES ('schema_version', ?, ?)
                    ON CONFLICT(key) DO UPDATE
                    SET value=excluded.value, updated_at=excluded.updated_at
                """, (str(SCHEMA_VERSION), now))

    def _create_migration_backup(self, current_version: int) -> str:
        """Create one immutable SQLite snapshot for a schema transition."""
        backup_path = f"{self.db_path}.pre-v{current_version}-to-v{SCHEMA_VERSION}.bak"
        if os.path.exists(backup_path):
            return backup_path
        destination = sqlite3.connect(backup_path)
        try:
            self._get_connection().backup(destination)
        finally:
            destination.close()
        return backup_path

    def _migrate_db_v2(
        self,
        cursor: sqlite3.Cursor,
        projects_table_exists: bool,
        current_version: int,
    ) -> None:
        """Add recoverable waveform/vector state while retaining legacy metadata."""
        if not cursor.connection.in_transaction:
            cursor.execute("BEGIN IMMEDIATE")
        if not projects_table_exists:
            cursor.execute("""
                CREATE TABLE projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    temp_dir TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    settings_json TEXT DEFAULT '{}'
                )
            """)
        cursor.execute("""
            INSERT OR IGNORE INTO projects (id, name, description)
            VALUES ('default', '默认工程', '系统默认工程')
        """)

        project_map: Dict[str, str] = {}
        project_rows = cursor.execute("SELECT * FROM projects").fetchall()
        for project in project_rows:
            old_id = project["id"]
            if is_safe_project_id(old_id):
                project_map[old_id] = old_id
                continue
            new_id = f"legacy_{hashlib.sha256(old_id.encode('utf-8')).hexdigest()[:16]}"
            project_map[old_id] = new_id
            cursor.execute("""
                INSERT OR IGNORE INTO projects
                (id, name, description, temp_dir, created_at, updated_at, settings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                new_id,
                project["name"],
                project["description"],
                project["temp_dir"],
                project["created_at"],
                project["updated_at"],
                project["settings_json"],
            ))
            _get_logger().warning(f"不安全工程 ID 已迁移: {old_id!r} -> {new_id}")

        legacy_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(files)").fetchall()
        }
        cursor.execute("ALTER TABLE files RENAME TO files_legacy_v2")
        cursor.execute("""
            CREATE TABLE files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_uuid TEXT NOT NULL UNIQUE,
                path TEXT NOT NULL,
                canonical_path TEXT NOT NULL,
                project_id TEXT DEFAULT 'default',
                filename TEXT NOT NULL,
                duration REAL DEFAULT 0,
                sample_rate INTEGER DEFAULT 0,
                channels INTEGER DEFAULT 0,
                file_size INTEGER DEFAULT 0,
                source_fingerprint TEXT,
                peaks_json TEXT,
                waveform_fingerprint TEXT,
                waveform_version INTEGER DEFAULT 0,
                tags TEXT DEFAULT '[]',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, canonical_path),
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
        """)

        legacy_rows = cursor.execute(
            "SELECT rowid AS legacy_rowid, * FROM files_legacy_v2"
        ).fetchall()
        used_keys: set[Tuple[str, str]] = set()
        used_uuids: set[str] = set()
        for row in legacy_rows:
            path = row["path"] if "path" in legacy_columns else None
            if not path:
                continue
            legacy_project = (
                row["project_id"] if "project_id" in legacy_columns else "default"
            ) or "default"
            project_id = project_map.get(legacy_project, legacy_project)
            if not cursor.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
                if not is_safe_project_id(project_id):
                    project_id = f"legacy_{hashlib.sha256(project_id.encode('utf-8')).hexdigest()[:16]}"
                cursor.execute(
                    "INSERT OR IGNORE INTO projects(id, name, description) VALUES (?, ?, ?)",
                    (project_id, project_id, "Migrated legacy project"),
                )
            canonical = canonicalize_path(path)
            key = (project_id, canonical)
            if key in used_keys:
                canonical = f"{canonical}#legacy-{row['legacy_rowid']}"
                key = (project_id, canonical)
            used_keys.add(key)
            file_uuid = (
                row["file_uuid"]
                if "file_uuid" in legacy_columns and row["file_uuid"]
                else _stable_file_uuid(project_id, canonical)
            )
            if file_uuid in used_uuids:
                file_uuid = str(uuid.uuid4())
            used_uuids.add(file_uuid)

            def legacy_value(name: str, default: Any) -> Any:
                return row[name] if name in legacy_columns and row[name] is not None else default

            source_fingerprint = legacy_value(
                "source_fingerprint", make_source_fingerprint(path)
            )
            cursor.execute("""
                INSERT INTO files
                (file_uuid, path, canonical_path, project_id, filename, duration,
                 sample_rate, channels, file_size, source_fingerprint, peaks_json,
                 waveform_fingerprint, waveform_version, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                file_uuid,
                path,
                canonical,
                project_id,
                legacy_value("filename", os.path.basename(path)),
                legacy_value("duration", 0),
                legacy_value("sample_rate", 0),
                legacy_value("channels", 0),
                legacy_value("file_size", 0),
                source_fingerprint,
                legacy_value("peaks_json", None),
                legacy_value("waveform_fingerprint", None),
                legacy_value("waveform_version", 0),
                legacy_value("tags", "[]"),
                legacy_value("created_at", datetime.now().isoformat()),
                legacy_value("updated_at", datetime.now().isoformat()),
            ))
        cursor.execute("DROP TABLE files_legacy_v2")

        # CREATE IF NOT EXISTS keeps all user tables and adds v2 support tables.
        # Execute each statement on the active transaction: executescript()
        # would commit the destructive legacy-table rewrite before backfill.
        _execute_sql_statements(cursor, CREATE_TABLE_SQL)
        for old_id, new_id in project_map.items():
            if old_id == new_id:
                continue
            for table in ("recent_projects", "user_folders", "imported_folder_mappings"):
                exists = cursor.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists:
                    cursor.execute(
                        f"UPDATE OR IGNORE {table} SET project_id=? WHERE project_id=?",
                        (new_id, old_id),
                    )
            cursor.execute("DELETE FROM projects WHERE id=?", (old_id,))

        now = datetime.now().isoformat()
        migrated_files = cursor.execute("""
            SELECT file_uuid, project_id, source_fingerprint, peaks_json FROM files
        """).fetchall()
        for row in migrated_files:
            try:
                peaks = json.loads(row["peaks_json"]) if row["peaks_json"] else None
                valid_peaks = bool(
                    isinstance(peaks, list)
                    and peaks
                    and all(
                        isinstance(value, (int, float)) and math.isfinite(value)
                        for value in peaks
                    )
                )
            except (ValueError, TypeError):
                valid_peaks = False
            for kind, state in (
                ("waveform", "stale" if valid_peaks else "pending"),
                ("audio_vector", "pending"),
                ("text_vector", "pending"),
            ):
                cursor.execute("""
                    INSERT OR REPLACE INTO file_artifacts
                    (project_id, file_uuid, kind, state, source_fingerprint, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    row["project_id"],
                    row["file_uuid"],
                    kind,
                    state,
                    row["source_fingerprint"],
                    now,
                ))
        integrity = cursor.execute("PRAGMA quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise sqlite3.DatabaseError(f"migration quick_check failed: {integrity}")
        _get_logger().info(f"数据库迁移完成: v{current_version} -> v2")

    def _migrate_db_v3(self, cursor: sqlite3.Cursor) -> None:
        """Merge physical-path aliases without resetting v2 artifact state.

        The richer row remains the authority and keeps its UUID.  If a ready
        vector only exists under a discarded alias UUID it is marked stale,
        because Chroma must recreate that vector under the surviving UUID.
        """
        if not cursor.connection.in_transaction:
            cursor.execute("BEGIN IMMEDIATE")

        rows = [dict(row) for row in cursor.execute(
            "SELECT * FROM files ORDER BY id"
        ).fetchall()]
        artifacts_by_uuid: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for artifact_row in cursor.execute("SELECT * FROM file_artifacts").fetchall():
            artifact = dict(artifact_row)
            artifacts_by_uuid.setdefault(str(artifact["file_uuid"]), {})[
                str(artifact["kind"])
            ] = artifact

        def valid_peaks(value: Any) -> bool:
            try:
                peaks = json.loads(value) if value else None
            except (TypeError, ValueError):
                return False
            return bool(
                isinstance(peaks, list)
                and peaks
                and all(
                    isinstance(item, (int, float)) and math.isfinite(item)
                    for item in peaks
                )
            )

        def tags_from(value: Any) -> List[str]:
            try:
                tags = json.loads(value) if value else []
            except (TypeError, ValueError):
                return []
            return [str(tag) for tag in tags] if isinstance(tags, list) else []

        def row_score(row: Dict[str, Any]) -> Tuple[Any, ...]:
            artifacts = artifacts_by_uuid.get(str(row["file_uuid"]), {})
            ready_vectors = sum(
                artifacts.get(kind, {}).get("state") == "ready"
                for kind in ("audio_vector", "text_vector")
            )
            ready_total = sum(
                artifact.get("state") == "ready" for artifact in artifacts.values()
            )
            return (
                ready_vectors,
                ready_total,
                valid_peaks(row.get("peaks_json")),
                float(row.get("duration") or 0) > 0,
                int(row.get("sample_rate") or 0) > 0,
                int(row.get("channels") or 0) > 0,
                len(tags_from(row.get("tags"))),
                int(row.get("file_size") or 0),
                str(row.get("updated_at") or ""),
                -int(row.get("id") or 0),
            )

        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            canonical = canonicalize_path(str(row.get("path") or ""))
            if not canonical:
                canonical = str(row.get("canonical_path") or "")
            groups.setdefault((str(row["project_id"]), canonical), []).append(row)

        merged_aliases = 0
        dirty_projects: Dict[str, set[str]] = {}
        state_rank = {
            "ready": 5,
            "processing": 4,
            "stale": 3,
            "failed": 2,
            "pending": 1,
        }
        now = datetime.now().isoformat()

        for (project_id, canonical), group in groups.items():
            if len(group) == 1:
                row = group[0]
                if str(row.get("canonical_path") or "") != canonical:
                    cursor.execute(
                        "UPDATE files SET canonical_path=?, updated_at=? WHERE id=?",
                        (canonical, now, row["id"]),
                    )
                continue

            survivor = max(group, key=row_score)
            survivor_uuid = str(survivor["file_uuid"])
            losers = [row for row in group if row["id"] != survivor["id"]]
            loser_uuids = {str(row["file_uuid"]) for row in losers}
            metadata_donor = max(
                group,
                key=lambda row: (
                    float(row.get("duration") or 0) > 0,
                    int(row.get("sample_rate") or 0) > 0,
                    int(row.get("channels") or 0) > 0,
                    int(row.get("file_size") or 0),
                    str(row.get("updated_at") or ""),
                ),
            )
            peak_rows = [row for row in group if valid_peaks(row.get("peaks_json"))]
            peak_donor = max(peak_rows, key=row_score) if peak_rows else None

            merged_tags: List[str] = []
            for row in sorted(group, key=row_score, reverse=True):
                for tag in tags_from(row.get("tags")):
                    if tag not in merged_tags:
                        merged_tags.append(tag)

            artifact_candidates: Dict[str, List[Dict[str, Any]]] = {
                kind: [] for kind in ARTIFACT_KINDS
            }
            for row in group:
                for kind, artifact in artifacts_by_uuid.get(
                    str(row["file_uuid"]), {}
                ).items():
                    artifact_candidates[kind].append(artifact)

            # Remove conflicting canonical keys and their cascading artifact
            # rows before assigning the new physical key to the survivor.
            cursor.executemany(
                "DELETE FROM files WHERE id=?",
                [(row["id"],) for row in losers],
            )
            stored_path = str(metadata_donor["path"])
            if not _is_windows_style_path(stored_path):
                stored_path = canonical
            source_fingerprint = (
                (peak_donor or metadata_donor).get("source_fingerprint")
                or survivor.get("source_fingerprint")
            )
            cursor.execute("""
                UPDATE files SET
                    path=?, canonical_path=?, filename=?, duration=?, sample_rate=?,
                    channels=?, file_size=?, source_fingerprint=?, peaks_json=?,
                    waveform_fingerprint=?, waveform_version=?, tags=?,
                    created_at=?, updated_at=?
                WHERE id=?
            """, (
                stored_path,
                canonical,
                metadata_donor.get("filename") or Path(stored_path).name,
                metadata_donor.get("duration") or 0,
                metadata_donor.get("sample_rate") or 0,
                metadata_donor.get("channels") or 0,
                metadata_donor.get("file_size") or 0,
                source_fingerprint,
                peak_donor.get("peaks_json") if peak_donor else None,
                peak_donor.get("waveform_fingerprint") if peak_donor else None,
                peak_donor.get("waveform_version") if peak_donor else 0,
                json.dumps(merged_tags, ensure_ascii=False),
                min(str(row.get("created_at") or now) for row in group),
                max(str(row.get("updated_at") or now) for row in group),
                survivor["id"],
            ))

            for kind in ARTIFACT_KINDS:
                candidates = artifact_candidates[kind]
                chosen = max(
                    candidates,
                    key=lambda artifact: (
                        state_rank.get(str(artifact.get("state")), 0),
                        str(artifact.get("file_uuid")) == survivor_uuid,
                        str(artifact.get("updated_at") or ""),
                    ),
                ) if candidates else {}
                state = str(chosen.get("state") or "pending")
                error_code = chosen.get("error_code")
                error_message = chosen.get("error_message")
                engine_fingerprint = chosen.get("engine_fingerprint")
                if kind == "waveform" and peak_donor:
                    state = "ready"
                    engine_fingerprint = peak_donor.get("waveform_fingerprint")
                    error_code = None
                    error_message = None
                elif (
                    kind in {"audio_vector", "text_vector"}
                    and chosen.get("file_uuid") in loser_uuids
                    and state == "ready"
                ):
                    state = "stale"
                    error_code = "path_alias_uuid_merged"
                    error_message = "Vector must be recreated under the surviving file UUID"
                if kind in {"audio_vector", "text_vector"} and any(
                    artifact.get("file_uuid") in loser_uuids
                    and artifact.get("state") == "ready"
                    for artifact in candidates
                ):
                    dirty_projects.setdefault(project_id, set()).add(kind)
                cursor.execute("""
                    INSERT INTO file_artifacts
                    (project_id, file_uuid, kind, state, source_fingerprint,
                     engine_fingerprint, error_code, error_message, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, file_uuid, kind) DO UPDATE SET
                        state=excluded.state,
                        source_fingerprint=excluded.source_fingerprint,
                        engine_fingerprint=excluded.engine_fingerprint,
                        error_code=excluded.error_code,
                        error_message=excluded.error_message,
                        updated_at=excluded.updated_at
                """, (
                    project_id,
                    survivor_uuid,
                    kind,
                    state,
                    chosen.get("source_fingerprint") or source_fingerprint,
                    engine_fingerprint,
                    error_code,
                    error_message,
                    now,
                ))
            merged_aliases += len(losers)

        # Folder mappings used raw-path uniqueness in v2.  Merge aliases here
        # and make future writes use the same physical identity.
        mapping_rows = [dict(row) for row in cursor.execute(
            "SELECT * FROM imported_folder_mappings ORDER BY id"
        ).fetchall()]
        mapping_groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for row in mapping_rows:
            mapping_groups.setdefault(
                (str(row["project_id"]), canonicalize_path(str(row["folder_path"]))),
                [],
            ).append(row)
        for (_project_id, canonical), group in mapping_groups.items():
            if not canonical:
                continue
            winner = max(
                group,
                key=lambda row: (
                    bool(row.get("user_folder_id")),
                    int(row.get("file_count") or 0),
                    -int(row["id"]),
                ),
            )
            losers = [row for row in group if row["id"] != winner["id"]]
            if losers:
                cursor.executemany(
                    "DELETE FROM imported_folder_mappings WHERE id=?",
                    [(row["id"],) for row in losers],
                )
            stored_folder = str(winner["folder_path"])
            if not _is_windows_style_path(stored_folder):
                stored_folder = canonical
            cursor.execute("""
                UPDATE imported_folder_mappings
                SET folder_path=?, folder_name=?, user_folder_id=?, file_count=?
                WHERE id=?
            """, (
                stored_folder,
                winner.get("folder_name") or Path(stored_folder).name,
                winner.get("user_folder_id"),
                max(int(row.get("file_count") or 0) for row in group),
                winner["id"],
            ))

        for project_id, kinds in dirty_projects.items():
            for kind in kinds:
                cursor.execute("""
                    UPDATE index_manifests
                    SET state='stale', revision=revision + 1, updated_at=?
                    WHERE project_id=? AND kind=?
                """, (now, project_id, kind))

        integrity = cursor.execute("PRAGMA quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise sqlite3.DatabaseError(f"v3 migration quick_check failed: {integrity}")
        _get_logger().info(
            "数据库迁移完成: v2 -> v3; 合并 %s 条物理路径别名", merged_aliases
        )

    def _row_to_record(self, row: sqlite3.Row) -> AudioFileRecord:
        """将数据库行转换为记录对象"""
        return AudioFileRecord(
            path=row['path'],
            filename=row['filename'],
            duration=row['duration'],
            sample_rate=row['sample_rate'],
            channels=row['channels'],
            file_size=row['file_size'],
            peaks_json=row['peaks_json'],
            tags=row['tags'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            file_uuid=row['file_uuid'],
            project_id=row['project_id'],
            canonical_path=row['canonical_path'],
            source_fingerprint=row['source_fingerprint'],
            waveform_fingerprint=row['waveform_fingerprint'],
            waveform_version=row['waveform_version'],
        )

    # ========== CRUD 操作 ==========

    def upsert_file(self, record: AudioFileRecord, project_id: str = "default") -> Optional[str]:
        """Insert/update metadata and return the stable project-scoped file UUID."""
        if not is_safe_project_id(project_id):
            raise ValueError(f"unsafe project id: {project_id!r}")
        canonical_path = canonicalize_path(record.path)
        if not canonical_path:
            raise ValueError("file path is empty")
        source_fingerprint = record.source_fingerprint or make_source_fingerprint(record.path)
        now = datetime.now().isoformat()
        try:
            with self.get_cursor() as cursor:
                existing = cursor.execute("""
                    SELECT file_uuid, source_fingerprint, filename, tags
                    FROM files WHERE project_id=? AND canonical_path=?
                """, (project_id, canonical_path)).fetchone()
                file_uuid = (
                    existing["file_uuid"] if existing else
                    record.file_uuid or _stable_file_uuid(project_id, canonical_path)
                )
                source_changed = bool(
                    existing and existing["source_fingerprint"] != source_fingerprint
                )
                metadata_changed = bool(
                    existing and existing["filename"] != record.filename
                )
                cursor.execute("""
                    INSERT INTO files
                    (file_uuid, path, canonical_path, project_id, filename, duration,
                     sample_rate, channels, file_size, source_fingerprint, peaks_json,
                     waveform_fingerprint, waveform_version, tags, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, canonical_path) DO UPDATE SET
                        path = excluded.path,
                        filename = excluded.filename,
                        duration = excluded.duration,
                        sample_rate = excluded.sample_rate,
                        channels = excluded.channels,
                        file_size = excluded.file_size,
                        source_fingerprint = excluded.source_fingerprint,
                        peaks_json = CASE
                            WHEN NOT (files.source_fingerprint IS excluded.source_fingerprint)
                                THEN excluded.peaks_json
                            ELSE COALESCE(excluded.peaks_json, files.peaks_json)
                        END,
                        waveform_fingerprint = CASE
                            WHEN NOT (files.source_fingerprint IS excluded.source_fingerprint)
                                THEN excluded.waveform_fingerprint
                            ELSE COALESCE(
                                excluded.waveform_fingerprint, files.waveform_fingerprint
                            )
                        END,
                        waveform_version = CASE
                            WHEN excluded.peaks_json IS NOT NULL THEN excluded.waveform_version
                            WHEN NOT (files.source_fingerprint IS excluded.source_fingerprint)
                                THEN NULL
                            ELSE files.waveform_version
                        END,
                        tags = CASE
                            WHEN files.tags IS NOT NULL AND files.tags != '[]' THEN files.tags
                            ELSE excluded.tags
                        END,
                        updated_at = excluded.updated_at
                """, (
                    file_uuid,
                    record.path,
                    canonical_path,
                    project_id,
                    record.filename,
                    record.duration,
                    record.sample_rate,
                    record.channels,
                    record.file_size,
                    source_fingerprint,
                    record.peaks_json,
                    record.waveform_fingerprint,
                    record.waveform_version,
                    record.tags,
                    now,
                ))

                waveform_state = "ready" if record.get_peaks() else "pending"
                for kind in ARTIFACT_KINDS:
                    initial_state = waveform_state if kind == "waveform" else "pending"
                    cursor.execute("""
                        INSERT OR IGNORE INTO file_artifacts
                        (project_id, file_uuid, kind, state, source_fingerprint, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (project_id, file_uuid, kind, initial_state, source_fingerprint, now))
                if source_changed:
                    cursor.execute("""
                        UPDATE file_artifacts
                        SET state='stale', source_fingerprint=?, error_code=NULL,
                            error_message=NULL, updated_at=?
                        WHERE project_id=? AND file_uuid=?
                          AND kind IN ('waveform', 'audio_vector', 'text_vector')
                    """, (source_fingerprint, now, project_id, file_uuid))
                if metadata_changed:
                    cursor.execute("""
                        UPDATE file_artifacts
                        SET state='stale', source_fingerprint=?, error_code=NULL,
                            error_message=NULL, updated_at=?
                        WHERE project_id=? AND file_uuid=? AND kind='text_vector'
                    """, (source_fingerprint, now, project_id, file_uuid))
                if record.get_peaks():
                    cursor.execute("""
                        UPDATE file_artifacts SET state='ready', source_fingerprint=?,
                            engine_fingerprint=?, error_code=NULL, error_message=NULL, updated_at=?
                        WHERE project_id=? AND file_uuid=? AND kind='waveform'
                    """, (
                        source_fingerprint,
                        record.waveform_fingerprint,
                        now,
                        project_id,
                        file_uuid,
                    ))
            return file_uuid
        except Exception as exc:
            _get_logger().error(f"写入文件失败 {record.path}: {exc}")
            raise

    def add_file(self, record: AudioFileRecord, project_id: str = 'default') -> bool:
        """
        添加或更新文件记录（按工程内路径去重）

        Args:
            record: 音频文件记录
            project_id: 所属工程ID

        Returns:
            是否成功
        """
        return self.upsert_file(record, project_id) is not None

    def add_file_simple(
        self,
        path: str,
        filename: str,
        duration: float = 0.0,
        sample_rate: int = 0,
        channels: int = 0,
        file_size: int = 0,
        peaks_json: Optional[str] = None,
        tags: str = '[]'
    ) -> bool:
        """
        简化版添加文件记录

        Args:
            path: 文件路径
            filename: 文件名
            duration: 时长
            sample_rate: 采样率
            channels: 声道数
            file_size: 文件大小
            peaks_json: 波形峰值 JSON
            tags: 标签 JSON

        Returns:
            是否成功
        """
        record = AudioFileRecord(
            path=path,
            filename=filename,
            duration=duration,
            sample_rate=sample_rate,
            channels=channels,
            file_size=file_size,
            peaks_json=peaks_json,
            tags=tags
        )
        return self.add_file(record)

    def get_file(self, path: str, project_id: str = None) -> Optional[AudioFileRecord]:
        """
        获取单个文件记录

        Args:
            path: 文件路径

        Returns:
            音频文件记录，如果不存在则返回 None
        """
        canonical_path = canonicalize_path(path)
        with self.get_cursor() as cursor:
            if project_id:
                cursor.execute(
                    "SELECT * FROM files WHERE canonical_path = ? AND project_id = ?",
                    (canonical_path, project_id),
                )
            else:
                cursor.execute("SELECT * FROM files WHERE canonical_path = ?", (canonical_path,))
            row = cursor.fetchone()
            if row:
                return self._row_to_record(row)
        return None

    def get_all_files(self) -> List[AudioFileRecord]:
        """
        获取所有文件记录

        Returns:
            音频文件记录列表
        """
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM files ORDER BY created_at DESC")
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def get_files_paginated(
        self,
        offset: int = 0,
        limit: int = 100,
        project_id: Optional[str] = None,
    ) -> List[AudioFileRecord]:
        """
        分页获取文件记录

        Args:
            offset: 起始位置
            limit: 返回数量

        Returns:
            音频文件记录列表
        """
        with self.get_cursor() as cursor:
            if project_id:
                cursor.execute(
                    "SELECT * FROM files WHERE project_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (project_id, limit, offset),
                )
            else:
                cursor.execute(
                    "SELECT * FROM files ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def get_files_cursor_page(
        self,
        project_id: str,
        limit: int = 200,
        before_id: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        """Return a metadata-only cursor page; waveform arrays are deliberately omitted."""
        limit = max(1, min(int(limit), 500))
        with self.get_cursor() as cursor:
            sql = """
                SELECT f.id, f.file_uuid, f.path, f.filename, f.duration,
                       f.sample_rate, f.channels, f.file_size, f.tags,
                       f.source_fingerprint, f.waveform_version,
                       MAX(CASE WHEN a.kind='waveform' THEN a.state END) AS waveform_state,
                       MAX(CASE WHEN a.kind='audio_vector' THEN a.state END) AS audio_index_state,
                       MAX(CASE WHEN a.kind='text_vector' THEN a.state END) AS text_index_state
                FROM files f
                LEFT JOIN file_artifacts a
                  ON a.project_id=f.project_id AND a.file_uuid=f.file_uuid
                WHERE f.project_id=?
            """
            params: List[Any] = [project_id]
            if before_id is not None:
                sql += " AND f.id < ?"
                params.append(before_id)
            sql += " GROUP BY f.id ORDER BY f.id DESC LIMIT ?"
            params.append(limit + 1)
            rows = cursor.execute(sql, params).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = []
        for row in rows:
            try:
                tags = json.loads(row["tags"] or "[]")
            except (TypeError, ValueError):
                tags = []
            items.append({
                "id": row["file_uuid"],
                "path": row["path"],
                "filename": row["filename"],
                "duration": row["duration"],
                "sample_rate": row["sample_rate"],
                "channels": row["channels"],
                "file_size": row["file_size"],
                "format": Path(row["path"]).suffix.lower().lstrip("."),
                "tags": tags,
                "source_fingerprint": row["source_fingerprint"],
                "waveform_version": row["waveform_version"],
                "waveform_state": row["waveform_state"] or "pending",
                "audio_index_state": row["audio_index_state"] or "pending",
                "text_index_state": row["text_index_state"] or "pending",
            })
        next_cursor = rows[-1]["id"] if has_more and rows else None
        return items, next_cursor

    def get_file_by_uuid(
        self, file_uuid: str, project_id: Optional[str] = None
    ) -> Optional[AudioFileRecord]:
        with self.get_cursor() as cursor:
            if project_id:
                row = cursor.execute(
                    "SELECT * FROM files WHERE file_uuid=? AND project_id=?",
                    (file_uuid, project_id),
                ).fetchone()
            else:
                row = cursor.execute(
                    "SELECT * FROM files WHERE file_uuid=?", (file_uuid,)
                ).fetchone()
        return self._row_to_record(row) if row else None

    def file_exists(self, path: str, project_id: str = None) -> bool:
        """
        检查文件记录是否存在

        Args:
            path: 文件路径
            project_id: 工程ID（可选，如果提供则只检查该工程）

        Returns:
            是否存在
        """
        canonical_path = canonicalize_path(path)
        with self.get_cursor() as cursor:
            if project_id:
                cursor.execute(
                    "SELECT 1 FROM files WHERE canonical_path = ? AND project_id = ?",
                    (canonical_path, project_id),
                )
            else:
                cursor.execute("SELECT 1 FROM files WHERE canonical_path = ?", (canonical_path,))
            return cursor.fetchone() is not None

    def update_peaks(
        self,
        path: str,
        peaks: List[float],
        project_id: str = None,
        source_fingerprint: Optional[str] = None,
        waveform_fingerprint: Optional[str] = None,
        waveform_version: int = 1,
    ) -> bool:
        """
        更新波形峰值数据

        Args:
            path: 文件路径
            peaks: 波形峰值列表

        Returns:
            是否成功
        """
        try:
            if not peaks or not all(
                isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1
                for value in peaks
            ):
                raise ValueError("waveform peaks must be a non-empty finite [0,1] array")
            peaks_json = json.dumps(peaks)
            canonical_path = canonicalize_path(path)
            source_fingerprint = source_fingerprint or make_source_fingerprint(path)
            effective_project = project_id or 'default'
            now = datetime.now().isoformat()
            with self.get_cursor() as cursor:
                if project_id:
                    existing_row = cursor.execute(
                        """
                        SELECT file_uuid, project_id, source_fingerprint
                        FROM files WHERE canonical_path=? AND project_id=?
                        LIMIT 1
                        """,
                        (canonical_path, project_id),
                    ).fetchone()
                else:
                    existing_row = cursor.execute(
                        """
                        SELECT file_uuid, project_id, source_fingerprint
                        FROM files WHERE canonical_path=?
                        LIMIT 1
                        """,
                        (canonical_path,),
                    ).fetchone()
                source_changed = bool(
                    existing_row
                    and existing_row["source_fingerprint"] != source_fingerprint
                )
                if project_id:
                    cursor.execute("""
                        UPDATE files SET peaks_json=?, source_fingerprint=?,
                            waveform_fingerprint=?, waveform_version=?, updated_at=?
                        WHERE canonical_path=? AND project_id=?
                    """, (
                        peaks_json, source_fingerprint, waveform_fingerprint,
                        waveform_version, now, canonical_path, project_id,
                    ))
                else:
                    cursor.execute("""
                        UPDATE files SET peaks_json=?, source_fingerprint=?,
                            waveform_fingerprint=?, waveform_version=?, updated_at=?
                        WHERE canonical_path=?
                    """, (
                        peaks_json, source_fingerprint, waveform_fingerprint,
                        waveform_version, now, canonical_path,
                    ))
                changed = cursor.rowcount > 0
                if changed:
                    file_row = existing_row or cursor.execute("""
                        SELECT file_uuid, project_id, source_fingerprint FROM files
                        WHERE canonical_path=? AND (? IS NULL OR project_id=?)
                        LIMIT 1
                    """, (canonical_path, project_id, project_id)).fetchone()
                    effective_project = file_row["project_id"]
                    if source_changed:
                        # A lazy waveform refresh must not make stale semantic
                        # vectors look current by advancing only files.source_fingerprint.
                        # Keep SQLite as the truth source and queue both vector
                        # artifacts for reconciliation against the new bytes.
                        cursor.execute("""
                            UPDATE file_artifacts
                            SET state='stale', source_fingerprint=?,
                                error_code=NULL, error_message=NULL, updated_at=?
                            WHERE project_id=? AND file_uuid=?
                              AND kind IN ('audio_vector', 'text_vector')
                        """, (
                            source_fingerprint, now, effective_project,
                            file_row["file_uuid"],
                        ))
                    cursor.execute("""
                        INSERT INTO file_artifacts
                        (project_id, file_uuid, kind, state, source_fingerprint,
                         engine_fingerprint, error_code, error_message, updated_at)
                        VALUES (?, ?, 'waveform', 'ready', ?, ?, NULL, NULL, ?)
                        ON CONFLICT(project_id, file_uuid, kind) DO UPDATE SET
                            state='ready', source_fingerprint=excluded.source_fingerprint,
                            engine_fingerprint=excluded.engine_fingerprint,
                            error_code=NULL, error_message=NULL, updated_at=excluded.updated_at
                    """, (
                        effective_project, file_row["file_uuid"], source_fingerprint,
                        waveform_fingerprint, now,
                    ))
            return changed
        except Exception as e:
            _get_logger().error(f"更新波形失败 {path}: {e}")
            return False

    def update_tags(self, path: str, tags: List[str], project_id: str = None) -> bool:
        """
        更新文件标签

        Args:
            path: 文件路径
            tags: 标签列表

        Returns:
            是否成功
        """
        try:
            tags_json = json.dumps(tags)
            canonical_path = canonicalize_path(path)
            now = datetime.now().isoformat()
            with self.get_cursor() as cursor:
                if project_id:
                    cursor.execute("""
                        UPDATE files SET tags = ?, updated_at = ?
                        WHERE canonical_path = ? AND project_id = ?
                    """, (tags_json, now, canonical_path, project_id))
                else:
                    cursor.execute("""
                        UPDATE files SET tags = ?, updated_at = ?
                        WHERE canonical_path = ?
                    """, (tags_json, now, canonical_path))
                changed = cursor.rowcount > 0
                if changed:
                    cursor.execute("""
                        UPDATE file_artifacts SET state='stale', error_code=NULL,
                            error_message=NULL, updated_at=?
                        WHERE kind='text_vector' AND file_uuid IN (
                            SELECT file_uuid FROM files
                            WHERE canonical_path=? AND (? IS NULL OR project_id=?)
                        )
                    """, (now, canonical_path, project_id, project_id))
            return changed
        except Exception as e:
            _get_logger().error(f"更新标签失败 {path}: {e}")
            return False

    def delete_file(self, path: str, project_id: str = None) -> bool:
        """
        删除文件记录

        Args:
            path: 文件路径

        Returns:
            是否成功删除
        """
        canonical_path = canonicalize_path(path)
        with self.get_cursor() as cursor:
            if project_id:
                cursor.execute(
                    "DELETE FROM files WHERE canonical_path = ? AND project_id = ?",
                    (canonical_path, project_id),
                )
            else:
                cursor.execute("DELETE FROM files WHERE canonical_path = ?", (canonical_path,))
            return cursor.rowcount > 0

    def search_files(self, keyword: str) -> List[AudioFileRecord]:
        """
        搜索文件（按文件名和标签）

        Args:
            keyword: 搜索关键词

        Returns:
            匹配的文件列表
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM files 
                WHERE filename LIKE ? OR tags LIKE ?
                ORDER BY created_at DESC
            """, (f'%{keyword}%', f'%{keyword}%'))
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def get_file_count(self, project_id: Optional[str] = None) -> int:
        """
        获取文件总数

        Returns:
            文件数量
        """
        with self.get_cursor() as cursor:
            if project_id:
                cursor.execute("SELECT COUNT(*) FROM files WHERE project_id=?", (project_id,))
            else:
                cursor.execute("SELECT COUNT(*) FROM files")
            return cursor.fetchone()[0]

    def get_total_duration(self, project_id: Optional[str] = None) -> float:
        """
        获取所有文件的总时长

        Returns:
            总时长（秒）
        """
        with self.get_cursor() as cursor:
            if project_id:
                cursor.execute(
                    "SELECT SUM(duration) FROM files WHERE project_id=?", (project_id,)
                )
            else:
                cursor.execute("SELECT SUM(duration) FROM files")
            result = cursor.fetchone()[0]
            return result if result else 0.0

    def clear_all(self) -> bool:
        """
        清空所有文件记录（谨慎使用）

        Returns:
            是否成功
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("DELETE FROM files")
            return True
        except Exception as e:
            _get_logger().error(f"清空数据库失败: {e}")
            return False

    def get_files_by_folder(self, folder_path: str, project_id: str = None) -> List[AudioFileRecord]:
        """
        获取指定文件夹下的所有文件

        Args:
            folder_path: 文件夹路径

        Returns:
            文件列表
        """
        canonical_folder = canonicalize_path(folder_path).rstrip("/\\")
        escaped = canonical_folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"{escaped}/%"
        with self.get_cursor() as cursor:
            if project_id:
                cursor.execute("""
                    SELECT * FROM files
                    WHERE (canonical_path = ? OR canonical_path LIKE ? ESCAPE '\\')
                      AND project_id = ?
                    ORDER BY filename
                """, (canonical_folder, pattern, project_id))
            else:
                cursor.execute("""
                    SELECT * FROM files
                    WHERE canonical_path = ? OR canonical_path LIKE ? ESCAPE '\\'
                    ORDER BY filename
                """, (canonical_folder, pattern))
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def remove_files_by_folder(self, folder_path: str, project_id: str = None) -> int:
        """
        删除指定文件夹下的所有文件记录

        Args:
            folder_path: 文件夹路径

        Returns:
            删除的文件数量
        """
        canonical_folder = canonicalize_path(folder_path).rstrip("/\\")
        escaped = canonical_folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"{escaped}/%"
        with self.get_cursor() as cursor:
            if project_id:
                cursor.execute("""
                    DELETE FROM files
                    WHERE (canonical_path = ? OR canonical_path LIKE ? ESCAPE '\\')
                      AND project_id = ?
                """, (canonical_folder, pattern, project_id))
            else:
                cursor.execute("""
                    DELETE FROM files
                    WHERE canonical_path = ? OR canonical_path LIKE ? ESCAPE '\\'
                """, (canonical_folder, pattern))
            return cursor.rowcount

    # ========== Artifact / index state ==========

    def set_artifact_state(
        self,
        project_id: str,
        file_uuid: str,
        kind: str,
        state: str,
        *,
        source_fingerprint: Optional[str] = None,
        engine_fingerprint: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        if kind not in ARTIFACT_KINDS or state not in ARTIFACT_STATES:
            raise ValueError(f"invalid artifact transition: {kind}/{state}")
        now = datetime.now().isoformat()
        with self.get_cursor() as cursor:
            exists = cursor.execute("""
                SELECT 1 FROM files WHERE project_id=? AND file_uuid=?
            """, (project_id, file_uuid)).fetchone()
            if not exists:
                return False
            cursor.execute("""
                INSERT INTO file_artifacts
                (project_id, file_uuid, kind, state, source_fingerprint,
                 engine_fingerprint, error_code, error_message, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, file_uuid, kind) DO UPDATE SET
                    state=excluded.state,
                    source_fingerprint=COALESCE(
                        excluded.source_fingerprint, file_artifacts.source_fingerprint
                    ),
                    engine_fingerprint=COALESCE(
                        excluded.engine_fingerprint, file_artifacts.engine_fingerprint
                    ),
                    error_code=excluded.error_code,
                    error_message=excluded.error_message,
                    updated_at=excluded.updated_at
            """, (
                project_id, file_uuid, kind, state, source_fingerprint,
                engine_fingerprint, error_code, error_message, now,
            ))
        return True

    def get_artifact_counts(self, project_id: str) -> Dict[str, Dict[str, int]]:
        result = {
            kind: {state: 0 for state in ARTIFACT_STATES}
            for kind in ARTIFACT_KINDS
        }
        with self.get_cursor() as cursor:
            rows = cursor.execute("""
                SELECT kind, state, COUNT(*) AS count
                FROM file_artifacts WHERE project_id=? GROUP BY kind, state
            """, (project_id,)).fetchall()
        for row in rows:
            result[row["kind"]][row["state"]] = row["count"]
        return result

    def get_artifact(
        self, project_id: str, file_uuid: str, kind: str
    ) -> Optional[Dict[str, Any]]:
        if kind not in ARTIFACT_KINDS:
            return None
        with self.get_cursor() as cursor:
            row = cursor.execute("""
                SELECT project_id, file_uuid, kind, state, source_fingerprint,
                       engine_fingerprint, error_code, error_message, updated_at
                FROM file_artifacts
                WHERE project_id=? AND file_uuid=? AND kind=?
            """, (project_id, file_uuid, kind)).fetchone()
        return dict(row) if row else None

    def get_ready_artifact_ids(
        self,
        project_id: str,
        kind: str,
        file_ids: Iterable[str],
    ) -> set[str]:
        """Return the requested IDs whose SQLite artifact is currently ready."""
        if kind not in ARTIFACT_KINDS:
            return set()
        selected = tuple(dict.fromkeys(str(value) for value in file_ids if value))
        if not selected:
            return set()
        ready: set[str] = set()
        # Stay below SQLite's common parameter bound while supporting large
        # internal search pages.
        for offset in range(0, len(selected), 500):
            batch = selected[offset : offset + 500]
            placeholders = ",".join("?" for _ in batch)
            with self.get_cursor() as cursor:
                rows = cursor.execute(
                    f"""
                    SELECT file_uuid FROM file_artifacts
                    WHERE project_id=? AND kind=? AND state='ready'
                      AND file_uuid IN ({placeholders})
                    """,
                    (project_id, kind, *batch),
                ).fetchall()
            ready.update(str(row["file_uuid"]) for row in rows)
        return ready

    def list_artifacts_for_work(
        self,
        project_id: str,
        kinds: Iterable[str] = ARTIFACT_KINDS,
        states: Iterable[str] = ("pending", "failed", "stale"),
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        selected_kinds = tuple(kind for kind in kinds if kind in ARTIFACT_KINDS)
        selected_states = tuple(state for state in states if state in ARTIFACT_STATES)
        if not selected_kinds or not selected_states:
            return []
        placeholders_kinds = ",".join("?" for _ in selected_kinds)
        placeholders_states = ",".join("?" for _ in selected_states)
        query = f"""
            SELECT f.file_uuid, f.path, f.filename, f.duration, f.sample_rate,
                   f.channels, f.file_size, f.tags, f.source_fingerprint,
                   a.kind, a.state, a.engine_fingerprint, a.error_code,
                   a.error_message
            FROM file_artifacts a
            JOIN files f ON f.project_id=a.project_id AND f.file_uuid=a.file_uuid
            WHERE a.project_id=? AND a.kind IN ({placeholders_kinds})
              AND a.state IN ({placeholders_states})
            ORDER BY a.updated_at, f.id LIMIT ?
        """
        # Index reconciliation is a background job and must be able to cover a
        # complete large library.  The public list API remains capped at 500;
        # this internal guard only prevents an accidentally unbounded query.
        params = [project_id, *selected_kinds, *selected_states, max(1, min(limit, 100000))]
        with self.get_cursor() as cursor:
            return [dict(row) for row in cursor.execute(query, params).fetchall()]

    def mark_project_artifacts(
        self, project_id: str, kinds: Iterable[str], state: str = "stale"
    ) -> int:
        selected_kinds = tuple(kind for kind in kinds if kind in ARTIFACT_KINDS)
        if not selected_kinds or state not in ARTIFACT_STATES:
            return 0
        placeholders = ",".join("?" for _ in selected_kinds)
        with self.get_cursor() as cursor:
            cursor.execute(f"""
                UPDATE file_artifacts
                SET state=?, error_code=NULL, error_message=NULL, updated_at=?
                WHERE project_id=? AND kind IN ({placeholders})
            """, (
                state, datetime.now().isoformat(), project_id, *selected_kinds,
            ))
            return cursor.rowcount

    def mark_text_artifacts_for_folders(
        self,
        project_id: str,
        folder_paths: Iterable[str],
        state: str = "stale",
    ) -> int:
        """Invalidate text artifacts for files under any mapped folder.

        Matching happens against canonical path keys in Python and updates use
        exact file UUIDs.  This keeps Windows drive/case/separator semantics
        consistent while treating literal ``%`` and ``_`` path characters as
        ordinary filename characters.
        """
        if state not in ARTIFACT_STATES:
            raise ValueError(f"invalid artifact state: {state}")
        roots = {
            canonicalize_path(folder_path).rstrip("/\\")
            for folder_path in folder_paths
            if str(folder_path or "").strip()
        }
        roots.discard("")
        if not roots:
            return 0

        with self.get_cursor() as cursor:
            rows = cursor.execute(
                "SELECT file_uuid, canonical_path FROM files WHERE project_id=?",
                (project_id,),
            ).fetchall()
            affected = [
                str(row["file_uuid"])
                for row in rows
                if any(
                    canonical_path_is_within(row["canonical_path"], root)
                    for root in roots
                )
            ]
            if not affected:
                return 0

            now = datetime.now().isoformat()
            updated = 0
            # Stay comfortably below SQLite's default host-parameter limit.
            for offset in range(0, len(affected), 400):
                batch = affected[offset:offset + 400]
                placeholders = ",".join("?" for _ in batch)
                cursor.execute(f"""
                    UPDATE file_artifacts
                    SET state=?, error_code=NULL, error_message=NULL, updated_at=?
                    WHERE project_id=? AND kind='text_vector'
                      AND file_uuid IN ({placeholders})
                """, (state, now, project_id, *batch))
                updated += cursor.rowcount
            return updated

    def upsert_index_manifest(
        self,
        project_id: str,
        kind: str,
        **values: Any,
    ) -> Dict[str, Any]:
        if kind not in ("audio_vector", "text_vector"):
            raise ValueError(f"invalid index kind: {kind}")
        allowed = {
            "collection_name", "engine_fingerprint", "model_id", "model_revision",
            "dimensions", "preprocessing_version", "metric", "state",
        }
        current = self.get_index_manifest(project_id, kind) or {}
        merged = {key: values.get(key, current.get(key)) for key in allowed}
        merged["metric"] = merged.get("metric") or "cosine"
        merged["state"] = merged.get("state") or "pending"
        revision = int(current.get("revision", 0)) + int(values.get("revision_increment", 0))
        now = datetime.now().isoformat()
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO index_manifests
                (project_id, kind, collection_name, engine_fingerprint, model_id,
                 model_revision, dimensions, preprocessing_version, metric,
                 revision, state, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, kind) DO UPDATE SET
                    collection_name=excluded.collection_name,
                    engine_fingerprint=excluded.engine_fingerprint,
                    model_id=excluded.model_id,
                    model_revision=excluded.model_revision,
                    dimensions=excluded.dimensions,
                    preprocessing_version=excluded.preprocessing_version,
                    metric=excluded.metric,
                    revision=excluded.revision,
                    state=excluded.state,
                    updated_at=excluded.updated_at
            """, (
                project_id, kind, merged.get("collection_name"),
                merged.get("engine_fingerprint"), merged.get("model_id"),
                merged.get("model_revision"), merged.get("dimensions"),
                merged.get("preprocessing_version"), merged["metric"], revision,
                merged["state"], now,
            ))
        return self.get_index_manifest(project_id, kind) or {}

    def get_index_manifest(self, project_id: str, kind: str) -> Optional[Dict[str, Any]]:
        with self.get_cursor() as cursor:
            row = cursor.execute("""
                SELECT * FROM index_manifests WHERE project_id=? AND kind=?
            """, (project_id, kind)).fetchone()
        return dict(row) if row else None

    def activate_index_manifests(
        self, project_id: str, manifests: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Atomically make one or more verified shadow collections active."""
        if not is_safe_project_id(project_id):
            raise ValueError("invalid project id")
        valid: Dict[str, Dict[str, Any]] = {}
        for kind, payload in manifests.items():
            if kind not in ("audio_vector", "text_vector"):
                raise ValueError(f"invalid index kind: {kind}")
            collection_name = str(payload.get("collection_name") or "").strip()
            if not collection_name or payload.get("metric") != "cosine":
                raise ValueError(f"unverified index manifest: {kind}")
            valid[kind] = dict(payload)
        if not valid:
            return {}

        now = datetime.now().isoformat()
        with self.get_cursor() as cursor:
            for kind, payload in valid.items():
                current = cursor.execute("""
                    SELECT revision FROM index_manifests
                    WHERE project_id=? AND kind=?
                """, (project_id, kind)).fetchone()
                revision = int(current["revision"] if current else 0) + 1
                cursor.execute("""
                    INSERT INTO index_manifests
                    (project_id, kind, collection_name, engine_fingerprint, model_id,
                     model_revision, dimensions, preprocessing_version, metric,
                     revision, state, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'cosine', ?, 'ready', ?)
                    ON CONFLICT(project_id, kind) DO UPDATE SET
                        collection_name=excluded.collection_name,
                        engine_fingerprint=excluded.engine_fingerprint,
                        model_id=excluded.model_id,
                        model_revision=excluded.model_revision,
                        dimensions=excluded.dimensions,
                        preprocessing_version=excluded.preprocessing_version,
                        metric='cosine', revision=excluded.revision,
                        state='ready', updated_at=excluded.updated_at
                """, (
                    project_id,
                    kind,
                    payload["collection_name"],
                    payload.get("engine_fingerprint"),
                    payload.get("model_id"),
                    payload.get("model_revision"),
                    payload.get("dimensions"),
                    payload.get("preprocessing_version"),
                    revision,
                    now,
                ))
        return {
            kind: self.get_index_manifest(project_id, kind) or {}
            for kind in valid
        }

    # ========== Persistent background jobs ==========

    def create_job(self, project_id: str, kind: str, total: int = 0) -> str:
        job_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self.get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO jobs(id, project_id, kind, state, total, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """, (job_id, project_id, kind, max(0, int(total)), now, now))
        return job_id

    def update_job(self, job_id: str, **changes: Any) -> bool:
        allowed = {
            "state", "stage", "processed", "total", "cancel_requested",
            "error_code", "error_message",
        }
        updates = []
        params: List[Any] = []
        for key, value in changes.items():
            if key in allowed:
                updates.append(f"{key}=?")
                params.append(value)
        if not updates:
            return False
        updates.append("updated_at=?")
        params.extend([datetime.now().isoformat(), job_id])
        with self.get_cursor() as cursor:
            cursor.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id=?", params)
            return cursor.rowcount > 0

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.get_cursor() as cursor:
            row = cursor.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def request_project_job_cancellation(self, project_id: str) -> List[str]:
        """Durably request cancellation before a project deletion takes its lock."""
        now = datetime.now().isoformat()
        with self.get_cursor() as cursor:
            rows = cursor.execute(
                """
                SELECT id FROM jobs
                WHERE project_id=? AND state IN ('pending', 'running')
                """,
                (project_id,),
            ).fetchall()
            job_ids = [str(row["id"]) for row in rows]
            if job_ids:
                cursor.execute(
                    """
                    UPDATE jobs SET cancel_requested=1, updated_at=?
                    WHERE project_id=? AND state IN ('pending', 'running')
                    """,
                    (now, project_id),
                )
        return job_ids

    # ========== 工程管理方法 ==========

    def create_project(self, project_id: str, name: str, description: str = "", temp_dir: Optional[str] = None) -> bool:
        """
        创建新工程

        Args:
            project_id: 工程唯一ID
            name: 工程名称
            description: 工程描述
            temp_dir: 工程特定的临时文件目录

        Returns:
            是否成功
        """
        if not is_safe_project_id(project_id):
            _get_logger().warning(f"拒绝不安全的工程 ID: {project_id!r}")
            return False
        try:
            with self.get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO projects (id, name, description, temp_dir, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (project_id, name, description, temp_dir, datetime.now().isoformat()))
            return True
        except Exception as e:
            _get_logger().error(f"创建工程失败 {project_id}: {e}")
            return False

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """
        获取工程信息

        Args:
            project_id: 工程ID

        Returns:
            工程信息字典
        """
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row['id'],
                    'name': row['name'],
                    'description': row['description'],
                    'temp_dir': row['temp_dir'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at'],
                    'settings': json.loads(row['settings_json'] or '{}')
                }
        return None

    def get_all_projects(self) -> List[Dict[str, Any]]:
        """
        获取所有工程列表

        Returns:
            工程信息列表
        """
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM projects ORDER BY updated_at DESC")
            return [{
                'id': row['id'],
                'name': row['name'],
                'description': row['description'],
                'temp_dir': row['temp_dir'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at'],
                'settings': json.loads(row['settings_json'] or '{}')
            } for row in cursor.fetchall()]

    def update_project(self, project_id: str, name: Optional[str] = None, 
                       description: Optional[str] = None, temp_dir: Optional[str] = None,
                       settings: Optional[Dict] = None) -> bool:
        """
        更新工程信息

        Args:
            project_id: 工程ID
            name: 新名称
            description: 新描述
            temp_dir: 新临时目录
            settings: 新配置

        Returns:
            是否成功
        """
        try:
            with self.get_cursor() as cursor:
                updates = []
                params = []
                if name is not None:
                    updates.append("name = ?")
                    params.append(name)
                if description is not None:
                    updates.append("description = ?")
                    params.append(description)
                if temp_dir is not None:
                    updates.append("temp_dir = ?")
                    params.append(temp_dir)
                if settings is not None:
                    updates.append("settings_json = ?")
                    params.append(json.dumps(settings))
                updates.append("updated_at = ?")
                params.append(datetime.now().isoformat())
                params.append(project_id)

                cursor.execute(f"""
                    UPDATE projects SET {', '.join(updates)} WHERE id = ?
                """, params)
            return True
        except Exception as e:
            _get_logger().error(f"更新工程失败 {project_id}: {e}")
            return False

    def delete_project(self, project_id: str) -> bool:
        """
        删除工程（会级联删除相关文件）

        Args:
            project_id: 工程ID

        Returns:
            是否成功
        """
        if project_id == "default" or not is_safe_project_id(project_id):
            return False
        try:
            with self.get_cursor() as cursor:
                cursor.execute("DELETE FROM files WHERE project_id = ?", (project_id,))
                cursor.execute("DELETE FROM imported_folder_mappings WHERE project_id = ?", (project_id,))
                cursor.execute("DELETE FROM user_folders WHERE project_id = ?", (project_id,))
                cursor.execute("DELETE FROM recent_projects WHERE project_id = ?", (project_id,))
                cursor.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return True
        except Exception as e:
            _get_logger().error(f"删除工程失败 {project_id}: {e}")
            return False

    def add_to_recent_projects(self, project_id: str) -> bool:
        """
        添加到最近工程列表

        Args:
            project_id: 工程ID

        Returns:
            是否成功
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self.get_cursor() as cursor:
                    cursor.execute("""
                        INSERT OR REPLACE INTO recent_projects (project_id, opened_at)
                        VALUES (?, ?)
                    """, (project_id, datetime.now().isoformat()))
                return True
            except sqlite3.Error as e:
                if "acquire_write" in str(e) or "database is locked" in str(e):
                    _get_logger().warning(f"数据库忙，重试 {attempt+1}/{max_retries}: {e}")
                    if attempt == 0:
                        # 第一次失败，尝试修复
                        self._repair_database()
                    import time
                    time.sleep(0.1 * (attempt + 1))  # 递增延迟
                else:
                    _get_logger().error(f"添加最近工程失败 {project_id}: {e}")
                    return False
        return False

    def get_recent_projects(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取最近工程列表

        Args:
            limit: 返回数量

        Returns:
            工程信息列表
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT p.* FROM projects p
                JOIN recent_projects r ON p.id = r.project_id
                ORDER BY r.opened_at DESC
                LIMIT ?
            """, (limit,))
            return [{
                'id': row['id'],
                'name': row['name'],
                'description': row['description'],
                'temp_dir': row['temp_dir'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at'],
                'settings': json.loads(row['settings_json'] or '{}')
            } for row in cursor.fetchall()]

    def get_project_file_count(self, project_id: str) -> int:
        """
        获取工程的文件数量

        Args:
            project_id: 工程ID

        Returns:
            文件数量
        """
        with self.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM files WHERE project_id = ?", (project_id,))
            return cursor.fetchone()[0]

    def get_files_by_project(self, project_id: str) -> List[AudioFileRecord]:
        """
        获取指定工程的所有文件

        Args:
            project_id: 工程ID

        Returns:
            文件列表
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM files WHERE project_id = ? ORDER BY created_at DESC
            """, (project_id,))
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def add_file_with_project(self, record: AudioFileRecord, project_id: str = 'default') -> bool:
        """
        添加文件到指定工程

        Args:
            record: 音频文件记录
            project_id: 工程ID

        Returns:
            是否成功
        """
        return self.upsert_file(record, project_id) is not None

    # ========== 用户自定义文件夹操作 ==========

    def create_user_folder(self, folder_id: str, project_id: str, name: str,
                          description: str = None, color: str = '#3b82f6',
                          sort_order: int = 0) -> bool:
        """
        创建用户自定义文件夹

        Args:
            folder_id: 文件夹ID
            project_id: 所属工程ID
            name: 文件夹名称
            description: 描述
            color: 颜色
            sort_order: 排序顺序

        Returns:
            是否成功
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO user_folders (id, project_id, name, description, color, sort_order, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (folder_id, project_id, name, description, color, sort_order, datetime.now().isoformat()))
            return True
        except Exception as e:
            _get_logger().error(f"创建用户文件夹失败 {name}: {e}")
            return False

    def get_user_folders(self, project_id: str) -> List[Dict[str, Any]]:
        """
        获取指定工程的所有用户自定义文件夹

        Args:
            project_id: 工程ID

        Returns:
            文件夹列表
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM user_folders
                WHERE project_id = ?
                ORDER BY sort_order ASC, created_at ASC
            """, (project_id,))
            return [{
                'id': row['id'],
                'project_id': row['project_id'],
                'name': row['name'],
                'description': row['description'],
                'color': row['color'],
                'sort_order': row['sort_order'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at']
            } for row in cursor.fetchall()]

    def get_user_folder(self, folder_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个用户文件夹

        Args:
            folder_id: 文件夹ID

        Returns:
            文件夹信息或None
        """
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM user_folders WHERE id = ?", (folder_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row['id'],
                    'project_id': row['project_id'],
                    'name': row['name'],
                    'description': row['description'],
                    'color': row['color'],
                    'sort_order': row['sort_order'],
                    'created_at': row['created_at'],
                    'updated_at': row['updated_at']
                }
            return None

    def update_user_folder(self, folder_id: str, name: str = None,
                          description: str = None, color: str = None,
                          sort_order: int = None) -> bool:
        """
        更新用户文件夹

        Args:
            folder_id: 文件夹ID
            name: 新名称
            description: 新描述
            color: 新颜色
            sort_order: 新排序顺序

        Returns:
            是否成功
        """
        try:
            with self.get_cursor() as cursor:
                updates = []
                params = []
                if name is not None:
                    updates.append("name = ?")
                    params.append(name)
                if description is not None:
                    updates.append("description = ?")
                    params.append(description)
                if color is not None:
                    updates.append("color = ?")
                    params.append(color)
                if sort_order is not None:
                    updates.append("sort_order = ?")
                    params.append(sort_order)
                updates.append("updated_at = ?")
                params.append(datetime.now().isoformat())
                params.append(folder_id)

                cursor.execute(f"""
                    UPDATE user_folders SET {', '.join(updates)} WHERE id = ?
                """, params)
            return True
        except Exception as e:
            _get_logger().error(f"更新用户文件夹失败 {folder_id}: {e}")
            return False

    def delete_user_folder(self, folder_id: str) -> bool:
        """
        删除用户文件夹

        Args:
            folder_id: 文件夹ID

        Returns:
            是否成功
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("DELETE FROM user_folders WHERE id = ?", (folder_id,))
            return True
        except Exception as e:
            _get_logger().error(f"删除用户文件夹失败 {folder_id}: {e}")
            return False

    # ========== 导入文件夹映射操作 ==========

    def add_imported_folder_mapping(self, project_id: str, folder_path: str,
                                    folder_name: str, user_folder_id: str = None,
                                    file_count: int = 0) -> bool:
        """
        添加或更新导入文件夹的映射

        Args:
            project_id: 工程ID
            folder_path: 导入的文件夹路径
            folder_name: 文件夹显示名称
            user_folder_id: 关联的用户文件夹ID
            file_count: 文件数量

        Returns:
            是否成功
        """
        try:
            with self.get_cursor() as cursor:
                canonical_folder = canonicalize_path(folder_path)
                existing = next((
                    row for row in cursor.execute("""
                        SELECT id, folder_path FROM imported_folder_mappings
                        WHERE project_id=?
                    """, (project_id,)).fetchall()
                    if canonicalize_path(str(row["folder_path"])) == canonical_folder
                ), None)
                if existing:
                    cursor.execute("""
                        UPDATE imported_folder_mappings
                        SET folder_name=?, file_count=?,
                            user_folder_id=COALESCE(?, user_folder_id)
                        WHERE id=?
                    """, (folder_name, file_count, user_folder_id, existing["id"]))
                    return True
                cursor.execute("""
                    INSERT INTO imported_folder_mappings
                    (project_id, folder_path, folder_name, user_folder_id, file_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, folder_path) DO UPDATE SET
                        folder_name=excluded.folder_name,
                        file_count=excluded.file_count,
                        user_folder_id=COALESCE(
                            excluded.user_folder_id,
                            imported_folder_mappings.user_folder_id
                        )
                """, (project_id, folder_path, folder_name, user_folder_id, file_count, datetime.now().isoformat()))
            return True
        except Exception as e:
            _get_logger().error(f"添加导入文件夹映射失败 {folder_path}: {e}")
            return False

    def get_imported_folder_mappings(self, project_id: str, user_folder_id: str = None) -> List[Dict[str, Any]]:
        """
        获取导入文件夹的映射

        Args:
            project_id: 工程ID
            user_folder_id: 筛选特定用户文件夹（可选）

        Returns:
            映射列表
        """
        with self.get_cursor() as cursor:
            if user_folder_id:
                cursor.execute("""
                    SELECT * FROM imported_folder_mappings
                    WHERE project_id = ? AND user_folder_id = ?
                    ORDER BY created_at DESC
                """, (project_id, user_folder_id))
            else:
                cursor.execute("""
                    SELECT * FROM imported_folder_mappings
                    WHERE project_id = ?
                    ORDER BY created_at DESC
                """, (project_id,))
            return [{
                'id': row['id'],
                'project_id': row['project_id'],
                'folder_path': row['folder_path'],
                'folder_name': row['folder_name'],
                'user_folder_id': row['user_folder_id'],
                'file_count': row['file_count'],
                'created_at': row['created_at']
            } for row in cursor.fetchall()]

    def update_imported_folder_mapping(self, project_id: str, folder_path: str,
                                       user_folder_id: str = None) -> bool:
        """
        更新导入文件夹的用户文件夹关联

        Args:
            project_id: 工程ID
            folder_path: 导入的文件夹路径
            user_folder_id: 新的用户文件夹ID

        Returns:
            是否成功
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("""
                    UPDATE imported_folder_mappings
                    SET user_folder_id = ?
                    WHERE project_id = ? AND folder_path = ?
                """, (user_folder_id, project_id, folder_path))
                return cursor.rowcount > 0
        except Exception as e:
            _get_logger().error(f"更新导入文件夹映射失败 {folder_path}: {e}")
            return False


# ========== 全局单例 ==========

_db_manager: Optional[DatabaseManager] = None


def get_db_manager(db_path: Optional[str] = None) -> DatabaseManager:
    """
    获取数据库管理器单例

    Args:
        db_path: 数据库文件路径，如果为 None 则使用默认路径

    Returns:
        DatabaseManager 实例
    """
    global _db_manager
    if _db_manager is None:
        if db_path is None:
            # 延迟导入避免循环依赖
            import config
            db_path = str(config.get_db_path() / "soundmind.db")
        _db_manager = DatabaseManager(db_path)
    return _db_manager


def reset_db_manager() -> None:
    """重置数据库管理器（用于关闭或重新初始化）"""
    global _db_manager
    if _db_manager is not None:
        # 关闭连接
        if hasattr(_db_manager._local, 'conn') and _db_manager._local.conn:
            try:
                _db_manager._local.conn.close()
            except Exception:
                pass
        _db_manager._local.conn = None
    _db_manager = None


def init_db(db_path: Optional[str] = None) -> DatabaseManager:
    """
    初始化数据库

    Args:
        db_path: 数据库文件路径

    Returns:
        DatabaseManager 实例
    """
    return get_db_manager(db_path)
