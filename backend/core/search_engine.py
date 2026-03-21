# -*- coding: utf-8 -*-
"""
优化后的语义搜索引擎

特性：
1. 三层搜索架构：精确关键词 -> 分词扩展 -> 语义搜索
2. 查询缓存 - 缓存常用查询的 embedding 和结果
3. 异步搜索 - 支持 WebSocket 进度推送
4. 自适应评分 - 根据匹配类型自动调整分数
5. 中文优化 - 支持中文分词和拼音匹配
"""

import os
import re
import time
import logging
import asyncio
import numpy as np
import json
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
    """中文文本处理器 - 使用 UCS 关键词库 + jieba 分词"""

    def __init__(self):
        # 惰性加载 UCS 关键词
        from core.ucs_keywords import UCSKeywordProcessor
        self._ucs_processor = UCSKeywordProcessor()

    def extract_keywords(self, text: str) -> List[str]:
        """提取关键词（使用 UCS 库）"""
        return self._ucs_processor.extract_keywords(text)

    def expand_query(self, query: str) -> List[str]:
        """扩展查询（使用 UCS 库 + 中文分词）"""
        return self._ucs_processor.expand_query_with_tokenization(query)

    def tokenize(self, text: str) -> List[str]:
        """中文分词"""
        return self._ucs_processor.tokenize(text)


class OptimizedAudioSearcher(AudioSearcher):
    """
    优化的音频搜索器 - 支持三层搜索架构

    搜索优先级：
    1. 第1层：精确关键词搜索（文件名、标签、路径完全匹配）
    2. 第2层：分词扩展搜索（中文分词、UCS关键词、中英文同义词）
    3. 第3层：纯语义搜索（CLAP embedding，作为兜底）
    """

    def __init__(self, *args, **kwargs):
        # 使用工程目录而非 db 目录
        persist_directory = kwargs.get('persist_directory')
        if persist_directory is None:
            from config import get_chroma_db_path, CURRENT_PROJECT_ID
            persist_directory = str(get_chroma_db_path(CURRENT_PROJECT_ID))
        kwargs['persist_directory'] = persist_directory

        super().__init__(*args, **kwargs)
        self._query_cache = QueryCache(max_size=100, ttl=3600)
        self._text_processor = ChineseTextProcessor()
        # 加载配置
        self._keyword_boost = getattr(config, 'KEYWORD_BOOST_FACTOR', 1.2)
        self._semantic_decay = getattr(config, 'SEMANTIC_DECAY_FACTOR', 1.0)

    def _compute_adaptive_score(
        self,
        keyword_score: float,
        semantic_score: float,
        match_level: str
    ) -> float:
        """
        自适应评分：根据匹配类型计算最终分数

        Args:
            keyword_score: 关键词匹配分数 (0.0-1.0)
            semantic_score: 语义相似度 (0.0-1.0)
            match_level: 匹配级别 ("exact", "partial", "weak", "none")

        Returns:
            最终评分 (0.0-1.0)
        """
        if match_level == "exact":
            # 精确匹配：1.0，给予最高分
            return 1.0

        elif match_level == "partial":
            # 部分匹配：0.80-0.95，关键词加权
            return 0.80 + min(keyword_score * 0.15, 0.15)

        elif match_level == "weak":
            # 弱匹配：0.60-0.80，关键词和语义混合
            base_score = max(keyword_score * 0.6, semantic_score * 0.4)
            return 0.60 + min(base_score * 0.20, 0.20)

        else:  # "none"
            # 无关键词匹配：纯语义，适当衰减
            return semantic_score * 0.85

    def _keyword_match_score(
        self,
        query: str,
        filename: str,
        metadata: Dict[str, Any],
        is_ucs_expanded: bool = False
    ) -> Tuple[float, str]:
        """
        计算关键词匹配分数（文件名和标签优先）

        Args:
            query: 查询文本
            filename: 文件名
            metadata: 文件元数据
            is_ucs_expanded: 是否是 UCS 扩展的查询词

        Returns:
            (关键词匹配分数, 匹配级别)
            - 分数: 0.0 - 1.0
            - 级别: "exact", "partial", "weak", "none"
        """
        query_lower = query.lower().strip()
        if not query_lower:
            return 0.0, "none"

        scores = []
        match_level = "none"

        # 支持中英文的分词（按空格、下划线、连字符分隔）
        query_tokens = [t.strip() for t in re.split(r'[_\-\s]+', query_lower) if t.strip()]

        # 1. 文件名完全匹配（最高优先级）
        filename_lower = filename.lower()
        filename_base = os.path.splitext(filename_lower)[0]

        # 完全匹配：查询词与文件名完全一致
        if query_lower == filename_base:
            scores.append(1.0)
            match_level = "exact"
        elif query_lower in filename_base:
            # 查询词是文件名的子串
            # 对于 UCS 扩展的短词（如 "hit", "drop"），要求更严格的匹配
            if is_ucs_expanded and len(query_lower) <= 4:
                # 短词需要作为完整单词匹配（前后是分隔符或边界）
                # 构建正则：匹配作为独立单词的查询词
                pattern = r'(^|[_\-\s])' + re.escape(query_lower) + r'($|[_\-\s])'
                if re.search(pattern, filename_base):
                    scores.append(0.95)
                    if match_level != "exact":
                        match_level = "exact"
            else:
                scores.append(0.95)
                if match_level != "exact":
                    match_level = "exact"

        # 文件名包含查询词的大部分（支持中英文）
        if len(query_tokens) > 1:
            matching_tokens = sum(1 for t in query_tokens if t in filename_base)
            match_ratio = matching_tokens / len(query_tokens)
            if matching_tokens == len(query_tokens):
                # 所有词都匹配
                scores.append(0.92)
                if match_level != "exact":
                    match_level = "exact"
            elif match_ratio >= 0.7:
                # 70%以上词匹配
                scores.append(0.85 + match_ratio * 0.05)
                if match_level == "none":
                    match_level = "partial"
            elif match_ratio > 0:
                # 部分词匹配
                scores.append(0.6 + match_ratio * 0.2)
                if match_level == "none":
                    match_level = "weak"
        elif len(query_tokens) == 1:
            # 单 token 匹配
            token = query_tokens[0]
            if token == filename_base:
                scores.append(1.0)
                match_level = "exact"
            elif token in filename_base:
                # 对于 UCS 扩展的短词，要求完整单词匹配
                if is_ucs_expanded and len(token) <= 4:
                    pattern = r'(^|[_\-\s])' + re.escape(token) + r'($|[_\-\s])'
                    if re.search(pattern, filename_base):
                        scores.append(0.88)
                        if match_level != "exact":
                            match_level = "partial"
                else:
                    scores.append(0.88)
                    if match_level != "exact":
                        match_level = "partial"

        # 2. 解析后的文件名描述匹配
        name_description = metadata.get("name_description", "")
        if name_description and query_lower in name_description.lower():
            scores.append(0.9)
            if match_level == "none":
                match_level = "partial"

        # 3. 文件夹路径匹配
        folder_path = metadata.get("folder_path", "")
        if folder_path and query_lower in folder_path.lower():
            scores.append(0.6)
            if match_level == "none":
                match_level = "weak"

        # 4. 元数据标签匹配（ID3标签、BWF标签等）
        metadata_tags_str = metadata.get("metadata_tags", "{}")
        try:
            metadata_tags = json.loads(metadata_tags_str) if metadata_tags_str else {}
            for key, value in metadata_tags.items():
                if isinstance(value, str):
                    value_lower = value.lower()
                    if query_lower in value_lower:
                        scores.append(0.75)
                        if match_level == "none":
                            match_level = "partial"
                        break
        except:
            pass

        # 5. 文件名分词匹配
        parsed_name = metadata.get("parsed_name", "")
        if parsed_name:
            parsed_lower = parsed_name.lower()
            if query_lower in parsed_lower:
                scores.append(0.8)
                if match_level == "none":
                    match_level = "weak"

        return max(scores) if scores else 0.0, match_level

    def _get_all_files(
        self,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """获取所有文件用于关键词搜索（从 SQLite 数据库获取，支持未索引的文件）"""
        try:
            # 从 SQLite 数据库获取文件列表，而不是 ChromaDB
            # 这样可以搜索到未索引的文件（只有元数据，没有向量）
            from core.database import get_db_manager
            db_manager = get_db_manager()
            
            # 获取所有文件记录
            all_records = db_manager.get_all_files()
            
            # 转换为与 ChromaDB 元数据兼容的格式
            all_files = []
            for record in all_records:
                # 从 filename 提取格式（扩展名）
                filename = record.filename
                file_ext = filename.split('.')[-1].lower() if '.' in filename else ""
                
                metadata = {
                    "file_path": record.path,
                    "filename": filename,
                    "duration": record.duration,
                    "format": file_ext,
                    "size": record.file_size,
                    "sample_rate": record.sample_rate,
                    "channels": record.channels,
                    "folder_path": "",  # SQLite 中没有这个字段
                    "parsed_name": "",  # SQLite 中没有这个字段
                    "name_description": "",  # SQLite 中没有这个字段
                    "metadata_tags": record.tags,  # 使用 tags 字段
                }
                
                # 应用过滤条件
                if filters:
                    skip = False
                    for key, condition in filters.items():
                        if key == "duration":
                            # 处理 $gte, $lte 等条件
                            if isinstance(condition, dict):
                                if "$gte" in condition and metadata.get("duration", 0) < condition["$gte"]:
                                    skip = True
                                    break
                                if "$lte" in condition and metadata.get("duration", 0) > condition["$lte"]:
                                    skip = True
                                    break
                            elif metadata.get("duration") != condition:
                                skip = True
                                break
                        elif key in ["sample_rate", "channels"]:
                            if metadata.get(key) != condition:
                                skip = True
                                break
                        elif key == "format":
                            if metadata.get("format", "").lower() != condition.lower():
                                skip = True
                                break
                    
                    if skip:
                        continue
                
                all_files.append(metadata)
            
            logger.info(f"从 SQLite 获取文件列表完成: {len(all_files)} 个文件")
            return all_files
            
        except Exception as e:
            logger.warning(f"从 SQLite 获取文件列表失败: {e}")
            # 如果 SQLite 获取失败，回退到 ChromaDB
            try:
                all_files = []
                offset = 0
                batch_size = 10000
                
                while True:
                    results = self.collection.get(
                        limit=batch_size,
                        offset=offset,
                        where=filters if filters else None
                    )
                    
                    if not results or not results.get("ids"):
                        break
                    
                    for i, file_id in enumerate(results["ids"]):
                        metadata = results["metadatas"][i]
                        all_files.append(metadata)
                    
                    if len(results["ids"]) < batch_size:
                        break
                    
                    offset += batch_size
                    
                    if offset > 1000000:
                        logger.warning(f"文件数量超过100万，停止获取更多文件")
                        break
                
                logger.info(f"从 ChromaDB 获取文件列表完成: {len(all_files)} 个文件")
                return all_files
            except Exception as e2:
                logger.error(f"从 ChromaDB 获取文件列表也失败: {e2}")
                return []

    def _exact_keyword_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """
        第1层：精确关键词搜索

        匹配规则：
        - 文件名完全包含查询词
        - 标签包含查询词
        - 文件夹路径包含查询词
        """
        if not query or not query.strip():
            return []

        all_files = self._get_all_files(filters)
        results = []

        for metadata in all_files:
            filename = metadata.get("filename", "")
            keyword_score, match_level = self._keyword_match_score(query, filename, metadata)

            # 放宽匹配要求：包含 weak 匹配，同时降低分数阈值到 0.3
            if match_level in ("exact", "partial", "weak") and keyword_score >= 0.3:
                final_score = self._compute_adaptive_score(
                    keyword_score, 0.0, match_level
                )

                results.append(SearchResult(
                    file_path=metadata.get("file_path", ""),
                    filename=filename,
                    similarity=final_score,
                    duration=metadata.get("duration", 0.0),
                    format=metadata.get("format", "") or "",
                    metadata={
                        **metadata,
                        "match_level": match_level,
                        "keyword_score": keyword_score,
                        "semantic_score": 0.0,
                        "search_layer": 1
                    }
                ))

        # 按分数排序
        results.sort(key=lambda x: x.similarity, reverse=True)
        return results

    def _expanded_keyword_search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """
        第2层：分词扩展搜索

        使用中文分词 + UCS 关键词扩展进行多路召回
        优化：优先使用原始查询，UCS扩展的结果降低权重
        """
        if not query or not query.strip():
            return []

        # 获取扩展查询词
        expanded_queries = self._text_processor.expand_query(query)
        
        # 标记原始查询
        original_query = query.lower().strip()

        # 如果扩展结果很少（只有原始查询），也尝试使用扩展词本身
        if len(expanded_queries) <= 1:
            # 尝试使用原始查询的分词结果进行搜索
            tokens = self._text_processor.tokenize(query)
            if tokens and len(tokens) > 1:
                expanded_queries.extend(tokens)

        all_files = self._get_all_files(filters)
        results = []

        # 使用字典跟踪每个文件的最佳匹配分数
        file_best_match = {}

        for expanded_query in expanded_queries:
            is_original = expanded_query.lower() == original_query
            is_ucs_expanded = not is_original  # 标记是否是 UCS 扩展的查询
            
            for metadata in all_files:
                file_path = metadata.get("file_path", "")
                filename = metadata.get("filename", "")
                
                keyword_score, match_level = self._keyword_match_score(
                    expanded_query, filename, metadata,
                    is_ucs_expanded=is_ucs_expanded
                )

                # 对于 UCS 扩展的查询，提高匹配门槛
                if is_original:
                    min_score = 0.3  # 原始查询使用较低门槛
                else:
                    min_score = 0.6  # UCS 扩展查询需要更高的匹配度
                    # 对于 UCS 扩展的结果，降低分数权重
                    keyword_score = keyword_score * 0.7

                if match_level in ("exact", "partial", "weak") and keyword_score >= min_score:
                    # 只保留每个文件的最佳匹配
                    if file_path not in file_best_match or file_best_match[file_path][0] < keyword_score:
                        file_best_match[file_path] = (keyword_score, match_level, expanded_query, metadata)

        # 构建结果列表
        for file_path, (keyword_score, match_level, matched_query, metadata) in file_best_match.items():
            filename = metadata.get("filename", "")
            final_score = self._compute_adaptive_score(
                keyword_score, 0.0, match_level
            )

            results.append(SearchResult(
                file_path=file_path,
                filename=filename,
                similarity=final_score,
                duration=metadata.get("duration", 0.0),
                format=metadata.get("format", "") or "",
                metadata={
                    **metadata,
                    "match_level": match_level,
                    "keyword_score": keyword_score,
                    "semantic_score": 0.0,
                    "search_layer": 2,
                    "matched_query": matched_query
                }
            ))

        # 按分数排序
        results.sort(key=lambda x: x.similarity, reverse=True)
        return results

    def _semantic_search(
        self,
        query_embedding: np.ndarray,
        query: str = "",
        top_k: int = 100,
        min_similarity: float = 0.0,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """
        第3层：纯语义搜索（CLAP embedding）

        作为兜底，确保任何查询都有语义相关的结果

        Args:
            query_embedding: 查询的 embedding 向量
            query: 原始查询文本（用于文件名匹配）
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            filters: 过滤条件
        """
        where_clause = filters if filters else None

        # 获取更多结果用于筛选
        search_k = min(top_k * 3, 500)

        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=search_k,
            where=where_clause
        )

        semantic_results = []
        if results and results.get("ids") and len(results["ids"]) > 0:
            ids = results["ids"][0]
            distances = results["distances"][0]
            metadatas = results["metadatas"][0]

            for i, file_id in enumerate(ids):
                distance = distances[i]
                # 使用高斯核函数转换距离为相似度
                semantic_sim = np.exp(-(distance ** 2) / 2.0)

                metadata = metadatas[i]
                filename = metadata.get("filename", "")

                # 计算文件名匹配分数（基于原始查询）
                keyword_score = 0.0
                match_level = "none"
                if query:
                    keyword_score, match_level = self._keyword_match_score(
                        query, filename, metadata
                    )

                # 综合语义相似度和文件名匹配
                if match_level in ("exact", "partial", "weak"):
                    # 如果有文件名匹配，提高分数
                    if match_level == "exact":
                        final_score = 0.95 + min(keyword_score * 0.05, 0.05)
                    elif match_level == "partial":
                        final_score = 0.70 + min(keyword_score * 0.20, 0.20)
                    else:  # weak
                        final_score = keyword_score * 0.5 + semantic_sim * 0.5
                else:
                    # 纯语义搜索
                    final_score = semantic_sim

                if final_score >= min_similarity:
                    semantic_results.append(SearchResult(
                        file_path=metadata.get("file_path", ""),
                        filename=filename,
                        similarity=final_score,
                        duration=metadata.get("duration", 0.0),
                        format=metadata.get("format", "") or "",
                        metadata={
                            **metadata,
                            "match_level": match_level,
                            "keyword_score": keyword_score,
                            "semantic_score": semantic_sim,
                            "search_layer": 3,
                            "distance": distance
                        }
                    ))

        # 按分数排序
        semantic_results.sort(key=lambda x: x.similarity, reverse=True)
        return semantic_results[:top_k]

    def _merge_and_rank(
        self,
        results: List[SearchResult],
        top_k: int,
        min_similarity: float = 0.0
    ) -> List[SearchResult]:
        """
        合并并排序搜索结果

        Args:
            results: 所有搜索结果列表
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值

        Returns:
            去重并排序后的结果
        """
        if not results:
            return []

        # 按文件路径去重，保留最高分数的结果
        seen_paths = {}
        for r in results:
            if r.file_path not in seen_paths or r.similarity > seen_paths[r.file_path].similarity:
                seen_paths[r.file_path] = r

        # 按分数排序
        unique_results = sorted(
            seen_paths.values(),
            key=lambda x: (x.similarity, x.metadata.get("search_layer", 99)),
            reverse=True
        )

        # 过滤低于阈值的
        filtered_results = [r for r in unique_results if r.similarity >= min_similarity]

        return filtered_results[:top_k]

    def _hybrid_search(
        self,
        query: str,
        query_embedding: np.ndarray,
        top_k: int,
        min_similarity: float,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """
        三层混合搜索

        搜索流程：
        1. 第1层：精确关键词搜索
        2. 第2层：分词扩展搜索
        3. 第3层：纯语义搜索（兜底）

        Args:
            query: 查询文本（原始查询，用于文件名匹配）
            query_embedding: 查询的 embedding 向量
            top_k: 返回结果数量
            min_similarity: 最小相似度阈值
            filters: 过滤条件

        Returns:
            搜索结果列表
        """
        all_results = []

        # 第1层：精确关键词搜索（使用原始查询）
        exact_results = self._exact_keyword_search(query, filters)
        all_results.extend(exact_results)
        logger.debug(f"第1层(精确关键词): 找到 {len(exact_results)} 个结果")

        # 第2层：分词扩展搜索（使用原始查询，让它内部扩展）
        expanded_results = self._expanded_keyword_search(query, filters)
        all_results.extend(expanded_results)
        logger.debug(f"第2层(分词扩展): 找到 {len(expanded_results)} 个结果")

        # 第3层：语义搜索（兜底，使用语义相似度）
        semantic_results = self._semantic_search(
            query_embedding, query, top_k, min_similarity, filters
        )
        all_results.extend(semantic_results)
        logger.debug(f"第3层(语义搜索): 找到 {len(semantic_results)} 个结果")

        # 合并去重并排序
        merged_results = self._merge_and_rank(all_results, top_k * 2, min_similarity)
        logger.debug(f"合并后总计: {len(merged_results)} 个结果")

        return merged_results[:top_k]

    def _single_query_semantic_search(
        self,
        query: str,
        query_embedding: np.ndarray,
        top_k: int,
        min_similarity: float,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """
        单查询语义搜索（用于查询扩展的多路召回）

        与 _semantic_search 不同，这里不应用自适应评分，
        直接使用原始语义相似度
        """
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
                distance = distances[i]
                # 使用高斯核函数转换距离为相似度
                semantic_sim = np.exp(-(distance ** 2) / 2.0)

                if semantic_sim < min_similarity:
                    continue

                metadata = metadatas[i]

                search_results.append(SearchResult(
                    file_path=metadata.get("file_path", ""),
                    filename=metadata.get("filename", ""),
                    similarity=semantic_sim,
                    duration=metadata.get("duration", 0.0),
                    format=metadata.get("format", "") or "",
                    metadata={
                        **metadata,
                        "semantic_score": semantic_sim,
                        "keyword_score": 0.0,
                        "matched_query": query
                    }
                ))

        return search_results

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
        stats = {
            "cache_hit": False,
            "query_expansion": False,
            "layers": {"exact": 0, "expanded": 0, "semantic": 0}
        }

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
        
        # 步骤 3: 获取查询扩展
        expanded_queries = self._text_processor.expand_query(query)
        if len(expanded_queries) > 1:
            stats["query_expansion"] = True
            stats["expanded_queries"] = expanded_queries

        all_results = []

        # 步骤 4: 执行搜索
        # 先执行不依赖 embedder 的关键词搜索（第1层和第2层）
        if progress_callback:
            await progress_callback("keyword_searching", 0.3)
        
        # 第1层：精确关键词搜索
        exact_results = self._exact_keyword_search(query, filters)
        all_results.extend(exact_results)
        stats["layers"]["exact"] = len(exact_results)
        logger.info(f"第1层(精确关键词): 找到 {len(exact_results)} 个结果, 查询='{query}'")
        
        # 第2层：分词扩展搜索
        expanded_results = self._expanded_keyword_search(query, filters)
        all_results.extend(expanded_results)
        stats["layers"]["expanded"] = len(expanded_results)
        logger.info(f"第2层(分词扩展): 找到 {len(expanded_results)} 个结果, 查询='{query}'")
        
        # 如果 embedder 不可用，只返回关键词搜索结果
        if embedder is None:
            logger.warning("Embedder 不可用，仅使用关键词搜索")
            
            # 合并去重并排序
            seen_paths = {}
            for r in all_results:
                if r.file_path not in seen_paths or r.similarity > seen_paths[r.file_path].similarity:
                    seen_paths[r.file_path] = r
            
            unique_results = sorted(
                seen_paths.values(),
                key=lambda x: x.similarity,
                reverse=True
            )
            final_results = unique_results[:top_k]
            
            stats["duration"] = time.time() - start_time
            stats["total_found"] = len(unique_results)
            stats["returned"] = len(final_results)
            stats["embedder_available"] = False
            
            return final_results, stats
        
        # 步骤 5: 执行语义搜索（第3层，需要 embedder）
        if progress_callback:
            await progress_callback("generating_embedding", 0.5)

        for i, q in enumerate(expanded_queries):
            try:
                # 在线程池中执行 embedding 生成，避免阻塞事件循环
                import asyncio
                loop = asyncio.get_event_loop()
                query_embedding = await loop.run_in_executor(
                    None, embedder.text_to_embedding, q
                )

                if progress_callback:
                    progress = 0.5 + (i * 0.3 / len(expanded_queries))
                    await progress_callback(f"semantic_searching_{i+1}", progress)

                # 第3层：语义搜索
                results = self._semantic_search(
                    query_embedding=query_embedding,
                    query=q,
                    top_k=top_k,
                    min_similarity=min_similarity,
                    filters=filters
                )

                # 标记结果来源
                for r in results:
                    r.metadata["matched_query"] = q

                all_results.extend(results)

                # 更新统计
                for r in results:
                    layer = r.metadata.get("search_layer", 3)
                    if layer == 1:
                        stats["layers"]["exact"] += 1
                    elif layer == 2:
                        stats["layers"]["expanded"] += 1
                    else:
                        stats["layers"]["semantic"] += 1

            except Exception as e:
                logger.warning(f"查询 '{q}' 失败: {e}")

        # 步骤 6: 合并去重和排序
        if progress_callback:
            await progress_callback("ranking_results", 0.85)

        # 合并所有结果并去重
        seen_paths = {}
        for r in all_results:
            if r.file_path not in seen_paths or r.similarity > seen_paths[r.file_path].similarity:
                seen_paths[r.file_path] = r

        # 按分数排序
        unique_results = sorted(
            seen_paths.values(),
            key=lambda x: x.similarity,
            reverse=True
        )
        final_results = unique_results[:top_k]

        # 步骤 7: 缓存结果
        if progress_callback:
            await progress_callback("caching", 0.95)

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
                    format=metadata.get("format", "") or "",
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
_searcher_lock = asyncio.Lock()


async def get_optimized_searcher(
    persist_directory: Optional[str] = None,
    collection_name: str = "audio_embeddings"
) -> OptimizedAudioSearcher:
    """获取优化的搜索器单例（线程安全）"""
    global _optimized_searcher
    if _optimized_searcher is None:
        async with _searcher_lock:
            # 双重检查锁定模式
            if _optimized_searcher is None:
                _optimized_searcher = OptimizedAudioSearcher(
                    persist_directory=persist_directory,
                    collection_name=collection_name
                )
    return _optimized_searcher


def get_optimized_searcher_sync(
    persist_directory: Optional[str] = None,
    collection_name: str = "audio_embeddings"
) -> OptimizedAudioSearcher:
    """获取优化的搜索器单例（同步版本，用于非异步上下文）"""
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
