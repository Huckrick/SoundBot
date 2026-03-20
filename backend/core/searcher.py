# -*- coding: utf-8 -*-
# 语义搜索模块

"""接收文本查询，生成 embedding，在 ChromaDB 中搜索最相似的音频。返回 Top-K 结果，包含文件路径和相似度分数。"""

import logging
from typing import List, Optional, Dict, Any

import numpy as np
from pydantic import BaseModel

import config
from core.embedder import get_embedder, is_embedder_available
from core.indexer import get_chroma_client

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    """搜索结果模型"""
    file_path: str
    filename: str
    similarity: float
    duration: float
    format: str
    metadata: Dict[str, Any]


class AudioSearcher:
    """音频语义搜索器"""

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        collection_name: str = "audio_embeddings"
    ):
        """
        初始化搜索器
        
        Args:
            persist_directory: ChromaDB 持久化存储路径
            collection_name: Collection 名称
        """
        if persist_directory is None:
            persist_directory = str(config.get_chroma_db_path())
        
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        
        # 使用全局 ChromaDB 客户端（与 Indexer 共用）
        self.client = get_chroma_client(persist_directory)
        
        # 获取或创建 collection（确保 collection 存在）
        try:
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"description": "Audio file embeddings for semantic search"}
            )
        except Exception as e:
            raise RuntimeError(f"无法获取或创建 collection '{collection_name}': {e}")
        
        logger.info(f"Searcher 初始化完成，Collection: {collection_name}")

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """
        使用文本查询搜索音频
        
        Args:
            query: 文本查询（如"铃铛声"）
            top_k: 返回结果数量，None 时使用配置默认值
            min_similarity: 最小相似度阈值，None 时使用配置默认值
            filters: 额外的过滤条件（如 format='wav'）
            
        Returns:
            搜索结果列表
        """
        if top_k is None:
            top_k = config.TOP_K_RESULTS
        
        if min_similarity is None:
            min_similarity = config.SIMILARITY_THRESHOLD
        
        logger.info(f"搜索: '{query}', top_k={top_k}")

        # 检查 embedder 是否可用
        embedder = get_embedder()
        if embedder is None:
            logger.warning("Embedder 不可用，无法执行语义搜索")
            return []

        # 生成查询的 embedding
        query_embedding = embedder.text_to_embedding(query)
        
        # 在 ChromaDB 中搜索
        where_clause = None
        if filters:
            where_clause = filters
        
        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=top_k,
            where=where_clause
        )
        
        # 解析结果
        search_results = []
        if results and results.get("ids") and len(results["ids"]) > 0:
            ids = results["ids"][0]
            distances = results["distances"][0]
            metadatas = results["metadatas"][0]
            
            for i, file_id in enumerate(ids):
                distance = distances[i]
                similarity = np.exp(-(distance ** 2) / 2.0)

                if similarity < min_similarity:
                    continue

                metadata = metadatas[i]

                search_results.append(SearchResult(
                    file_path=metadata.get("file_path", ""),
                    filename=metadata.get("filename", ""),
                    similarity=similarity,
                    duration=metadata.get("duration", 0.0),
                    format=metadata.get("format", ""),
                    metadata=metadata
                ))
        
        logger.info(f"找到 {len(search_results)} 个结果")
        return search_results

    def search_by_embedding(
        self,
        query_embedding: np.ndarray,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """
        使用已有的 embedding 向量搜索音频
        
        Args:
            query_embedding: 查询 embedding 向量
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            filters: 额外的过滤条件
            
        Returns:
            搜索��果列表
        """
        if top_k is None:
            top_k = config.TOP_K_RESULTS
        
        if min_similarity is None:
            min_similarity = config.SIMILARITY_THRESHOLD
        
        # 在 ChromaDB 中搜索
        where_clause = None
        if filters:
            where_clause = filters
        
        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=top_k,
            where=where_clause
        )
        
        # 解析结果
        search_results = []
        if results and results.get("ids") and len(results["ids"]) > 0:
            ids = results["ids"][0]
            distances = results["distances"][0]
            metadatas = results["metadatas"][0]
            
            for i, file_id in enumerate(ids):
                distance = distances[i]
                similarity = np.exp(-(distance ** 2) / 2.0)

                if similarity < min_similarity:
                    continue

                metadata = metadatas[i]

                search_results.append(SearchResult(
                    file_path=metadata.get("file_path", ""),
                    filename=metadata.get("filename", ""),
                    similarity=similarity,
                    duration=metadata.get("duration", 0.0),
                    format=metadata.get("format", ""),
                    metadata=metadata
                ))

        return search_results

    def search_audio_to_audio(
        self,
        audio_query_path: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None
    ) -> List[SearchResult]:
        """
        使用音频文件作为查询搜索相似音频
        
        Args:
            audio_query_path: 查询音频文件路径
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            
        Returns:
            搜索结果列表
        """
        if top_k is None:
            top_k = config.TOP_K_RESULTS
        
        if min_similarity is None:
            min_similarity = config.SIMILARITY_THRESHOLD
        
        # 生成音频的 embedding
        embedder = get_embedder()
        query_embedding = embedder.audio_to_embedding(audio_query_path)
        
        # 搜索
        return self.search_by_embedding(
            query_embedding=query_embedding,
            top_k=top_k,
            min_similarity=min_similarity
        )

    def get_all_indexed_files(self) -> List[Dict[str, Any]]:
        """获取所有已索引的文件信息"""
        try:
            results = self.collection.get()
            
            if not results or not results.get("ids"):
                return []
            
            files = []
            for i, file_id in enumerate(results["ids"]):
                metadata = results["metadatas"][i]
                files.append({
                    "id": file_id,
                    "file_path": metadata.get("file_path", ""),
                    "filename": metadata.get("filename", ""),
                    "duration": metadata.get("duration", 0.0),
                    "format": metadata.get("format", "")
                })
            
            return files
            
        except Exception as e:
            logger.error(f"获取索引文件列表失败: {e}")
            return []

    def get_collection_stats(self) -> Dict[str, Any]:
        """获取 Collection 统计信息"""
        try:
            count = self.collection.count()
            return {
                "total_count": count,
                "collection_name": self.collection_name,
                "persist_directory": self.persist_directory
            }
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {}


# 全局单例
_searcher: Optional[AudioSearcher] = None


def get_searcher(
    persist_directory: Optional[str] = None,
    collection_name: str = "audio_embeddings"
) -> AudioSearcher:
    """获取 Searcher 单例（延迟加载）"""
    global _searcher
    if _searcher is None:
        _searcher = AudioSearcher(
            persist_directory=persist_directory,
            collection_name=collection_name
        )
    return _searcher


def reset_searcher() -> None:
    """重置 Searcher 单例（用于测试或重新初始化）"""
    global _searcher
    _searcher = None
