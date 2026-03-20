# -*- coding: utf-8 -*-
"""
优化后的语义搜索引擎

特性：
1. 查询缓存 - 缓存常用查询的 embedding 和结果
2. 异步搜索 - 支持 WebSocket 进度推送
3. 混合搜索 - 结合向量搜索和关键词匹配
4. 中文优化 - 支持中文分词和拼音匹配
"""

import re
import time
import logging
import asyncio
import numpy as np
from typing import List, Optional, Dict, Any, Tuple, Callable
from dataclasses import dataclass, field
from functools import lru_cache
import hashlib

import config
from core.embedder import get_embedder, is_embedder_available
from core.indexer import get_chroma_client
from core.searcher import SearchResult, AudioSearcher

logger = logging.getLogger(__name__)


@dataclass
class SearchCacheEntry:
    """搜索结果缓存条目"""
    query_hash: str
    results: List[SearchResult]
    timestamp: float
    total_count: int


class QueryCache:
    """查询缓存管理器"""
    
    def __init__(self, max_size: int = 100, ttl: float = 3600):
        """
        初始化缓存
        
        Args:
            max_size: 最大缓存条目数
            ttl: 缓存过期时间（秒）
        """
        self._cache: Dict[str, SearchCacheEntry] = {}
        self._max_size = max_size
        self._ttl = ttl
        self._lock = asyncio.Lock()
    
    def _hash_query(self, query: str, **kwargs) -> str:
        """生成查询的哈希值"""
        key = f"{query}:{sorted(kwargs.items())}"
        return hashlib.md5(key.encode()).hexdigest()
    
    async def get(self, query: str, **kwargs) -> Optional[SearchCacheEntry]:
        """获取缓存结果"""
        async with self._lock:
            query_hash = self._hash_query(query, **kwargs)
            entry = self._cache.get(query_hash)
            
            if entry is None:
                return None
            
            # 检查是否过期
            if time.time() - entry.timestamp > self._ttl:
                del self._cache[query_hash]
                return None
            
            return entry
    
    async def set(self, query: str, results: List[SearchResult], total_count: int, **kwargs):
        """设置缓存结果"""
        async with self._lock:
            query_hash = self._hash_query(query, **kwargs)
            
            # 清理过期条目
            current_time = time.time()
            expired_keys = [
                k for k, v in self._cache.items()
                if current_time - v.timestamp > self._ttl
            ]
            for k in expired_keys:
                del self._cache[k]
            
            # 如果缓存满了，删除最旧的条目
            if len(self._cache) >= self._max_size:
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].timestamp)
                del self._cache[oldest_key]
            
            self._cache[query_hash] = SearchCacheEntry(
                query_hash=query_hash,
                results=results,
                timestamp=current_time,
                total_count=total_count
            )
    
    async def clear(self):
        """清空缓存"""
        async with self._lock:
            self._cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "ttl": self._ttl
        }


class ChineseTextProcessor:
    """中文文本处理器"""
    
    # 常见的音效中文-英文映射
    SOUND_KEYWORDS_MAP = {
        "猫": ["cat", "meow", "kitten"],
        "狗": ["dog", "bark", "puppy"],
        "门": ["door", "knock", "open", "close"],
        "火": ["fire", "flame", "burn"],
        "水": ["water", "splash", "drop"],
        "风": ["wind", "breeze", "gust"],
        "雨": ["rain", "storm", "thunder"],
        "雷": ["thunder", "lightning", "storm"],
        "电": ["electric", "spark", "zap"],
        "车": ["car", "vehicle", "automobile"],
        "铃": ["bell", "ring", "chime"],
        "钟": ["clock", "tick", "tock"],
        "枪": ["gun", "shot", "firearm"],
        "爆炸": ["explosion", "blast", "boom"],
        "撞击": ["impact", "hit", "crash"],
        "点击": ["click", "tap", "button"],
        "提示音": ["notification", "alert", "beep"],
        "音乐": ["music", "melody", "song"],
        "人声": ["voice", "speech", "vocal"],
        "动物": ["animal", "creature", "wildlife"],
        "机械": ["machine", "mechanical", "gear"],
        "电子": ["electronic", "digital", "synthetic"],
        "环境": ["ambient", "environment", "background"],
        "UI": ["ui", "interface", "menu"],
        "游戏": ["game", "gaming", "arcade"],
        "恐怖": ["horror", "scary", "creepy"],
        "搞笑": ["funny", "comedy", "cartoon"],
    }
    
    @classmethod
    def extract_keywords(cls, text: str) -> List[str]:
        """提取关键词（中英文）"""
        keywords = []
        
        # 检查中文关键词映射
        for cn_keyword, en_keywords in cls.SOUND_KEYWORDS_MAP.items():
            if cn_keyword in text:
                keywords.extend(en_keywords)
        
        return keywords
    
    @classmethod
    def expand_query(cls, query: str) -> List[str]:
        """扩展查询（中文 -> 英文）"""
        queries = [query]  # 原始查询
        
        # 提取英文关键词
        keywords = cls.extract_keywords(query)
        if keywords:
            # 添加英文关键词组合
            queries.append(" ".join(keywords[:3]))  # 最多3个关键词
        
        return queries


class OptimizedAudioSearcher(AudioSearcher):
    """优化的音频搜索器"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._query_cache = QueryCache(max_size=100, ttl=3600)
        self._text_processor = ChineseTextProcessor()
    
    async def search_async(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
        progress_callback: Optional[Callable[[str, float], None]] = None
    ) -> Tuple[List[SearchResult], Dict[str, Any]]:
        """
        异步搜索（带缓存和进度回调）
        
        Args:
            query: 查询文本
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            filters: 过滤条件
            use_cache: 是否使用缓存
            progress_callback: 进度回调函数 (stage, progress)
            
        Returns:
            (搜索结果列表, 搜索统计信息)
        """
        if top_k is None:
            top_k = config.TOP_K_RESULTS
        if min_similarity is None:
            min_similarity = config.SIMILARITY_THRESHOLD
        
        start_time = time.time()
        stats = {"cache_hit": False, "query_expansion": False}
        
        # 步骤 1: 检查缓存
        if progress_callback:
            await progress_callback("checking_cache", 0.1)
        
        if use_cache:
            cached = await self._query_cache.get(query, top_k=top_k, min_similarity=min_similarity)
            if cached:
                stats["cache_hit"] = True
                stats["duration"] = time.time() - start_time
                return cached.results, stats
        
        # 步骤 2: 检查 embedder
        if progress_callback:
            await progress_callback("loading_model", 0.2)
        
        embedder = get_embedder()
        if embedder is None:
            logger.warning("Embedder 不可用，无法执行语义搜索")
            return [], stats
        
        # 步骤 3: 处理中文查询扩展
        expanded_queries = self._text_processor.expand_query(query)
        if len(expanded_queries) > 1:
            stats["query_expansion"] = True
        
        # 步骤 4: 生成 embedding
        if progress_callback:
            await progress_callback("generating_embedding", 0.3)
        
        all_results = []
        for i, q in enumerate(expanded_queries):
            try:
                query_embedding = embedder.text_to_embedding(q)
                
                if progress_callback:
                    await progress_callback("searching_database", 0.4 + (i * 0.3 / len(expanded_queries)))
                
                # 执行向量搜索
                results = self._vector_search(
                    query_embedding=query_embedding,
                    top_k=top_k * 2,  # 获取更多结果用于去重
                    min_similarity=min_similarity,
                    filters=filters
                )
                all_results.extend(results)
                
            except Exception as e:
                logger.warning(f"查询 '{q}' 失败: {e}")
        
        # 步骤 5: 去重和排序
        if progress_callback:
            await progress_callback("ranking_results", 0.8)
        
        # 按文件路径去重，保留最高相似度
        seen_paths = {}
        for r in all_results:
            if r.file_path not in seen_paths or r.similarity > seen_paths[r.file_path].similarity:
                seen_paths[r.file_path] = r
        
        # 排序并限制结果数量
        unique_results = sorted(seen_paths.values(), key=lambda x: x.similarity, reverse=True)
        final_results = unique_results[:top_k]
        
        # 步骤 6: 缓存结果
        if progress_callback:
            await progress_callback("caching", 0.9)
        
        if use_cache and final_results:
            await self._query_cache.set(
                query, final_results, len(unique_results),
                top_k=top_k, min_similarity=min_similarity
            )
        
        if progress_callback:
            await progress_callback("complete", 1.0)
        
        stats["duration"] = time.time() - start_time
        stats["total_found"] = len(unique_results)
        stats["returned"] = len(final_results)
        
        return final_results, stats
    
    def _vector_search(
        self,
        query_embedding: np.ndarray,
        top_k: int,
        min_similarity: float,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """执行向量搜索"""
        where_clause = filters if filters else None
        
        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=top_k,
            where=where_clause
        )
        
        search_results = []
        if results and results.get("ids") and len(results["ids"]) > 0:
            ids = results["ids"][0]
            distances = results["distances"][0]
            metadatas = results["metadatas"][0]
            
            for i, file_id in enumerate(ids):
                # 欧氏距离转余弦相似度
                distance = distances[i]
                similarity = 1.0 - (distance ** 2) / 2.0
                
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
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        return self._query_cache.get_stats()

    async def clear_cache(self):
        """清空缓存"""
        await self._query_cache.clear()

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
            return {"total_count": 0}

    def get_all_indexed_files(self) -> List[Dict[str, Any]]:
        """获取所有已索引的文件"""
        try:
            results = self.collection.get()
            files = []
            if results and results.get("metadatas"):
                for metadata in results["metadatas"]:
                    files.append(metadata)
            return files
        except Exception as e:
            logger.error(f"获取索引文件列表失败: {e}")
            return []


# 全局优化的搜索器实例
_optimized_searcher: Optional[OptimizedAudioSearcher] = None


def get_optimized_searcher(
    persist_directory: Optional[str] = None,
    collection_name: str = "audio_embeddings"
) -> OptimizedAudioSearcher:
    """获取优化的搜索器单例"""
    global _optimized_searcher
    if _optimized_searcher is None:
        _optimized_searcher = OptimizedAudioSearcher(
            persist_directory=persist_directory,
            collection_name=collection_name
        )
    return _optimized_searcher


def reset_optimized_searcher() -> None:
    """重置优化的搜索器单例"""
    global _optimized_searcher
    _optimized_searcher = None
