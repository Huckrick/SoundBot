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
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    duration REAL DEFAULT 0,
    sample_rate INTEGER DEFAULT 0,
    channels INTEGER DEFAULT 0,
    file_size INTEGER DEFAULT 0,
    peaks_json TEXT,
    tags TEXT DEFAULT '[]',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_filename ON files(filename);
CREATE INDEX IF NOT EXISTS idx_created_at ON files(created_at);
CREATE INDEX IF NOT EXISTS idx_duration ON files(duration);
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
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            self._local.conn.row_factory = sqlite3.Row
            # 启用 WAL 模式提升并发性能
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

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
        """初始化数据库（创建表和索引）"""
        with self.get_cursor() as cursor:
            cursor.executescript(CREATE_TABLE_SQL)

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

    def add_file(self, record: AudioFileRecord) -> bool:
        """
        添加或更新文件记录（INSERT OR REPLACE）

        Args:
            record: 音频文件记录

        Returns:
            是否成功
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute("""
                    INSERT OR REPLACE INTO files 
                    (path, filename, duration, sample_rate, channels, 
                     file_size, peaks_json, tags, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.path,
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

    def file_exists(self, path: str) -> bool:
        """
        检查文件记录是否存在

        Args:
            path: 文件路径

        Returns:
            是否存在
        """
        with self.get_cursor() as cursor:
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
