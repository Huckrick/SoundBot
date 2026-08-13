# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Nagisa_Huckrick (胡杨)

"""Low-level cosine search adapters for audio and metadata collections."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
from pydantic import BaseModel

import config
from core.embedder import (
    TextEmbeddingProvider,
    get_embedder,
    get_embedder_fingerprint,
    get_text_embedding_provider,
    get_text_embedding_config_fingerprint,
    normalize_embedding,
)
from core.indexer import (
    collection_engine_fingerprints,
    collection_uses_cosine,
    get_chroma_client,
    safe_project_chroma_path,
)

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    file_path: str
    filename: str
    similarity: float
    duration: float
    format: str
    metadata: Dict[str, Any]


def cosine_similarity_from_distance(distance: float) -> float:
    """Convert Chroma cosine distance to cosine similarity."""
    value = 1.0 - float(distance)
    if not np.isfinite(value):
        raise ValueError("Chroma 返回了非有限距离")
    return float(np.clip(value, -1.0, 1.0))


def _condition_clauses(field: str, condition: Any) -> List[Dict[str, Any]]:
    if isinstance(condition, Mapping):
        clauses = []
        for operator, value in condition.items():
            if operator not in {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin"}:
                raise ValueError(f"不支持的 Chroma 过滤运算符: {operator}")
            clauses.append({field: {operator: value}})
        return clauses
    return [{field: {"$eq": condition}}]


def build_chroma_where(filters: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build a Chroma-valid where tree, including multi-bound fields.

    Chroma accepts exactly one operator per field expression and exactly one
    top-level expression. A UI filter such as ``duration: {$gte, $lte}`` must
    therefore become two children of ``$and``.
    """
    if not filters:
        return None
    clauses: List[Dict[str, Any]] = []
    for field, condition in filters.items():
        if field in {"$and", "$or"}:
            if not isinstance(condition, list) or not condition:
                raise ValueError(f"{field} 必须是非空过滤列表")
            children = [build_chroma_where(child) for child in condition]
            clauses.append({field: [child for child in children if child]})
        elif str(field).startswith("$"):
            raise ValueError(f"不支持的顶层过滤运算符: {field}")
        else:
            clauses.extend(_condition_clauses(str(field), condition))
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class AudioSearcher:
    """Text-to-audio and audio-to-audio search over the CLAP collection."""

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        collection_name: str = "audio_embeddings",
        project_id: Optional[str] = None,
        index_revision: int = 0,
        model_fingerprint: Optional[str] = None,
    ):
        if persist_directory is None:
            persist_directory = str(config.get_db_path())
        self.persist_directory = str(Path(persist_directory).resolve(strict=False))
        self.collection_name = collection_name
        self.project_id = project_id
        self.index_revision = int(index_revision)
        self.model_fingerprint = model_fingerprint or get_embedder_fingerprint(load=False)
        self.client = get_chroma_client(self.persist_directory)
        try:
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=None,
                metadata={
                    "description": "SoundBot CLAP audio embeddings",
                    "hnsw:space": "cosine",
                    "soundbot_schema": 2,
                },
            )
        except Exception as exc:
            raise RuntimeError(f"无法获取 collection '{collection_name}': {exc}") from exc
        self._engine_fingerprints, self._unknown_engine_metadata = (
            collection_engine_fingerprints(self.collection)
        )
        self.needs_rebuild = bool(
            not collection_uses_cosine(self.collection)
            or (
                int(self.collection.count()) > 0
                and (
                    self._unknown_engine_metadata
                    or len(self._engine_fingerprints) != 1
                )
            )
        )

    def is_compatible_with(self, fingerprint: Optional[str]) -> bool:
        """Reject vectors produced by a different CLAP/preprocessing revision."""
        if self.collection.count() <= 0:
            return True
        self._engine_fingerprints, self._unknown_engine_metadata = (
            collection_engine_fingerprints(self.collection)
        )
        if (
            self._unknown_engine_metadata
            or len(self._engine_fingerprints) != 1
        ):
            self.needs_rebuild = True
            return False
        if not fingerprint or fingerprint == "clap:unavailable":
            return False
        return self._engine_fingerprints == {str(fingerprint)}

    def _query(
        self,
        query_embedding: np.ndarray,
        top_k: int,
        min_similarity: float,
        filters: Optional[Dict[str, Any]],
        *,
        score_key: str = "audio_score",
    ) -> List[SearchResult]:
        if self.needs_rebuild:
            logger.warning(
                "跳过距离度量或引擎指纹不兼容的 collection %s；等待影子重建",
                self.collection_name,
            )
            return []
        current_fingerprint = get_embedder_fingerprint(load=False)
        if not self.is_compatible_with(current_fingerprint):
            logger.warning("CLAP index fingerprint 与当前模型不匹配，等待重建")
            return []
        count = int(self.collection.count())
        if count <= 0 or top_k <= 0:
            return []
        results = self.collection.query(
            query_embeddings=[normalize_embedding(query_embedding).tolist()],
            n_results=min(int(top_k), count),
            where=build_chroma_where(filters),
            include=["metadatas", "distances"],
        )
        parsed: List[SearchResult] = []
        ids = (results.get("ids") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        for index, file_id in enumerate(ids):
            similarity = cosine_similarity_from_distance(distances[index])
            if similarity < min_similarity:
                continue
            metadata = dict(metadatas[index] or {})
            metadata.setdefault("file_id", file_id)
            metadata[score_key] = similarity
            metadata["semantic_score"] = similarity
            metadata["distance"] = float(distances[index])
            parsed.append(
                SearchResult(
                    file_path=metadata.get("file_path", ""),
                    filename=metadata.get("filename", ""),
                    similarity=similarity,
                    duration=float(metadata.get("duration", 0.0) or 0.0),
                    format=metadata.get("format", "") or "",
                    metadata=metadata,
                )
            )
        return parsed

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        embedder = get_embedder()
        if embedder is None:
            return []
        return self.search_by_embedding(
            embedder.text_to_embedding(query), top_k, min_similarity, filters
        )

    def search_by_embedding(
        self,
        query_embedding: np.ndarray,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        return self._query(
            query_embedding,
            top_k if top_k is not None else config.TOP_K_RESULTS,
            min_similarity if min_similarity is not None else config.SIMILARITY_THRESHOLD,
            filters,
        )

    def search_audio_to_audio(
        self,
        audio_query_path: str,
        top_k: Optional[int] = None,
        min_similarity: Optional[float] = None,
    ) -> List[SearchResult]:
        embedder = get_embedder()
        if embedder is None:
            return []
        return self.search_by_embedding(
            embedder.audio_to_embedding(audio_query_path), top_k, min_similarity
        )

    def get_all_indexed_files(self) -> List[Dict[str, Any]]:
        try:
            results = self.collection.get(include=["metadatas"])
            files = []
            for index, file_id in enumerate(results.get("ids") or []):
                metadata = dict((results.get("metadatas") or [])[index] or {})
                files.append(
                    {
                        "id": file_id,
                        "file_path": metadata.get("file_path", ""),
                        "filename": metadata.get("filename", ""),
                        "duration": metadata.get("duration", 0.0),
                        "format": metadata.get("format", ""),
                    }
                )
            return files
        except Exception as exc:
            logger.error("获取索引文件列表失败: %s", exc)
            return []

    def get_collection_stats(self) -> Dict[str, Any]:
        try:
            return {
                "total_count": self.collection.count(),
                "collection_name": self.collection_name,
                "persist_directory": self.persist_directory,
                "project_id": self.project_id,
                "index_revision": self.index_revision,
                "model_fingerprint": self.model_fingerprint,
                "metric": (getattr(self.collection, "metadata", None) or {}).get("hnsw:space"),
                "needs_rebuild": self.needs_rebuild,
            }
        except Exception as exc:
            logger.error("获取统计信息失败: %s", exc)
            return {}


class MetadataTextSearcher:
    """Async query adapter for the independent metadata-text collection."""

    def __init__(
        self,
        persist_directory: str,
        collection_name: str = "text_metadata_embeddings",
        provider: Optional[TextEmbeddingProvider] = None,
        project_id: Optional[str] = None,
    ):
        self.persist_directory = str(Path(persist_directory).resolve(strict=False))
        self.collection_name = collection_name
        self.project_id = project_id
        self.provider = provider
        self._provider_override = provider is not None
        self._provider_config_fingerprint: Optional[str] = None
        self.client = get_chroma_client(self.persist_directory)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=None,
            metadata={
                "description": "SoundBot searchable file metadata",
                "hnsw:space": "cosine",
                "soundbot_schema": 1,
            },
        )
        self._engine_fingerprints, self._unknown_engine_metadata = (
            collection_engine_fingerprints(self.collection)
        )
        self.needs_rebuild = bool(
            not collection_uses_cosine(self.collection)
            or (
                int(self.collection.count()) > 0
                and (
                    self._unknown_engine_metadata
                    or len(self._engine_fingerprints) != 1
                )
            )
        )

    def _is_compatible_with(self, fingerprint: str) -> bool:
        if self.collection.count() <= 0:
            return True
        self._engine_fingerprints, self._unknown_engine_metadata = (
            collection_engine_fingerprints(self.collection)
        )
        if (
            self._unknown_engine_metadata
            or len(self._engine_fingerprints) != 1
        ):
            self.needs_rebuild = True
            return False
        return self._engine_fingerprints == {str(fingerprint or "")}

    def _provider(self) -> TextEmbeddingProvider:
        if self._provider_override:
            return self.provider
        configured = get_text_embedding_config_fingerprint()
        if self.provider is None or configured != self._provider_config_fingerprint:
            self.provider = get_text_embedding_provider()
            self._provider_config_fingerprint = configured
        return self.provider

    async def search(
        self,
        query: str,
        top_k: int,
        min_similarity: float = 0.0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        count = int(self.collection.count())
        if count <= 0 or self.needs_rebuild:
            return []
        provider = self._provider()
        if not self._is_compatible_with(provider.fingerprint):
            logger.warning(
                "文本索引 fingerprint 未知、混合或不匹配（stored=%s current=%s），等待重建",
                sorted(self._engine_fingerprints),
                provider.fingerprint,
            )
            return []
        vectors = await provider.embed_texts([query])
        if not vectors:
            return []
        raw = self.collection.query(
            query_embeddings=[normalize_embedding(vectors[0]).tolist()],
            n_results=min(int(top_k), count),
            where=build_chroma_where(filters),
            include=["metadatas", "distances"],
        )
        results: List[SearchResult] = []
        ids = (raw.get("ids") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        for index, file_id in enumerate(ids):
            similarity = cosine_similarity_from_distance(distances[index])
            if similarity < min_similarity:
                continue
            metadata = dict(metadatas[index] or {})
            metadata.setdefault("file_id", file_id)
            metadata.update(
                {
                    "text_score": similarity,
                    "text_engine_fingerprint": provider.fingerprint,
                    "text_distance": float(distances[index]),
                }
            )
            results.append(
                SearchResult(
                    file_path=metadata.get("file_path", ""),
                    filename=metadata.get("filename", ""),
                    similarity=similarity,
                    duration=float(metadata.get("duration", 0.0) or 0.0),
                    format=metadata.get("format", "") or "",
                    metadata=metadata,
                )
            )
        return results

    def get_collection_stats(self) -> Dict[str, Any]:
        return {
            "total_count": self.collection.count(),
            "collection_name": self.collection_name,
            "project_id": self.project_id,
            "metric": (getattr(self.collection, "metadata", None) or {}).get("hnsw:space"),
            "needs_rebuild": self.needs_rebuild,
            "model_fingerprint": self.provider.fingerprint if self.provider else None,
        }


_searchers: Dict[str, AudioSearcher] = {}
_searchers_lock = threading.RLock()


def _searcher_key(
    persist_directory: Optional[str], collection_name: str, project_id: Optional[str]
) -> str:
    return json.dumps(
        {
            "path": os.path.normcase(str(Path(persist_directory).resolve(strict=False)))
            if persist_directory
            else None,
            "collection": collection_name,
            "project": project_id,
        },
        sort_keys=True,
    )


def get_searcher(
    persist_directory: Optional[str] = None,
    collection_name: str = "audio_embeddings",
    project_id: Optional[str] = None,
    index_revision: int = 0,
    model_fingerprint: Optional[str] = None,
) -> AudioSearcher:
    if project_id is not None and persist_directory is None:
        persist_directory = str(safe_project_chroma_path(project_id))
    key = _searcher_key(persist_directory, collection_name, project_id)
    with _searchers_lock:
        searcher = _searchers.get(key)
        if (
            searcher is None
            or searcher.index_revision != int(index_revision)
            or (
                model_fingerprint is not None
                and searcher.model_fingerprint != model_fingerprint
            )
        ):
            searcher = AudioSearcher(
                persist_directory=persist_directory,
                collection_name=collection_name,
                project_id=project_id,
                index_revision=index_revision,
                model_fingerprint=model_fingerprint,
            )
            _searchers[key] = searcher
        return searcher


def reset_searcher(project_id: Optional[str] = None) -> None:
    with _searchers_lock:
        if project_id is None:
            _searchers.clear()
        else:
            for key in list(_searchers):
                if _searchers[key].project_id == project_id:
                    del _searchers[key]
