# -*- coding: utf-8 -*-
# ChromaDB 索引模块

"""使用 ChromaDB 创建本地向量数据库，保存音频 embedding 和元数据。支持增量更新（只处理新文件）。"""

import os
import json
import hashlib
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import chromadb
from chromadb.config import Settings
import numpy as np

import config
from core.embedder import get_embedder, is_embedder_available
from core.scanner import AudioScanner

logger = logging.getLogger(__name__)


# 全局 ChromaDB 客户端（按路径缓存）
_chroma_clients: Dict[str, chromadb.PersistentClient] = {}


def get_chroma_client(persist_directory: Optional[str] = None) -> chromadb.PersistentClient:
    """获取 ChromaDB 客户端（按路径缓存）"""
    global _chroma_clients
    if persist_directory is None:
        persist_directory = str(config.get_db_path())

    # 按路径缓存客户端，不同工程使用不同客户端
    if persist_directory not in _chroma_clients:
        Path(persist_directory).mkdir(parents=True, exist_ok=True)
        # 配置 ChromaDB 使用隔离的 SQLite 设置
        _chroma_clients[persist_directory] = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(
                anonymized_telemetry=False,
                is_persistent=True
            )
        )
    return _chroma_clients[persist_directory]


def reset_chroma_client(persist_directory: Optional[str] = None) -> None:
    """重置 ChromaDB 客户端（用于测试或重新初始化）"""
    global _chroma_clients
    if persist_directory is None:
        # 重置所有客户端
        _chroma_clients.clear()
    elif persist_directory in _chroma_clients:
        del _chroma_clients[persist_directory]


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
        
        # 确保目录存在
        Path(persist_directory).mkdir(parents=True, exist_ok=True)
        
        # 使用全局 ChromaDB 客户端
        self.client = get_chroma_client(persist_directory)
        
        # 获取或创建 collection
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"description": "Audio file embeddings for semantic search"}
        )
        
        # 记录已索引文件的元数据（用于增量更新）
        self.indexed_files_meta: Dict[str, Dict[str, Any]] = {}
        self._load_indexed_meta()

        logger.info(f"Indexer 初始化完成，Collection: {collection_name}")
        logger.info(f"已索引文件数量: {len(self.indexed_files_meta)}")

    def _load_indexed_meta(self) -> None:
        """加载已索引文件的元数据"""
        meta_file = Path(self.persist_directory) / "indexed_files_meta.json"
        if meta_file.exists():
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    self.indexed_files_meta = json.load(f)
            except Exception as e:
                logger.warning(f"加载索引元数据失败: {e}")
                self.indexed_files_meta = {}

    def _save_indexed_meta(self) -> None:
        """保存已索引文件的元数据"""
        meta_file = Path(self.persist_directory) / "indexed_files_meta.json"
        try:
            with open(meta_file, 'w', encoding='utf-8') as f:
                json.dump(self.indexed_files_meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存索引元数据失败: {e}")

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

    def _batch_process_files(
        self,
        files_to_process: List[tuple],
        embedder,
        is_update: bool = False,
        batch_size: int = 32,
        max_workers: int = 4
    ) -> int:
        """
        批量处理文件（并行生成 embedding，批量写入 ChromaDB）
        
        Args:
            files_to_process: 待处理文件列表 [(file_id, file_path, audio_file), ...]
            embedder: embedding 生成器
            is_update: 是否为更新操作
            batch_size: 批量大小
            max_workers: 并行线程数
            
        Returns:
            成功处理的文件数量
        """
        if not files_to_process or embedder is None:
            return 0
        
        total = len(files_to_process)
        processed = 0
        failed = 0
        start_time = time.time()
        
        logger.info(f"[BATCH] 开始批量处理 {total} 个文件, 批次大小: {batch_size}, 并行数: {max_workers}")
        
        # 分批处理
        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch = files_to_process[batch_start:batch_end]
            
            # 并行生成 embedding
            results = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_file = {
                    executor.submit(self._generate_embedding_for_file, file_id, file_path, audio_file, embedder): 
                    (file_id, file_path, audio_file) 
                    for file_id, file_path, audio_file in batch
                }
                
                for future in as_completed(future_to_file):
                    result = future.result()
                    if result:
                        results.append(result)
                    else:
                        failed += 1
            
            # 批量写入 ChromaDB
            if results:
                try:
                    ids = [r['file_id'] for r in results]
                    embeddings = [r['embedding'] for r in results]
                    metadatas = [r['metadata'] for r in results]
                    
                    if is_update:
                        self.collection.update(ids=ids, embeddings=embeddings, metadatas=metadatas)
                    else:
                        self.collection.add(ids=ids, embeddings=embeddings, metadatas=metadatas)
                    
                    # 更新元数据记录
                    for r in results:
                        self.indexed_files_meta[r['file_id']] = r['metadata']
                    
                    processed += len(results)
                    
                except Exception as e:
                    logger.error(f"[BATCH] 批量写入 ChromaDB 失败: {e}")
                    failed += len(results)
            
            # 进度报告
            elapsed = time.time() - start_time
            speed = (batch_end) / elapsed if elapsed > 0 else 0
            logger.info(f"[BATCH] 进度: {batch_end}/{total} ({batch_end/total*100:.1f}%), 速度: {speed:.1f} 文件/秒")
            print(f"[BATCH] 进度: {batch_end}/{total} ({batch_end/total*100:.1f}%), 速度: {speed:.1f} 文件/秒", flush=True)
        
        total_time = time.time() - start_time
        logger.info(f"[BATCH] 批量处理完成: 成功 {processed}, 失败 {failed}, 耗时 {total_time:.2f} 秒, 平均速度: {processed/total_time:.1f} 文件/秒")
        
        return processed

    def _generate_embedding_for_file(self, file_id: str, file_path: str, audio_file, embedder) -> Optional[Dict]:
        """为单个文件生成 embedding 和元数据"""
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
                "hash": self._get_file_hash(file_path),
                "folder_path": audio_file.folder_path,
                "parsed_name": audio_file.parsed_name,
                "name_description": audio_file.name_description,
                "metadata_tags": json.dumps(audio_file.metadata_tags, ensure_ascii=False) if audio_file.metadata_tags else "{}"
            }
            
            return {
                'file_id': file_id,
                'embedding': embedding.tolist(),
                'metadata': metadata
            }
        except Exception as e:
            logger.error(f"生成 embedding 失败 {file_path}: {e}")
            return None

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
        logger.info(f"[INDEXER] 开始索引文件夹: {folder_path}")
        print(f"[INDEXER] 开始索引文件夹: {folder_path}", flush=True)

        # 扫描音频文件
        scanner = AudioScanner()
        logger.info(f"[INDEXER] 调用 scanner.scan...")
        print(f"[INDEXER] 调用 scanner.scan...", flush=True)
        audio_files = scanner.scan(folder_path, recursive)

        logger.info(f"[INDEXER] 扫描完成，找到 {len(audio_files)} 个音频文件")
        print(f"[INDEXER] 扫描完成，找到 {len(audio_files)} 个音频文件", flush=True)

        # 尝试初始化 embedder，如果失败则跳过 embedding
        embedder = None
        if is_embedder_available():
            embedder = get_embedder()
        else:
            logger.warning("Embedder 不可用，等待模型加载...")
            # 等待模型加载完成（最多等待60秒）
            import time
            wait_time = 0
            max_wait = 60
            while wait_time < max_wait:
                if is_embedder_available():
                    embedder = get_embedder()
                    logger.info(f"模型加载完成，等待了 {wait_time} 秒")
                    break
                time.sleep(1)
                wait_time += 1
                if wait_time % 10 == 0:
                    logger.info(f"等待模型加载... {wait_time}/{max_wait} 秒")
            
            if embedder is None:
                logger.error(f"模型加载超时（{max_wait}秒），将只扫描文件，不生成语义索引")

        # 如果没有 embedder，直接返回扫描结果（不建索引）
        if embedder is None:
            logger.info(f"跳过索引建立，直接返回 {len(audio_files)} 个文件")
            return {
                "added": 0,
                "updated": 0,
                "skipped": len(audio_files),
                "files": [f.path for f in audio_files]
            }
        
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
        
        logger.info(f"需要新增: {len(to_add)}, 需要更新: {len(to_update)}")
        
        # 批量处理新增文件
        added_count = self._batch_process_files(to_add, embedder, is_update=False)
        
        # 批量处理更新文件
        updated_count = self._batch_process_files(to_update, embedder, is_update=True)
        
        logger.info(f"批量处理完成: 新增 {added_count}, 更新 {updated_count}")
        
        # 保存索引元数据
        self._save_indexed_meta()
        
        result = {
            "total_files": len(audio_files),
            "added": added_count,
            "updated": updated_count,
            "skipped": len(audio_files) - added_count - updated_count,
            "total_indexed": len(self.indexed_files_meta)
        }
        
        logger.info(f"索引完成: {result}")
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
                logger.debug(f"文件已存在: {file_path}")
                return False
            
            # 生成 embedding
            embedding = embedder.audio_to_embedding(file_path)
            
            # 准备元数据
            from core.scanner import AudioScanner
            scanner = AudioScanner()
            audio_info = scanner._process_file(Path(file_path))
            
            if audio_info is None:
                logger.error(f"无法读取音频文件: {file_path}")
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
            
            logger.info(f"成功添加: {file_path}")
            return True

        except Exception as e:
            logger.error(f"添加文件失败 {file_path}: {e}")
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
                logger.debug(f"文件不在索引中: {file_path}")
                return False
            
            # 从 ChromaDB 删除
            self.collection.delete(ids=[file_id])
            
            # 更新元数据记录
            if file_id in self.indexed_files_meta:
                del self.indexed_files_meta[file_id]
                self._save_indexed_meta()
            
            logger.info(f"成功移除: {file_path}")
            return True

        except Exception as e:
            logger.error(f"移除文件失败 {file_path}: {e}")
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
        logger.info("索引已清空")


# 全局索引器实例字典（按工程ID存储）
_indexers: Dict[str, AudioIndexer] = {}


def get_indexer(project_id: str = None) -> AudioIndexer:
    """
    获取指定工程的 Indexer 实例

    Args:
        project_id: 工程ID，默认为当前激活的工程

    Returns:
        AudioIndexer 实例
    """
    import config

    if project_id is None:
        project_id = config.CURRENT_PROJECT_ID

    global _indexers
    if project_id not in _indexers:
        persist_directory = str(config.get_chroma_db_path(project_id))
        _indexers[project_id] = AudioIndexer(persist_directory=persist_directory)

    return _indexers[project_id]


def reset_indexer(project_id: str = None) -> None:
    """
    重置指定工程的 Indexer 实例

    Args:
        project_id: 工程ID，默认为当前激活的工程
    """
    import config

    if project_id is None:
        project_id = config.CURRENT_PROJECT_ID

    global _indexers
    if project_id in _indexers:
        del _indexers[project_id]


def reset_all_indexers() -> None:
    """重置所有工程的 Indexer 实例"""
    global _indexers
    _indexers = {}


def delete_project_index(project_id: str) -> bool:
    """
    删除指定工程的向量数据库

    Args:
        project_id: 工程ID

    Returns:
        是否成功删除
    """
    import shutil
    import config

    try:
        # 先重置该工程的索引器实例
        reset_indexer(project_id)

        # 删除向量数据库目录
        db_path = config.get_chroma_db_path(project_id)
        if db_path.exists():
            shutil.rmtree(db_path)
            logger.info(f"已删除工程 {project_id} 的向量数据库: {db_path}")

        # 清理 ChromaDB 客户端缓存
        reset_chroma_client(str(db_path))
        logger.info(f"已清理工程 {project_id} 的 ChromaDB 客户端缓存")

        return True
    except Exception as e:
        logger.error(f"删除工程 {project_id} 的向量数据库失败: {e}")
        return False
