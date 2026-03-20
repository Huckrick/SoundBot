# -*- coding: utf-8 -*-
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
import threading
from pathlib import Path
from typing import List, Optional, Dict, Any
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime

logger = None  # 延迟初始化


def _get_logger():
    """延迟获取 logger，避免循环导入"""
    global logger
    if logger is None:
        from utils.logger import get_logger
        logger = get_logger()
    return logger


# SQL 建表语句
CREATE_TABLE_SQL = """
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

-- 音频文件表（添加 project_id 外键）
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    project_id TEXT DEFAULT 'default',
    filename TEXT NOT NULL,
    duration REAL DEFAULT 0,
    sample_rate INTEGER DEFAULT 0,
    channels INTEGER DEFAULT 0,
    file_size INTEGER DEFAULT 0,
    peaks_json TEXT,
    tags TEXT DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id);
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
"""


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

    def get_peaks(self) -> Optional[List[float]]:
        """获取波形峰值列表"""
        if self.peaks_json:
            try:
                return json.loads(self.peaks_json)
            except json.JSONDecodeError:
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

    _local = threading.local()

    def __init__(self, db_path: str):
        """
        初始化数据库管理器

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        _get_logger().info(f"DatabaseManager 初始化完成: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """获取线程局部的数据库连接"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            try:
                self._local.conn = sqlite3.connect(
                    self.db_path,
                    check_same_thread=False,
                    timeout=30.0
                )
                self._local.conn.row_factory = sqlite3.Row
                # 禁用 WAL 模式避免 acquire_write 错误
                # 使用 DELETE 模式更稳定
                self._local.conn.execute("PRAGMA journal_mode=DELETE")
                self._local.conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.Error as e:
                _get_logger().error(f"数据库连接失败: {e}")
                # 尝试修复数据库
                self._repair_database()
                # 重新连接
                self._local.conn = sqlite3.connect(
                    self.db_path,
                    check_same_thread=False,
                    timeout=30.0
                )
                self._local.conn.row_factory = sqlite3.Row
                self._local.conn.execute("PRAGMA journal_mode=DELETE")
                self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def _repair_database(self):
        """尝试修复损坏的数据库"""
        try:
            _get_logger().warning(f"尝试修复数据库: {self.db_path}")
            # 删除 WAL 相关文件
            import os
            wal_file = self.db_path + "-wal"
            shm_file = self.db_path + "-shm"
            if os.path.exists(wal_file):
                os.remove(wal_file)
                _get_logger().info(f"删除 WAL 文件: {wal_file}")
            if os.path.exists(shm_file):
                os.remove(shm_file)
                _get_logger().info(f"删除 SHM 文件: {shm_file}")
        except Exception as e:
            _get_logger().error(f"修复数据库失败: {e}")

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
        """初始化数据库（创建表和索引，支持迁移）"""
        with self.get_cursor() as cursor:
            # 检查 files 表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='files'")
            files_table_exists = cursor.fetchone() is not None

            # 检查 projects 表是否存在
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
            projects_table_exists = cursor.fetchone() is not None

            if not files_table_exists:
                # 全新数据库，直接创建所有表
                cursor.executescript(CREATE_TABLE_SQL)
                _get_logger().info("数据库初始化完成：创建新表")
            else:
                # 现有数据库，需要迁移
                _get_logger().info("检测到现有数据库，执行迁移...")
                self._migrate_db(cursor, projects_table_exists)

    def _migrate_db(self, cursor, projects_table_exists):
        """迁移现有数据库到新版结构"""
        try:
            # 1. 检查 files 表是否有 project_id 列
            cursor.execute("PRAGMA table_info(files)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'project_id' not in columns:
                _get_logger().info("迁移：添加 project_id 列到 files 表")
                cursor.execute("ALTER TABLE files ADD COLUMN project_id TEXT DEFAULT 'default'")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id)")

            # 2. 创建 projects 表（如果不存在）
            if not projects_table_exists:
                _get_logger().info("迁移：创建 projects 表")
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

            # 确保默认工程存在（无论表是否刚创建）
            cursor.execute("""
                INSERT OR IGNORE INTO projects (id, name, description)
                VALUES ('default', '默认工程', '系统默认工程')
            """)

            # 3. 创建 recent_projects 表（如果不存在）
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='recent_projects'")
            if cursor.fetchone() is None:
                _get_logger().info("迁移：创建 recent_projects 表")
                cursor.execute("""
                    CREATE TABLE recent_projects (
                        project_id TEXT PRIMARY KEY,
                        opened_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                    )
                """)

            # 4. 创建缺失的索引
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_files_project'")
            if cursor.fetchone() is None:
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_project ON files(project_id)")

            # 5. 创建 user_folders 表（如果不存在）
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_folders'")
            if cursor.fetchone() is None:
                _get_logger().info("迁移：创建 user_folders 表")
                cursor.execute("""
                    CREATE TABLE user_folders (
                        id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        description TEXT,
                        color TEXT DEFAULT '#3b82f6',
                        sort_order INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                    )
                """)
                cursor.execute("CREATE INDEX idx_user_folders_project ON user_folders(project_id)")
                cursor.execute("CREATE INDEX idx_user_folders_order ON user_folders(sort_order)")

            # 6. 创建 imported_folder_mappings 表（如果不存在）
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='imported_folder_mappings'")
            if cursor.fetchone() is None:
                _get_logger().info("迁移：创建 imported_folder_mappings 表")
                cursor.execute("""
                    CREATE TABLE imported_folder_mappings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id TEXT NOT NULL,
                        folder_path TEXT NOT NULL,
                        user_folder_id TEXT,
                        folder_name TEXT,
                        file_count INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                        FOREIGN KEY (user_folder_id) REFERENCES user_folders(id) ON DELETE SET NULL,
                        UNIQUE(project_id, folder_path)
                    )
                """)
                cursor.execute("CREATE INDEX idx_imported_mappings_project ON imported_folder_mappings(project_id)")
                cursor.execute("CREATE INDEX idx_imported_mappings_user_folder ON imported_folder_mappings(user_folder_id)")

            _get_logger().info("数据库迁移完成")
        except Exception as e:
            _get_logger().error(f"数据库迁移失败: {e}")
            raise

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
            updated_at=row['updated_at']
        )

    # ========== CRUD 操作 ==========

    def add_file(self, record: AudioFileRecord, project_id: str = 'default') -> bool:
        """
        添加或更新文件记录（INSERT OR REPLACE）

        Args:
            record: 音频文件记录
            project_id: 所属工程ID

        Returns:
            是否成功
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("""
                    INSERT OR REPLACE INTO files 
                    (path, project_id, filename, duration, sample_rate, channels, 
                     file_size, peaks_json, tags, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.path,
                    project_id,
                    record.filename,
                    record.duration,
                    record.sample_rate,
                    record.channels,
                    record.file_size,
                    record.peaks_json,
                    record.tags,
                    datetime.now().isoformat()
                ))
            return True
        except Exception as e:
            _get_logger().error(f"添加文件失败 {record.path}: {e}")
            return False

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

    def get_file(self, path: str) -> Optional[AudioFileRecord]:
        """
        获取单个文件记录

        Args:
            path: 文件路径

        Returns:
            音频文件记录，如果不存在则返回 None
        """
        with self.get_cursor() as cursor:
            cursor.execute("SELECT * FROM files WHERE path = ?", (path,))
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
        limit: int = 100
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
            cursor.execute(
                "SELECT * FROM files ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset)
            )
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def file_exists(self, path: str, project_id: str = None) -> bool:
        """
        检查文件记录是否存在

        Args:
            path: 文件路径
            project_id: 工程ID（可选，如果提供则只检查该工程）

        Returns:
            是否存在
        """
        with self.get_cursor() as cursor:
            if project_id:
                cursor.execute("SELECT 1 FROM files WHERE path = ? AND project_id = ?", (path, project_id))
            else:
                cursor.execute("SELECT 1 FROM files WHERE path = ?", (path,))
            return cursor.fetchone() is not None

    def update_peaks(self, path: str, peaks: List[float]) -> bool:
        """
        更新波形峰值数据

        Args:
            path: 文件路径
            peaks: 波形峰值列表

        Returns:
            是否成功
        """
        try:
            peaks_json = json.dumps(peaks)
            with self.get_cursor() as cursor:
                cursor.execute("""
                    UPDATE files SET peaks_json = ?, updated_at = ?
                    WHERE path = ?
                """, (peaks_json, datetime.now().isoformat(), path))
            return cursor.rowcount > 0
        except Exception as e:
            _get_logger().error(f"更新波形失败 {path}: {e}")
            return False

    def update_tags(self, path: str, tags: List[str]) -> bool:
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
            with self.get_cursor() as cursor:
                cursor.execute("""
                    UPDATE files SET tags = ?, updated_at = ?
                    WHERE path = ?
                """, (tags_json, datetime.now().isoformat(), path))
            return cursor.rowcount > 0
        except Exception as e:
            _get_logger().error(f"更新标签失败 {path}: {e}")
            return False

    def delete_file(self, path: str) -> bool:
        """
        删除文件记录

        Args:
            path: 文件路径

        Returns:
            是否成功删除
        """
        with self.get_cursor() as cursor:
            cursor.execute("DELETE FROM files WHERE path = ?", (path,))
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

    def get_file_count(self) -> int:
        """
        获取文件总数

        Returns:
            文件数量
        """
        with self.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM files")
            return cursor.fetchone()[0]

    def get_total_duration(self) -> float:
        """
        获取所有文件的总时长

        Returns:
            总时长（秒）
        """
        with self.get_cursor() as cursor:
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

    def get_files_by_folder(self, folder_path: str) -> List[AudioFileRecord]:
        """
        获取指定文件夹下的所有文件

        Args:
            folder_path: 文件夹路径

        Returns:
            文件列表
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT * FROM files 
                WHERE path LIKE ?
                ORDER BY filename
            """, (f'{folder_path}%',))
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def remove_files_by_folder(self, folder_path: str) -> int:
        """
        删除指定文件夹下的所有文件记录

        Args:
            folder_path: 文件夹路径

        Returns:
            删除的文件数量
        """
        with self.get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM files WHERE path LIKE ?
            """, (f'{folder_path}%',))
            return cursor.rowcount

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
        try:
            with self.get_cursor() as cursor:
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
        try:
            with self.get_cursor() as cursor:
                cursor.execute("""
                    INSERT OR REPLACE INTO files
                    (path, project_id, filename, duration, sample_rate, channels,
                     file_size, peaks_json, tags, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.path,
                    project_id,
                    record.filename,
                    record.duration,
                    record.sample_rate,
                    record.channels,
                    record.file_size,
                    record.peaks_json,
                    record.tags,
                    datetime.now().isoformat()
                ))
            return True
        except Exception as e:
            _get_logger().error(f"添加文件到工程失败 {record.path}: {e}")
            return False

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
                cursor.execute("""
                    INSERT OR REPLACE INTO imported_folder_mappings
                    (project_id, folder_path, folder_name, user_folder_id, file_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
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
            return True
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
            db_path = str(config.BASE_DIR / "db" / "soundmind.db")
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
