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
import threading
from pathlib import Path

import config
from core.embedder import (
    peek_embedder,
    get_embedder_fingerprint,
    get_text_embedding_config_fingerprint,
)
from core.indexer import get_collection_revision, safe_project_chroma_path
from core.searcher import (
    SearchResult,
    AudioSearcher,
    MetadataTextSearcher,
    build_chroma_where,
    cosine_similarity_from_distance,
)

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
        # JSON canonicalisation keeps nested filters stable regardless of dict
        # insertion order.  Project/revision/model context is supplied by the
        # caller and therefore participates in every key.
        payload = {"query": query, **kwargs}
        key = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

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
        self.project_id = str(kwargs.pop("project_id", None) or config.CURRENT_PROJECT_ID)
        self.index_revision = int(kwargs.pop("index_revision", 0))
        self.model_fingerprint = kwargs.pop("model_fingerprint", None)
        self.text_collection_name = kwargs.pop(
            "text_collection_name", "text_metadata_embeddings"
        )
        # 使用工程目录而非 db 目录
        persist_directory = kwargs.get('persist_directory')
        if persist_directory is None:
            persist_directory = str(safe_project_chroma_path(self.project_id))
        kwargs['persist_directory'] = persist_directory
        kwargs['project_id'] = self.project_id
        kwargs['index_revision'] = self.index_revision
        kwargs['model_fingerprint'] = self.model_fingerprint

        super().__init__(*args, **kwargs)
        self._text_searcher = MetadataTextSearcher(
            persist_directory=persist_directory,
            collection_name=self.text_collection_name,
            project_id=self.project_id,
        )
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
        metadata: Dict[str, Any]
    ) -> Tuple[float, str]:
        """
        计算关键词匹配分数（文件名和标签优先）

        Args:
            query: 查询文本
            filename: 文件名
            metadata: 文件元数据

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
        raw_metadata_tags = metadata.get("metadata_tags", {})
        try:
            metadata_tags = (
                json.loads(raw_metadata_tags)
                if isinstance(raw_metadata_tags, str) and raw_metadata_tags.strip()
                else raw_metadata_tags
            )
        except (TypeError, json.JSONDecodeError):
            metadata_tags = raw_metadata_tags
        if isinstance(metadata_tags, dict):
            searchable_tags = [*metadata_tags.keys(), *metadata_tags.values()]
        elif isinstance(metadata_tags, (list, tuple, set)):
            searchable_tags = list(metadata_tags)
        elif metadata_tags:
            searchable_tags = [metadata_tags]
        else:
            searchable_tags = []
        if any(query_lower in str(value).lower() for value in searchable_tags):
            scores.append(0.75)
            if match_level == "none":
                match_level = "partial"

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
            
            # 获取当前工程的文件记录，避免关键词搜索跨工程串数据
            all_records = db_manager.get_files_by_project(self.project_id)
            
            # 转换为与 ChromaDB 元数据兼容的格式
            all_files = []
            for record in all_records:
                # 从 filename 提取格式（扩展名）
                filename = record.filename
                file_ext = filename.split('.')[-1].lower() if '.' in filename else ""
                
                metadata = {
                    "file_id": record.file_uuid,
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
                        where=build_chroma_where(filters)
                    )
                    
                    if not results or not results.get("ids"):
                        break
                    
                    for i, file_id in enumerate(results["ids"]):
                        metadata = dict(results["metadatas"][i] or {})
                        metadata["file_id"] = str(file_id)
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
        filters: Optional[Dict[str, Any]] = None,
        files_snapshot: Optional[List[Dict[str, Any]]] = None,
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

        all_files = files_snapshot if files_snapshot is not None else self._get_all_files(filters)
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
        filters: Optional[Dict[str, Any]] = None,
        files_snapshot: Optional[List[Dict[str, Any]]] = None,
        expanded_queries: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        """
        第2层：分词扩展搜索

        使用中文分词 + UCS 关键词扩展进行多路召回
        """
        if not query or not query.strip():
            return []

        # 获取扩展查询词
        expanded_queries = list(
            expanded_queries
            if expanded_queries is not None
            else self._text_processor.expand_query(query)
        )

        # 如果扩展结果很少（只有原始查询），也尝试使用扩展词本身
        # 这样可以搜索文件名包含查询词的文件
        if len(expanded_queries) <= 1:
            # 尝试使用原始查询的分词结果进行搜索
            tokens = self._text_processor.tokenize(query)
            if tokens and len(tokens) > 1:
                expanded_queries.extend(tokens)
            # 不再直接返回空列表，而是继续使用原始查询进行搜索

        all_files = files_snapshot if files_snapshot is not None else self._get_all_files(filters)
        results = []

        # 使用集合跟踪已处理的文件，避免重复
        processed_paths = set()

        for expanded_query in expanded_queries:
            # 对于英文单词等没有扩展的查询，也需要搜索原始查询
            is_original = expanded_query.lower() == query.lower()
            
            # 不再跳过原始查询，确保原始查询的精确匹配结果被包含
            # 即使与第1层有重复，后续会去重并保留最高分数

            for metadata in all_files:
                file_path = metadata.get("file_path", "")

                # 避免重复处理同一文件
                if file_path in processed_paths:
                    continue

                filename = metadata.get("filename", "")
                keyword_score, match_level = self._keyword_match_score(
                    expanded_query, filename, metadata
                )

                # 对于无扩展的查询，降低匹配要求
                min_score = 0.3 if len(expanded_queries) <= 1 else 0.5
                if match_level in ("exact", "partial", "weak") and keyword_score >= min_score:
                    processed_paths.add(file_path)
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
                            "matched_query": expanded_query
                        }
                    ))

        # 按分数排序
        results.sort(key=lambda x: x.similarity, reverse=True)
        return results

    def _keyword_search_from_snapshot(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[str], List[SearchResult], List[SearchResult]]:
        """Expand and match a query against one immutable metadata snapshot.

        The caller may run this entire method in a worker thread so both the
        SQLite read and O(files × expanded terms) matching stay off the event
        loop.  Exact and expanded branches intentionally share the same list.
        """
        expanded_queries = list(self._text_processor.expand_query(query))
        if query not in expanded_queries:
            expanded_queries.insert(0, query)
        files_snapshot = self._get_all_files(filters)
        exact_results = self._exact_keyword_search(
            query, filters, files_snapshot=files_snapshot
        )
        expanded_results = self._expanded_keyword_search(
            query,
            filters,
            files_snapshot=files_snapshot,
            expanded_queries=expanded_queries,
        )
        return expanded_queries, exact_results, expanded_results

    def _ready_vector_results(
        self,
        results: List[SearchResult],
        kind: str,
    ) -> List[SearchResult]:
        """Exclude Chroma rows SQLite has marked pending/failed/stale."""
        if not results:
            return []
        from core.database import get_db_manager

        ready = get_db_manager().get_ready_artifact_ids(
            self.project_id,
            kind,
            (result.metadata.get("file_id") for result in results),
        )
        return [
            result for result in results
            if str(result.metadata.get("file_id") or "") in ready
        ]

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
        where_clause = build_chroma_where(filters)

        # 获取更多结果用于筛选
        collection_count = int(self.collection.count())
        if collection_count <= 0 or self.needs_rebuild:
            return []
        search_k = min(top_k * 3, 500, collection_count)

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
                semantic_sim = cosine_similarity_from_distance(distance)

                metadata = dict(metadatas[i] or {})
                metadata["file_id"] = str(file_id)
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

        # Both keyword layers share one SQLite/Chroma metadata snapshot.
        _, exact_results, expanded_results = self._keyword_search_from_snapshot(
            query, filters
        )
        all_results.extend(exact_results)
        logger.debug(f"第1层(精确关键词): 找到 {len(exact_results)} 个结果")

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
        where_clause = build_chroma_where(filters)

        collection_count = int(self.collection.count())
        if collection_count <= 0 or self.needs_rebuild:
            return []

        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=min(top_k, collection_count),
            where=where_clause
        )

        search_results = []
        if results and results.get("ids") and len(results["ids"]) > 0:
            ids = results["ids"][0]
            distances = results["distances"][0]
            metadatas = results["metadatas"][0]

            for i, file_id in enumerate(ids):
                distance = distances[i]
                semantic_sim = cosine_similarity_from_distance(distance)

                if semantic_sim < min_similarity:
                    continue

                metadata = dict(metadatas[i] or {})
                metadata["file_id"] = str(file_id)

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

    @staticmethod
    def _configured_text_fingerprint() -> str:
        """Fingerprint provider/model configuration without hashing secrets."""
        try:
            return get_text_embedding_config_fingerprint()
        except Exception:
            return "text-config:unknown"

    def _weighted_merge_results(
        self,
        keyword_results: List[SearchResult],
        audio_results: List[SearchResult],
        text_results: List[SearchResult],
        *,
        top_k: int,
        min_similarity: float,
        audio_available: bool,
        text_available: bool,
    ) -> Tuple[List[SearchResult], Dict[str, float]]:
        """Merge branches using the v0.2 weights and expose component scores."""
        configured = {"audio": 0.55, "text": 0.30, "keyword": 0.15}
        enabled = {
            "audio": audio_available,
            "text": text_available,
            "keyword": True,
        }
        weight_total = sum(configured[name] for name, active in enabled.items() if active)
        weights = {
            name: (configured[name] / weight_total if active and weight_total else 0.0)
            for name, active in enabled.items()
        }

        candidates: Dict[str, Dict[str, Any]] = {}

        def add(result: SearchResult, component: str, score: float) -> None:
            key = result.file_path or f"filename:{result.filename}"
            entry = candidates.setdefault(
                key,
                {
                    "result": result,
                    "audio": 0.0,
                    "text": 0.0,
                    "keyword": 0.0,
                },
            )
            entry[component] = max(entry[component], max(0.0, float(score)))
            # Prefer the richer record while preserving all score metadata.
            if len(result.metadata) > len(entry["result"].metadata):
                entry["result"] = result
            else:
                entry["result"].metadata.update(result.metadata)

        for result in keyword_results:
            add(
                result,
                "keyword",
                result.metadata.get("keyword_score", result.similarity),
            )
        for result in audio_results:
            add(
                result,
                "audio",
                result.metadata.get("audio_score", result.metadata.get("semantic_score", result.similarity)),
            )
        for result in text_results:
            add(result, "text", result.metadata.get("text_score", result.similarity))

        merged: List[SearchResult] = []
        for entry in candidates.values():
            score = sum(entry[name] * weights[name] for name in weights)
            if score < min_similarity:
                continue
            source: SearchResult = entry["result"]
            metadata = dict(source.metadata)
            metadata.update(
                {
                    "audio_score": entry["audio"],
                    "text_score": entry["text"],
                    "keyword_score": entry["keyword"],
                    "score_weights": weights,
                }
            )
            merged.append(
                SearchResult(
                    file_path=source.file_path,
                    filename=source.filename,
                    similarity=float(np.clip(score, 0.0, 1.0)),
                    duration=source.duration,
                    format=source.format,
                    metadata=metadata,
                )
            )
        merged.sort(key=lambda result: result.similarity, reverse=True)
        return merged[:top_k], weights

    async def search_async(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
        progress_callback: Optional[Callable[[str, float], None]] = None,
        offset: int = 0,
    ) -> Tuple[List[SearchResult], Dict[str, Any]]:
        """Run project-isolated keyword + audio + metadata-text retrieval."""
        if top_k is None:
            top_k = config.TOP_K_RESULTS
        if min_similarity is None:
            min_similarity = config.SIMILARITY_THRESHOLD
        offset = max(0, int(offset))
        fetch_k = int(top_k) + offset
        query = str(query or "").strip()
        if not query:
            return [], {"cache_hit": False, "total_found": 0, "returned": 0}

        start_time = time.time()
        stats = {
            "cache_hit": False,
            "query_expansion": False,
            "layers": {"exact": 0, "expanded": 0, "semantic": 0}
        }

        async def report(stage: str, progress: float) -> None:
            if progress_callback:
                returned = progress_callback(stage, progress)
                if hasattr(returned, "__await__"):
                    await returned

        # Request paths may use only a model that background preload/indexing
        # has already made ready.  Never synchronously initialize CLAP here.
        embedder = peek_embedder()
        audio_fingerprint = (
            getattr(embedder, "fingerprint", None)
            or get_embedder_fingerprint(load=False)
            or self.model_fingerprint
        )
        self.model_fingerprint = audio_fingerprint
        cache_context = {
            "top_k": int(top_k),
            "offset": offset,
            "min_similarity": float(min_similarity),
            "filters": filters or {},
            "project_id": self.project_id,
            "index_revision": self.index_revision,
            "audio_collection_revision": get_collection_revision(
                self.persist_directory, self.collection_name
            ),
            "text_collection_revision": get_collection_revision(
                self.persist_directory, self._text_searcher.collection_name
            ),
            "model_fingerprint": audio_fingerprint,
            "text_fingerprint": self._configured_text_fingerprint(),
        }

        await report("checking_cache", 0.1)

        if use_cache:
            cached = await self._query_cache.get(query, **cache_context)
            if cached:
                stats["cache_hit"] = True
                stats["duration"] = time.time() - start_time
                stats["total_found"] = cached.total_count
                stats["returned"] = len(cached.results)
                return cached.results, stats

        await report("preparing_query", 0.2)

        await report("keyword_searching", 0.3)
        expanded_queries, exact_results, expanded_results = await asyncio.to_thread(
            self._keyword_search_from_snapshot, query, filters
        )
        if len(expanded_queries) > 1:
            stats["query_expansion"] = True
            stats["expanded_queries"] = expanded_queries
        stats["layers"]["exact"] = len(exact_results)
        stats["layers"]["expanded"] = len(expanded_results)
        keyword_results = exact_results + expanded_results

        from core.database import get_db_manager

        artifact_counts = await asyncio.to_thread(
            get_db_manager().get_artifact_counts, self.project_id
        )

        audio_results: List[SearchResult] = []
        audio_available = bool(
            embedder is not None
            and not self.needs_rebuild
            and int(self.collection.count()) > 0
            and artifact_counts["audio_vector"]["ready"] > 0
            and self.is_compatible_with(audio_fingerprint)
        )
        if audio_available:
            await report("generating_embedding", 0.5)
            for index, expanded_query in enumerate(expanded_queries):
                try:
                    query_embedding = await asyncio.to_thread(
                        embedder.text_to_embedding, expanded_query
                    )
                    await report(
                        f"semantic_searching_{index + 1}",
                        0.5 + (index * 0.2 / max(1, len(expanded_queries))),
                    )
                    found = await asyncio.to_thread(
                        self._semantic_search,
                        query_embedding,
                        expanded_query,
                        fetch_k,
                        0.0,
                        filters,
                    )
                    for result in found:
                        result.metadata["matched_query"] = expanded_query
                        result.metadata["audio_score"] = result.metadata.get(
                            "semantic_score", result.similarity
                        )
                    audio_results.extend(found)
                except Exception as exc:
                    logger.warning("音频语义查询 '%s' 失败: %s", expanded_query, exc)
            audio_results = await asyncio.to_thread(
                self._ready_vector_results, audio_results, "audio_vector"
            )
            stats["layers"]["semantic"] = len(audio_results)

        await report("text_metadata_searching", 0.75)
        text_results: List[SearchResult] = []
        text_available = bool(
            not self._text_searcher.needs_rebuild
            and int(self._text_searcher.collection.count()) > 0
            and artifact_counts["text_vector"]["ready"] > 0
        )
        if text_available:
            try:
                text_results = await self._text_searcher.search(
                    query=query,
                    top_k=max(fetch_k * 3, fetch_k),
                    min_similarity=0.0,
                    filters=filters,
                )
            except Exception as exc:
                text_available = False
                logger.warning("文本元数据索引查询失败，按可用分支降级: %s", exc)
        if text_results:
            text_results = await asyncio.to_thread(
                self._ready_vector_results, text_results, "text_vector"
            )
        stats["layers"]["text"] = len(text_results)

        await report("ranking_results", 0.85)
        merged_results, weights = self._weighted_merge_results(
            keyword_results,
            audio_results,
            text_results,
            top_k=fetch_k,
            min_similarity=min_similarity,
            audio_available=audio_available,
            text_available=text_available,
        )
        final_results = merged_results[offset : offset + int(top_k)]

        await report("caching", 0.95)
        if use_cache:
            await self._query_cache.set(
                query,
                final_results,
                len(merged_results),
                **cache_context,
            )

        await report("complete", 1.0)

        stats["duration"] = time.time() - start_time
        stats["total_found"] = len(merged_results)
        stats["returned"] = len(final_results)
        stats["embedder_available"] = audio_available
        stats["text_index_available"] = text_available
        stats["weights"] = weights

        return final_results, stats

    def _vector_search(
        self,
        query_embedding: np.ndarray,
        top_k: int,
        min_similarity: float,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """执行向量搜索"""
        where_clause = build_chroma_where(filters)

        collection_count = int(self.collection.count())
        if collection_count <= 0 or self.needs_rebuild:
            return []

        results = self.collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=min(top_k, collection_count),
            where=where_clause
        )

        search_results = []
        if results and results.get("ids") and len(results["ids"]) > 0:
            ids = results["ids"][0]
            distances = results["distances"][0]
            metadatas = results["metadatas"][0]

            for i, file_id in enumerate(ids):
                distance = distances[i]
                similarity = cosine_similarity_from_distance(distance)

                if similarity < min_similarity:
                    continue

                metadata = dict(metadatas[i] or {})
                metadata["file_id"] = str(file_id)

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


# 全局优化的搜索器实例（按工程/collection 隔离）
_optimized_searchers: Dict[str, OptimizedAudioSearcher] = {}
_searcher_locks: Dict[str, asyncio.Lock] = {}
_optimized_sync_lock = threading.RLock()


def _optimized_key(
    persist_directory: Optional[str],
    collection_name: str,
    text_collection_name: str,
    project_id: Optional[str],
) -> str:
    resolved_project = str(project_id or config.CURRENT_PROJECT_ID)
    resolved_path = persist_directory or str(safe_project_chroma_path(resolved_project))
    return json.dumps(
        {
            "project": resolved_project,
            "path": os.path.normcase(str(Path(resolved_path).resolve(strict=False))),
            "collection": collection_name,
            "text_collection": text_collection_name,
        },
        sort_keys=True,
    )


async def get_optimized_searcher(
    persist_directory: Optional[str] = None,
    collection_name: str = "audio_embeddings",
    project_id: Optional[str] = None,
    index_revision: int = 0,
    model_fingerprint: Optional[str] = None,
    text_collection_name: str = "text_metadata_embeddings",
) -> OptimizedAudioSearcher:
    """获取优化的搜索器单例（线程安全）"""
    key = _optimized_key(
        persist_directory, collection_name, text_collection_name, project_id
    )
    lock = _searcher_locks.setdefault(key, asyncio.Lock())
    async with lock:
        searcher = _optimized_searchers.get(key)
        if (
            searcher is None
            or searcher.index_revision != int(index_revision)
            or (
                model_fingerprint is not None
                and searcher.model_fingerprint != model_fingerprint
            )
        ):
            searcher = OptimizedAudioSearcher(
                persist_directory=persist_directory,
                collection_name=collection_name,
                text_collection_name=text_collection_name,
                project_id=project_id,
                index_revision=index_revision,
                model_fingerprint=model_fingerprint,
            )
            with _optimized_sync_lock:
                _optimized_searchers[key] = searcher
        return searcher


def get_optimized_searcher_sync(
    persist_directory: Optional[str] = None,
    collection_name: str = "audio_embeddings",
    project_id: Optional[str] = None,
    index_revision: int = 0,
    model_fingerprint: Optional[str] = None,
    text_collection_name: str = "text_metadata_embeddings",
) -> OptimizedAudioSearcher:
    """获取优化的搜索器单例（同步版本，用于非异步上下文）"""
    key = _optimized_key(
        persist_directory, collection_name, text_collection_name, project_id
    )
    with _optimized_sync_lock:
        searcher = _optimized_searchers.get(key)
        if (
            searcher is None
            or searcher.index_revision != int(index_revision)
            or (
                model_fingerprint is not None
                and searcher.model_fingerprint != model_fingerprint
            )
        ):
            searcher = OptimizedAudioSearcher(
                persist_directory=persist_directory,
                collection_name=collection_name,
                text_collection_name=text_collection_name,
                project_id=project_id,
                index_revision=index_revision,
                model_fingerprint=model_fingerprint,
            )
            _optimized_searchers[key] = searcher
        return searcher


def reset_optimized_searcher(project_id: Optional[str] = None) -> None:
    """重置优化的搜索器单例"""
    with _optimized_sync_lock:
        if project_id is None:
            _optimized_searchers.clear()
            _searcher_locks.clear()
            return
        for key, searcher in list(_optimized_searchers.items()):
            if searcher.project_id == project_id:
                del _optimized_searchers[key]
                _searcher_locks.pop(key, None)
