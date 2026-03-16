# ChromaDB 索引模块

使用 ChromaDB 创建本地向量数据库，保存音频 embedding 和元数据。
支持增量更新（只处理新文件）。
"""

import os
import json
import hashlib
from pathlib import Path
from typing import List, Optional, Dict, Any

import chromadb
from chromadb.config import Settings
import numpy as np

import config
from core.embedder import get_embedder
from core.scanner import AudioScanner


class AudioIndexer:
    """音频向量索引器，使用 ChromaDB 存储音频 embeddings"""

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        collection_name: str = "audio_embeddings"
    ):
        """
        初始化索引器
        
        Args:
            persist_directory: ChromaDB 持久化存储路径
            collection_name: Collection 名称
        """
        if persist_directory is None:
            persist_directory = str(config.get_db_path())
        
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        
        # 确保目���存在
        Path(persist_directory).mkdir(parents=True, exist_ok=True)
        
        # 初始化 ChromaDB 客户端
        self.client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False)
        )
        
        # 获取或创建 collection
        # 使用余弦相似度
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "Audio file embeddings for semantic search"}
        )
        
        # 记录已索引文件的元数据（用于增量更新）
        self.indexed_files_meta: Dict[str, Dict[str, Any]] = {}
        self._load_indexed_meta()
        
        print(f"[Indexer] 初始化完成，Collection: {collection_name}")
        print(f"[Indexer] 已索引文件数量: {len(self.indexed_files_meta)}")

    def _load_indexed_meta(self) -> None:
        """加载已索引文件的元数据"""
        meta_file = Path(self.persist_directory) / "indexed_files_meta.json"
        if meta_file.exists():
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    self.indexed_files_meta = json.load(f)
            except Exception as e:
                print(f"[Indexer] 加载索引元数据失败: {e}")
                self.indexed_files_meta = {}

    def _save_indexed_meta(self) -> None:
        """保存已索引文件的元数据"""
        meta_file = Path(self.persist_directory) / "indexed_files_meta.json"
        try:
            with open(meta_file, 'w', encoding='utf-8') as f:
                json.dump(self.indexed_files_meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Indexer] 保存索引元数据失败: {e}")

    def _get_file_hash(self, file_path: str) -> str:
        """
        获取文件的内容哈希（用于检测文件变化）
        
        Args:
            file_path: 文件路径
            
        Returns:
            MD5 哈希值
        """
        # 使用文件修改时间和大小作为快速哈希
        stat = os.stat(file_path)
        key = f"{stat.st_mtime}_{stat.st_size}"
        return hashlib.md5(key.encode()).hexdigest()

    def _generate_file_id(self, file_path: str) -> str:
        """
        生成文件的唯一 ID
        
        Args:
            file_path: 文件路径
            
        Returns:
            唯一 ID
        """
        return hashlib.sha256(file_path.encode()).hexdigest()[:16]

    def index_audio_files(
        self,
        folder_path: str,
        recursive: bool = True,
        force_reindex: bool = False
    ) -> Dict[str, Any]:
        """
        索引指定文件夹中的所有音频文件
        
        Args:
            folder_path: 要索引的文件夹路径
            recursive: 是否递归扫描子文件夹
            force_reindex: 是否强制重新索引所有文件
            
        Returns:
            索引结果统计
        """
        print(f"[Indexer] 开始索引文件夹: {folder_path}")
        
        # 扫描音频文件
        scanner = AudioScanner()
        audio_files = scanner.scan(folder_path, recursive)
        
        print(f"[Indexer] 找到 {len(audio_files)} 个音频文件")
        
        # 初始化 embedder
        embedder = get_embedder()
        
        # 分类文件：需要新增、需要更新、不需要处理
        to_add = []
        to_update = []
        
        for audio_file in audio_files:
            file_path = audio_file.path
            file_id = self._generate_file_id(file_path)
            file_hash = self._get_file_hash(file_path)
            
            if file_id in self.indexed_files_meta:
                # 文件已存在，检查是否需要更新
                existing_meta = self.indexed_files_meta[file_id]
                if existing_meta.get("hash") != file_hash or force_reindex:
                    to_update.append((file_id, file_path, audio_file))
            else:
                to_add.append((file_id, file_path, audio_file))
        
        print(f"[Indexer] 需要新增: {len(to_add)}, 需要更新: {len(to_update)}")
        
        # 处理新增文件
        added_count = 0
        for file_id, file_path, audio_file in to_add:
            try:
                # 生成 embedding
                embedding = embedder.audio_to_embedding(file_path)
                
                # 准备元数据
                metadata = {
                    "file_path": file_path,
                    "filename": audio_file.filename,
                    "duration": audio_file.duration,
                    "sample_rate": audio_file.sample_rate,
                    "channels": audio_file.channels,
                    "format": audio_file.format,
                    "size": audio_file.size,
                    "hash": self._get_file_hash(file_path)
                }
                
                # 添加到 ChromaDB
                self.collection.add(
                    ids=[file_id],
                    embeddings=[embedding.tolist()],
                    metadatas=[metadata]
                )
                
                # 更新元数据记录
                self.indexed_files_meta[file_id] = metadata
                added_count += 1
                
            except Exception as e:
                print(f"[Indexer] 索引文件失败 {file_path}: {e}")
        
        # 处理需要更新的文件
        updated_count = 0
        for file_id, file_path, audio_file in to_update:
            try:
                # 生成新的 embedding
                embedding = embedder.audio_to_embedding(file_path)
                
                # 准备新的元数据
                metadata = {
                    "file_path": file_path,
                    "filename": audio_file.filename,
                    "duration": audio_file.duration,
                    "sample_rate": audio_file.sample_rate,
                    "channels": audio_file.channels,
                    "format": audio_file.format,
                    "size": audio_file.size,
                    "hash": self._get_file_hash(file_path)
                }
                
                # 更新 ChromaDB
                self.collection.update(
                    ids=[file_id],
                    embeddings=[embedding.tolist()],
                    metadatas=[metadata]
                )
                
                # 更新元数据记录
                self.indexed_files_meta[file_id] = metadata
                updated_count += 1
                
            except Exception as e:
                print(f"[Indexer] 更新索引失败 {file_path}: {e}")
        
        # 保存索引元数据
        self._save_indexed_meta()
        
        result = {
            "total_files": len(audio_files),
            "added": added_count,
            "updated": updated_count,
            "skipped": len(audio_files) - added_count - updated_count,
            "total_indexed": len(self.indexed_files_meta)
        }
        
        print(f"[Indexer] 索引完成: {result}")
        return result

    def add_single_audio(
        self,
        file_path: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        添加单个音频文件到索引
        
        Args:
            file_path: 音频文件路径
            metadata: 额外的元数据
            
        Returns:
            是否成功
        """
        try:
            embedder = get_embedder()
            file_id = self._generate_file_id(file_path)
            
            # 检查是否已存在
            if self.collection.get(ids=[file_id]).get("ids"):
                print(f"[Indexer] 文件已存在: {file_path}")
                return False
            
            # 生成 embedding
            embedding = embedder.audio_to_embedding(file_path)
            
            # 准备元数据
            from core.scanner import AudioScanner
            scanner = AudioScanner()
            audio_info = scanner._process_file(Path(file_path))
            
            if audio_info is None:
                print(f"[Indexer] 无法读取音频文件: {file_path}")
                return False
            
            meta = {
                "file_path": file_path,
                "filename": audio_info.filename,
                "duration": audio_info.duration,
                "sample_rate": audio_info.sample_rate,
                "channels": audio_info.channels,
                "format": audio_info.format,
                "size": audio_info.size,
                "hash": self._get_file_hash(file_path)
            }
            
            if metadata:
                meta.update(metadata)
            
            # 添加到 ChromaDB
            self.collection.add(
                ids=[file_id],
                embeddings=[embedding.tolist()],
                metadatas=[meta]
            )
            
            # 更新元数据记录
            self.indexed_files_meta[file_id] = meta
            self._save_indexed_meta()
            
            print(f"[Indexer] 成功添加: {file_path}")
            return True
            
        except Exception as e:
            print(f"[Indexer] 添加文件失败 {file_path}: {e}")
            return False

    def remove_audio(self, file_path: str) -> bool:
        """
        从索引中移除音频文件
        
        Args:
            file_path: 音频文件路径
            
        Returns:
            是否成功
        """
        try:
            file_id = self._generate_file_id(file_path)
            
            # 检查是否存在
            existing = self.collection.get(ids=[file_id])
            if not existing.get("ids"):
                print(f"[Indexer] 文件不在索引中: {file_path}")
                return False
            
            # 从 ChromaDB 删除
            self.collection.delete(ids=[file_id])
            
            # 更新元数据记录
            if file_id in self.indexed_files_meta:
                del self.indexed_files_meta[file_id]
                self._save_indexed_meta()
            
            print(f"[Indexer] 成功移除: {file_path}")
            return True
            
        except Exception as e:
            print(f"[Indexer] 移除文件失败 {file_path}: {e}")
            return False

    def get_indexed_count(self) -> int:
        """获取已索引的文件数量"""
        return len(self.indexed_files_meta)

    def get_all_indexed_files(self) -> List[Dict[str, Any]]:
        """获取所有已索引文件的元数据"""
        return list(self.indexed_files_meta.values())

    def clear_index(self) -> None:
        """清空所有索引"""
        self.client.delete_collection(name=self.collection_name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"description": "Audio file embeddings for semantic search"}
        )
        self.indexed_files_meta = {}
        self._save_indexed_meta()
        print("[Indexer] 索引已清空")


# 全局单例
_indexer: Optional[AudioIndexer] = None


def get_indexer(persist_directory: Optional[str] = None) -> AudioIndexer:
    """获取 Indexer 单例（延迟加载）"""
    global _indexer
    if _indexer is None:
        _indexer = AudioIndexer(persist_directory=persist_directory)
    return _indexer


def reset_indexer() -> None:
    """重置 Indexer 单例（用于测试或重新初始化）"""
    global _indexer
    _indexer = None
