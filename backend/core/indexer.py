# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Nagisa_Huckrick (胡杨)

"""Project-scoped Chroma indexes for audio and metadata text.

Chroma itself is the source of truth for vector presence.  The historical
``indexed_files_meta.json`` sidecar is intentionally ignored: it could claim a
file was indexed after a failed Chroma write, or omit a vector that did exist.
SQLite owns the durable file/artifact state; :meth:`AudioIndexer.reconcile`
provides the hook used by the API layer to compare that state with Chroma.
"""

from __future__ import annotations

import hashlib
import gc
import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import chromadb
import numpy as np
from chromadb.config import Settings

import config
from core.database import canonicalize_path
from core.embedder import (
    TextEmbeddingProvider,
    get_embedder,
    get_embedder_fingerprint,
    get_clap_engine_manifest,
    get_text_embedding_provider,
    get_text_embedding_config_fingerprint,
    normalize_embedding,
)
from core.scanner import AudioScanner

logger = logging.getLogger(__name__)

AUDIO_COLLECTION_SCHEMA = 2
TEXT_COLLECTION_SCHEMA = 1
COSINE_SPACE = "cosine"
_SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def normalize_file_path(file_path: str) -> str:
    """Create a stable path key across Windows separator/case variants."""
    value = os.fspath(file_path)
    if not value:
        raise ValueError("文件路径不能为空")
    return canonicalize_path(value)


def safe_project_chroma_path(project_id: str, *, create: bool = True) -> Path:
    """Resolve a project index below the one permitted Chroma root.

    Existing legacy IDs made from letters, digits, dots, underscores and
    hyphens remain valid. Path separators, drive prefixes and ``..`` are
    rejected before any directory is created.
    """
    value = str(project_id or "").strip()
    windows_stem = value.split(".", 1)[0].upper()
    if (
        value in {"", ".", ".."}
        or value.endswith(".")
        or windows_stem in _WINDOWS_RESERVED_NAMES
        or not _SAFE_PROJECT_ID.fullmatch(value)
    ):
        raise ValueError("工程 ID 包含非法字符")

    root = (config.get_user_data_dir() / "chroma_projects").resolve(strict=False)
    candidate = (root / value).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("工程索引路径越界") from exc
    if candidate == root:
        raise ValueError("工程索引路径不能是根目录")
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def file_source_fingerprint(file_path: str) -> str:
    """Fast source fingerprint (identity is deliberately not content hash)."""
    stat = os.stat(file_path)
    payload = f"{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_file_id(file_path: str) -> str:
    """Legacy-compatible vector ID derived from the normalized path."""
    return hashlib.sha256(normalize_file_path(file_path).encode("utf-8")).hexdigest()[:32]


def _sanitize_metadata(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    """Convert metadata to Chroma's scalar-only wire shape."""
    result: Dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, bool):
            result[str(key)] = value
        elif isinstance(value, (str, int, float)):
            result[str(key)] = value
        elif isinstance(value, (dict, list, tuple, set)):
            result[str(key)] = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            result[str(key)] = str(value)
    return result


def _collection_dimension(collection: Any) -> Optional[int]:
    """Read one stored vector dimension without loading an embedding model."""
    try:
        snapshot = collection.get(limit=1, include=["embeddings"])
        embeddings = snapshot.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            return None
        vector = np.asarray(embeddings[0]).reshape(-1)
        return int(vector.size) if vector.size else None
    except Exception:
        return None


def _collection_metadata(description: str, schema: int) -> Dict[str, Any]:
    return {
        "description": description,
        "hnsw:space": COSINE_SPACE,
        "soundbot_schema": int(schema),
    }


def collection_uses_cosine(collection: Any) -> bool:
    metadata = getattr(collection, "metadata", None) or {}
    return metadata.get("hnsw:space") == COSINE_SPACE


def collection_engine_fingerprints(collection: Any) -> Tuple[set[str], bool]:
    """Return every persisted engine fingerprint and whether any row lacks one."""
    if int(collection.count()) <= 0:
        return set(), False
    snapshot = collection.get(include=["metadatas"])
    metadatas = snapshot.get("metadatas") or []
    fingerprints: set[str] = set()
    unknown = False
    for metadata in metadatas:
        fingerprint = str((metadata or {}).get("engine_fingerprint") or "").strip()
        if fingerprint:
            fingerprints.add(fingerprint)
        else:
            unknown = True
    if len(metadatas) < int(collection.count()):
        unknown = True
    return fingerprints, unknown


_chroma_clients: Dict[str, chromadb.PersistentClient] = {}
_clients_lock = threading.RLock()
_collection_revisions: Dict[str, int] = {}
_revision_lock = threading.RLock()


def _revision_key(persist_directory: str, collection_name: str) -> str:
    path = os.path.normcase(str(Path(persist_directory).resolve(strict=False)))
    return f"{path}\0{collection_name}"


def get_collection_revision(persist_directory: str, collection_name: str) -> int:
    with _revision_lock:
        return _collection_revisions.get(
            _revision_key(persist_directory, collection_name), 0
        )


def bump_collection_revision(persist_directory: str, collection_name: str) -> int:
    key = _revision_key(persist_directory, collection_name)
    with _revision_lock:
        _collection_revisions[key] = _collection_revisions.get(key, 0) + 1
        return _collection_revisions[key]


def get_chroma_client(persist_directory: Optional[str] = None) -> chromadb.PersistentClient:
    if persist_directory is None:
        persist_directory = str(config.get_db_path())
    path = Path(persist_directory).expanduser().resolve(strict=False)
    path.mkdir(parents=True, exist_ok=True)
    key = os.path.normcase(str(path))
    with _clients_lock:
        if key not in _chroma_clients:
            _chroma_clients[key] = chromadb.PersistentClient(
                path=str(path),
                settings=Settings(anonymized_telemetry=False, is_persistent=True),
            )
        return _chroma_clients[key]


def reset_chroma_client(persist_directory: Optional[str] = None) -> None:
    clients = []
    with _clients_lock:
        if persist_directory is None:
            clients = list(_chroma_clients.values())
            _chroma_clients.clear()
        else:
            key = os.path.normcase(
                str(Path(persist_directory).expanduser().resolve(strict=False))
            )
            client = _chroma_clients.pop(key, None)
            if client is not None:
                clients.append(client)
    # Chroma keeps a shared SQLite/system reference until close(). Dropping
    # only our cache entry leaves Windows file handles live and prevents the
    # project directory from being removed.
    for client in clients:
        try:
            client.close()
        except Exception as exc:
            logger.warning("关闭 Chroma client 失败: %s", exc)


class AudioIndexer:
    """Cosine audio-vector index with idempotent upserts."""

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        collection_name: str = "audio_embeddings",
        project_id: Optional[str] = None,
    ):
        if persist_directory is None:
            persist_directory = str(config.get_db_path())
        self.persist_directory = str(Path(persist_directory).resolve(strict=False))
        self.collection_name = collection_name
        self.project_id = project_id
        self.client = get_chroma_client(self.persist_directory)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=None,
            metadata=_collection_metadata(
                "SoundBot CLAP audio embeddings", AUDIO_COLLECTION_SCHEMA
            ),
        )
        # Chroma cannot change HNSW distance for an existing collection.  The
        # API layer must schedule a shadow rebuild when this is true.
        self._write_lock = threading.RLock()
        self._refresh_engine_state()
        if self.needs_rebuild:
            logger.warning(
                "Collection %s 的距离度量或引擎指纹不兼容，需执行影子重建后切换",
                collection_name,
            )
        logger.info(
            "Indexer 初始化完成: project=%s collection=%s count=%s",
            project_id,
            collection_name,
            self.collection.count(),
        )

    def _ensure_engine_compatible(self, fingerprint: str) -> None:
        """Never mix vectors from different CLAP revisions in an active collection."""
        fingerprint = str(fingerprint or "").strip()
        if not fingerprint:
            raise RuntimeError("CLAP engine fingerprint is missing")
        if int(self.collection.count()) > 0 and (
            self._unknown_engine_metadata
            or self._engine_fingerprints != {fingerprint}
        ):
            self.needs_rebuild = True
            raise RuntimeError(
                "CLAP engine fingerprint changed; active collection requires shadow rebuild"
            )

    def _refresh_engine_state(self) -> None:
        self._engine_fingerprints, self._unknown_engine_metadata = (
            collection_engine_fingerprints(self.collection)
        )
        count = int(self.collection.count())
        self.needs_rebuild = bool(
            not collection_uses_cosine(self.collection)
            or (
                count > 0
                and (
                    self._unknown_engine_metadata
                    or len(self._engine_fingerprints) != 1
                )
            )
        )

    def _record_engine_write(self, fingerprint: str) -> None:
        self._engine_fingerprints = {str(fingerprint)}
        self._unknown_engine_metadata = False

    @property
    def indexed_files_meta(self) -> Dict[str, Dict[str, Any]]:
        """Compatibility view derived from Chroma, never from a sidecar."""
        snapshot = self.collection.get(include=["metadatas"])
        ids = snapshot.get("ids") or []
        metadatas = snapshot.get("metadatas") or []
        return {str(file_id): dict(metadatas[i] or {}) for i, file_id in enumerate(ids)}

    def _load_indexed_meta(self) -> None:
        """Deprecated no-op retained for old scripts."""

    def _save_indexed_meta(self) -> None:
        """Deprecated no-op retained for old scripts."""

    def _get_file_hash(self, file_path: str) -> str:
        return file_source_fingerprint(file_path)

    def _generate_file_id(self, file_path: str) -> str:
        return generate_file_id(file_path)

    def _record_id(self, file_path: str, metadata: Optional[Mapping[str, Any]] = None) -> str:
        explicit = (metadata or {}).get("file_id") or (metadata or {}).get("id")
        return str(explicit) if explicit else self._generate_file_id(file_path)

    def _generate_embedding_for_file(
        self, file_id: str, file_path: str, audio_file: Any, embedder: Any
    ) -> Optional[Dict[str, Any]]:
        try:
            embedding = normalize_embedding(embedder.audio_to_embedding(file_path))
            metadata = _sanitize_metadata(
                {
                    "file_path": file_path,
                    "normalized_path": normalize_file_path(file_path),
                    "filename": audio_file.filename,
                    "duration": audio_file.duration,
                    "sample_rate": audio_file.sample_rate,
                    "channels": audio_file.channels,
                    "format": audio_file.format,
                    "size": audio_file.size,
                    "source_fingerprint": file_source_fingerprint(file_path),
                    # Keep the old key until callers migrate.
                    "hash": file_source_fingerprint(file_path),
                    "engine_fingerprint": getattr(embedder, "fingerprint", "clap:unknown"),
                    "folder_path": getattr(audio_file, "folder_path", ""),
                    "parsed_name": getattr(audio_file, "parsed_name", ""),
                    "name_description": getattr(audio_file, "name_description", ""),
                    "metadata_tags": getattr(audio_file, "metadata_tags", {}) or {},
                }
            )
            return {"file_id": file_id, "embedding": embedding.tolist(), "metadata": metadata}
        except Exception as exc:
            logger.error("生成 embedding 失败 %s: %s", file_path, exc)
            return None

    def _batch_process_files(
        self,
        files_to_process: List[Tuple[str, str, Any]],
        embedder: Any,
        is_update: bool = False,
        batch_size: int = 32,
        max_workers: int = 1,
    ) -> int:
        """Encode through the model's single worker and atomically upsert batches."""
        if not files_to_process or embedder is None:
            return 0
        if self.needs_rebuild:
            logger.error("拒绝写入不兼容 collection；请先执行影子重建")
            return 0
        try:
            self._ensure_engine_compatible(getattr(embedder, "fingerprint", ""))
        except RuntimeError as exc:
            logger.error("拒绝混写 CLAP collection: %s", exc)
            return 0
        if max_workers != 1:
            logger.debug("忽略 max_workers=%s；CLAP 推理固定为单 worker", max_workers)

        processed = 0
        started = time.monotonic()
        for batch_start in range(0, len(files_to_process), max(1, batch_size)):
            batch = files_to_process[batch_start : batch_start + max(1, batch_size)]
            generated = [
                self._generate_embedding_for_file(file_id, file_path, audio_file, embedder)
                for file_id, file_path, audio_file in batch
            ]
            rows = [row for row in generated if row is not None]
            if not rows:
                continue
            try:
                with self._write_lock:
                    self._ensure_engine_compatible(embedder.fingerprint)
                    self.collection.upsert(
                        ids=[row["file_id"] for row in rows],
                        embeddings=[row["embedding"] for row in rows],
                        metadatas=[row["metadata"] for row in rows],
                    )
                    bump_collection_revision(self.persist_directory, self.collection_name)
                    self._record_engine_write(embedder.fingerprint)
                processed += len(rows)
            except Exception:
                logger.exception("批量写入 ChromaDB 失败")
        logger.info(
            "向量批处理完成: success=%s failed=%s duration=%.2fs",
            processed,
            len(files_to_process) - processed,
            time.monotonic() - started,
        )
        return processed

    def index_audio_files(
        self, folder_path: str, recursive: bool = True, force_reindex: bool = False
    ) -> Dict[str, Any]:
        scanner = AudioScanner()
        audio_files = scanner.scan(folder_path, recursive)
        # This legacy synchronous entry point is dispatched to an indexing
        # worker by API callers, so it may load the model here. Async request
        # paths use the no-load availability probe instead.
        embedder = get_embedder()
        if embedder is None:
            return {
                "total_files": len(audio_files),
                "added": 0,
                "updated": 0,
                "skipped": len(audio_files),
                "failed": len(audio_files),
                "pending_model": True,
                "files": [item.path for item in audio_files],
                "total_indexed": self.get_indexed_count(),
            }

        existing = self.indexed_files_meta
        to_add: List[Tuple[str, str, Any]] = []
        to_update: List[Tuple[str, str, Any]] = []
        for audio_file in audio_files:
            file_path = audio_file.path
            file_id = self._generate_file_id(file_path)
            current = existing.get(file_id)
            fingerprint = file_source_fingerprint(file_path)
            if current is None:
                to_add.append((file_id, file_path, audio_file))
            elif (
                force_reindex
                or current.get("source_fingerprint", current.get("hash")) != fingerprint
                or current.get("engine_fingerprint") != getattr(embedder, "fingerprint", None)
            ):
                to_update.append((file_id, file_path, audio_file))

        added = self._batch_process_files(to_add, embedder)
        updated = self._batch_process_files(to_update, embedder, is_update=True)
        failed = len(to_add) + len(to_update) - added - updated
        return {
            "total_files": len(audio_files),
            "added": added,
            "updated": updated,
            "skipped": len(audio_files) - len(to_add) - len(to_update),
            "failed": failed,
            "pending_model": False,
            "total_indexed": self.get_indexed_count(),
            "needs_rebuild": self.needs_rebuild,
        }

    def add_single_audio(
        self, file_path: str, metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Idempotently encode and upsert one file.

        ``metadata['file_id']`` lets SQLite's project-local UUID become the
        vector ID. Without it, a normalized-path hash preserves compatibility.
        """
        metadata = dict(metadata or {})
        try:
            if self.needs_rebuild:
                logger.error("拒绝写入不兼容 collection；请先执行影子重建")
                return False
            embedder = get_embedder()
            if embedder is None:
                logger.warning("CLAP 模型不可用，文件保持 pending: %s", file_path)
                return False
            self._ensure_engine_compatible(getattr(embedder, "fingerprint", ""))
            file_id = self._record_id(file_path, metadata)
            if metadata.get("filename"):
                class AudioInfo:
                    pass

                audio_info = AudioInfo()
                audio_info.filename = metadata.get("filename", Path(file_path).name)
                audio_info.duration = metadata.get("duration", 0.0)
                audio_info.sample_rate = metadata.get("sample_rate", 0)
                audio_info.channels = metadata.get("channels", 0)
                audio_info.format = metadata.get("format", Path(file_path).suffix.lstrip("."))
                audio_info.size = metadata.get("size", os.path.getsize(file_path))
                audio_info.folder_path = metadata.get("folder_path", str(Path(file_path).parent))
                audio_info.parsed_name = metadata.get("parsed_name", "")
                audio_info.name_description = metadata.get("name_description", "")
                audio_info.metadata_tags = metadata.get("metadata_tags", {})
            else:
                audio_info = AudioScanner()._process_file(Path(file_path))
                if audio_info is None:
                    return False

            row = self._generate_embedding_for_file(file_id, file_path, audio_info, embedder)
            if row is None:
                return False
            row["metadata"].update(_sanitize_metadata(metadata))
            row["metadata"]["file_path"] = file_path
            row["metadata"]["normalized_path"] = normalize_file_path(file_path)
            with self._write_lock:
                self._ensure_engine_compatible(embedder.fingerprint)
                self.collection.upsert(
                    ids=[file_id], embeddings=[row["embedding"]], metadatas=[row["metadata"]]
                )
                bump_collection_revision(self.persist_directory, self.collection_name)
                self._record_engine_write(embedder.fingerprint)
            return True
        except Exception as exc:
            logger.error("添加文件向量失败 %s: %s", file_path, exc)
            return False

    def remove_audio(self, file_path: str, file_id: Optional[str] = None) -> bool:
        try:
            record_id = str(file_id) if file_id else self._generate_file_id(file_path)
            with self._write_lock:
                existing = self.collection.get(ids=[record_id], include=[])
                if not existing.get("ids"):
                    # Migration compatibility: locate an explicit SQLite UUID by path.
                    matches = self.collection.get(
                        where={"normalized_path": {"$eq": normalize_file_path(file_path)}},
                        include=[],
                    )
                    ids = matches.get("ids") or []
                    if not ids:
                        return False
                    self.collection.delete(ids=ids)
                else:
                    self.collection.delete(ids=[record_id])
                bump_collection_revision(self.persist_directory, self.collection_name)
                self._refresh_engine_state()
            return True
        except Exception as exc:
            logger.error("移除文件向量失败 %s: %s", file_path, exc)
            return False

    def get_indexed_count(self) -> int:
        return int(self.collection.count())

    def get_all_indexed_files(self) -> List[Dict[str, Any]]:
        return list(self.indexed_files_meta.values())

    def get_manifest(self) -> Dict[str, Any]:
        # Structural compatibility (metric, missing/mixed row metadata) is
        # necessary but not sufficient: a homogeneous collection produced by
        # model revision A must still be shadow-rebuilt when the currently
        # loaded CLAP engine is revision B.
        self._refresh_engine_state()
        engine = get_clap_engine_manifest(load=False)
        current_fingerprint = get_embedder_fingerprint(load=False)
        current_available = current_fingerprint != "clap:unavailable"
        stored_fingerprint = (
            next(iter(self._engine_fingerprints))
            if not self._unknown_engine_metadata
            and len(self._engine_fingerprints) == 1
            else None
        )
        engine_changed = bool(
            self.get_indexed_count() > 0
            and current_available
            and stored_fingerprint != current_fingerprint
        )
        if engine_changed:
            self.needs_rebuild = True
        return {
            "collection": self.collection_name,
            "schema": AUDIO_COLLECTION_SCHEMA,
            "metric": (getattr(self.collection, "metadata", None) or {}).get("hnsw:space"),
            "count": self.get_indexed_count(),
            "needs_rebuild": self.needs_rebuild,
            # Report what the active collection actually contains.  The
            # target is separate so SQLite never claims old active vectors
            # were already produced by the newly installed model.
            "engine_fingerprint": stored_fingerprint
            or (current_fingerprint if current_available else engine.get("engine_fingerprint")),
            "target_engine_fingerprint": (
                current_fingerprint if current_available else None
            ),
            "model_id": engine.get("model_id"),
            "model_revision": engine.get("model_revision"),
            "dimensions": _collection_dimension(self.collection)
            or engine.get("dimensions"),
            "preprocessing_version": engine.get("preprocessing_version"),
        }

    def reconcile(
        self,
        expected_files: Sequence[Mapping[str, Any]],
        *,
        remove_orphans: bool = False,
    ) -> Dict[str, Any]:
        """Compare SQLite-shaped records with Chroma without re-encoding."""
        expected: Dict[str, Mapping[str, Any]] = {}
        for record in expected_files:
            path = str(record.get("file_path") or record.get("path") or "")
            if not path:
                continue
            expected[self._record_id(path, record)] = record
        actual = self.indexed_files_meta
        missing = sorted(set(expected) - set(actual))
        orphaned = sorted(set(actual) - set(expected))
        stale: List[str] = []
        engine = get_embedder_fingerprint(load=False)
        for file_id in sorted(set(expected) & set(actual)):
            path = str(expected[file_id].get("file_path") or expected[file_id].get("path"))
            try:
                source = file_source_fingerprint(path)
            except OSError:
                source = "missing"
            metadata = actual[file_id]
            if (
                metadata.get("source_fingerprint", metadata.get("hash")) != source
                or (engine != "clap:unavailable" and metadata.get("engine_fingerprint") != engine)
            ):
                stale.append(file_id)
        if remove_orphans and orphaned:
            with self._write_lock:
                self.collection.delete(ids=orphaned)
                bump_collection_revision(self.persist_directory, self.collection_name)
                self._refresh_engine_state()
        return {
            "missing": missing,
            "stale": stale,
            "orphaned": orphaned,
            "removed_orphans": len(orphaned) if remove_orphans else 0,
            "needs_rebuild": self.needs_rebuild,
            "manifest": self.get_manifest(),
        }

    def clear_index(self) -> None:
        with self._write_lock:
            try:
                self.client.delete_collection(name=self.collection_name)
            except Exception:
                logger.debug("Collection %s 已不存在", self.collection_name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=None,
                metadata=_collection_metadata(
                    "SoundBot CLAP audio embeddings", AUDIO_COLLECTION_SCHEMA
                ),
            )
            self._refresh_engine_state()
            bump_collection_revision(self.persist_directory, self.collection_name)


def build_metadata_text(metadata: Mapping[str, Any]) -> str:
    """Create deterministic searchable text from file metadata."""
    parts: List[str] = []
    for key in (
        "filename",
        "logical_folder",
        "folder_path",
        "tags",
        "metadata_tags",
        "ucs_category",
        "category",
        "description",
        "name_description",
        "parsed_name",
    ):
        value = metadata.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, str):
            rendered = value
        else:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if rendered not in parts:
            parts.append(rendered)
    return "\n".join(parts).strip()


class TextMetadataIndexer:
    """Separate text-only metadata collection for hybrid retrieval."""

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        collection_name: str = "text_metadata_embeddings",
        provider: Optional[TextEmbeddingProvider] = None,
        project_id: Optional[str] = None,
    ):
        if persist_directory is None:
            persist_directory = str(config.get_db_path())
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
            metadata=_collection_metadata(
                "SoundBot searchable file metadata", TEXT_COLLECTION_SCHEMA
            ),
        )
        self._write_lock = threading.RLock()
        self._refresh_engine_state()

    def _refresh_engine_state(self) -> None:
        self._engine_fingerprints, self._unknown_engine_metadata = (
            collection_engine_fingerprints(self.collection)
        )
        count = int(self.collection.count())
        self.needs_rebuild = bool(
            not collection_uses_cosine(self.collection)
            or (
                count > 0
                and (
                    self._unknown_engine_metadata
                    or len(self._engine_fingerprints) != 1
                )
            )
        )

    def _ensure_engine_compatible(self, fingerprint: str) -> None:
        fingerprint = str(fingerprint or "").strip()
        if not fingerprint:
            raise RuntimeError("text embedding engine fingerprint is missing")
        if int(self.collection.count()) > 0 and (
            self._unknown_engine_metadata
            or self._engine_fingerprints != {fingerprint}
        ):
            self.needs_rebuild = True
            raise RuntimeError(
                "text embedding engine fingerprint changed; active collection "
                "requires shadow rebuild"
            )

    def _record_engine_write(self, fingerprint: str) -> None:
        self._engine_fingerprints = {str(fingerprint)}
        self._unknown_engine_metadata = False

    def _provider(self) -> TextEmbeddingProvider:
        if self._provider_override:
            return self.provider
        configured = get_text_embedding_config_fingerprint()
        if self.provider is None or configured != self._provider_config_fingerprint:
            self.provider = get_text_embedding_provider()
            self._provider_config_fingerprint = configured
        return self.provider

    def _target_engine_fingerprint(self) -> Optional[str]:
        """Resolve the configured text engine without synchronously loading CLAP."""
        if self._provider_override:
            return str(self.provider.fingerprint) if self.provider is not None else None

        from core.llm_config_manager import get_llm_config_manager

        configured = get_llm_config_manager().get_embedding_config()
        provider_name = str(configured.get("provider", "default"))
        if provider_name == "default":
            clap_fingerprint = get_embedder_fingerprint(load=False)
            if clap_fingerprint == "clap:unavailable":
                return None
            return f"clap-text:{clap_fingerprint}"
        return str(self._provider().fingerprint)

    async def upsert_metadata(self, records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        if self.needs_rebuild:
            raise RuntimeError("文本 collection 使用旧距离度量，请先重建为 cosine")
        usable: List[Tuple[str, Dict[str, Any], str]] = []
        for source in records:
            metadata = dict(source)
            path = str(metadata.get("file_path") or metadata.get("path") or "")
            text = build_metadata_text(metadata)
            if not path or not text:
                continue
            explicit = metadata.get("file_id") or metadata.get("id")
            file_id = str(explicit) if explicit else generate_file_id(path)
            usable.append((file_id, metadata, text))
        if not usable:
            return {"indexed": 0, "failed": 0, "fingerprint": None}

        provider = self._provider()
        self._ensure_engine_compatible(provider.fingerprint)
        vectors = await provider.embed_texts([item[2] for item in usable])
        if len(vectors) != len(usable):
            raise RuntimeError("文本向量数量与元数据数量不一致")
        metadatas = []
        for (_, source, text), vector in zip(usable, vectors):
            normalize_embedding(vector)
            source.update(
                {
                    "file_path": str(source.get("file_path") or source.get("path")),
                    "normalized_path": normalize_file_path(
                        str(source.get("file_path") or source.get("path"))
                    ),
                    "metadata_text": text,
                    "engine_fingerprint": provider.fingerprint,
                }
            )
            metadatas.append(_sanitize_metadata(source))
        with self._write_lock:
            self._ensure_engine_compatible(provider.fingerprint)
            self.collection.upsert(
                ids=[item[0] for item in usable],
                embeddings=[normalize_embedding(vector).tolist() for vector in vectors],
                metadatas=metadatas,
            )
            bump_collection_revision(self.persist_directory, self.collection_name)
            self._record_engine_write(provider.fingerprint)
        return {
            "indexed": len(usable),
            "failed": 0,
            "fingerprint": provider.fingerprint,
            "needs_rebuild": self.needs_rebuild,
        }

    def remove(self, *, file_id: Optional[str] = None, file_path: Optional[str] = None) -> bool:
        if file_id:
            ids = [str(file_id)]
        elif file_path:
            result = self.collection.get(
                where={"normalized_path": {"$eq": normalize_file_path(file_path)}}, include=[]
            )
            ids = result.get("ids") or []
        else:
            raise ValueError("file_id 或 file_path 至少提供一个")
        if ids:
            with self._write_lock:
                self.collection.delete(ids=ids)
                bump_collection_revision(self.persist_directory, self.collection_name)
                self._refresh_engine_state()
        return bool(ids)

    def get_indexed_count(self) -> int:
        return int(self.collection.count())

    def reconcile(
        self,
        expected_files: Sequence[Mapping[str, Any]],
        *,
        remove_orphans: bool = False,
    ) -> Dict[str, Any]:
        """Compare SQLite UUID/source state with the metadata collection."""
        expected = {
            str(record.get("file_id") or record.get("id")): record
            for record in expected_files
            if record.get("file_id") or record.get("id")
        }
        snapshot = self.collection.get(include=["metadatas"])
        ids = [str(value) for value in (snapshot.get("ids") or [])]
        metadatas = snapshot.get("metadatas") or []
        stored = {
            file_id: dict(metadatas[index] or {})
            for index, file_id in enumerate(ids)
        }
        missing = sorted(set(expected) - set(stored))
        orphans = sorted(set(stored) - set(expected))
        stale: List[str] = []
        try:
            configured_fingerprint = self._provider().fingerprint
        except Exception:
            configured_fingerprint = None
        for file_id in sorted(set(expected) & set(stored)):
            expected_source = expected[file_id].get("source_fingerprint")
            actual = stored[file_id]
            if (
                expected_source
                and actual.get("source_fingerprint") != expected_source
            ) or (
                configured_fingerprint
                and actual.get("engine_fingerprint")
                and actual.get("engine_fingerprint") != configured_fingerprint
            ):
                stale.append(file_id)
        if remove_orphans and orphans:
            with self._write_lock:
                self.collection.delete(ids=orphans)
                bump_collection_revision(self.persist_directory, self.collection_name)
                self._refresh_engine_state()
        return {
            "missing": missing,
            "stale": stale,
            "orphans": orphans,
            "removed_orphans": len(orphans) if remove_orphans else 0,
            "count": self.get_indexed_count(),
        }

    def get_manifest(self) -> Dict[str, Any]:
        self._refresh_engine_state()
        target_fingerprint = self._target_engine_fingerprint()
        stored_fingerprint = (
            next(iter(self._engine_fingerprints))
            if not self._unknown_engine_metadata
            and len(self._engine_fingerprints) == 1
            else None
        )
        engine_changed = bool(
            self.get_indexed_count() > 0
            and target_fingerprint
            and stored_fingerprint != target_fingerprint
        )
        if engine_changed:
            self.needs_rebuild = True
        return {
            "collection": self.collection_name,
            "schema": TEXT_COLLECTION_SCHEMA,
            "metric": (getattr(self.collection, "metadata", None) or {}).get("hnsw:space"),
            "count": self.get_indexed_count(),
            "needs_rebuild": self.needs_rebuild,
            "engine_fingerprint": stored_fingerprint or target_fingerprint,
            "target_engine_fingerprint": target_fingerprint,
            "config_fingerprint": get_text_embedding_config_fingerprint(),
            "dimensions": _collection_dimension(self.collection),
            "preprocessing_version": "metadata-text-v1",
        }

    def clear_index(self) -> None:
        """Recreate the text collection with the required cosine metric."""
        with self._write_lock:
            try:
                self.client.delete_collection(name=self.collection_name)
            except Exception:
                logger.debug("Collection %s 已不存在", self.collection_name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=None,
                metadata=_collection_metadata(
                    "SoundBot searchable file metadata", TEXT_COLLECTION_SCHEMA
                ),
            )
            self._refresh_engine_state()
            bump_collection_revision(self.persist_directory, self.collection_name)


_indexers: Dict[Tuple[str, str], AudioIndexer] = {}
_text_indexers: Dict[Tuple[str, str], TextMetadataIndexer] = {}
_indexers_lock = threading.RLock()


def _project_id(project_id: Optional[str]) -> str:
    return str(project_id or config.CURRENT_PROJECT_ID)


def get_indexer(
    project_id: str = None,
    collection_name: str = "audio_embeddings",
) -> AudioIndexer:
    project_id = _project_id(project_id)
    key = (project_id, str(collection_name))
    with _indexers_lock:
        if key not in _indexers:
            path = safe_project_chroma_path(project_id)
            _indexers[key] = AudioIndexer(
                persist_directory=str(path),
                collection_name=collection_name,
                project_id=project_id,
            )
        return _indexers[key]


def get_text_indexer(
    project_id: str = None,
    provider: Optional[TextEmbeddingProvider] = None,
    collection_name: str = "text_metadata_embeddings",
) -> TextMetadataIndexer:
    project_id = _project_id(project_id)
    key = (project_id, str(collection_name))
    with _indexers_lock:
        if key not in _text_indexers:
            path = safe_project_chroma_path(project_id)
            _text_indexers[key] = TextMetadataIndexer(
                persist_directory=str(path),
                collection_name=collection_name,
                provider=provider,
                project_id=project_id,
            )
        elif provider is not None:
            _text_indexers[key].provider = provider
            _text_indexers[key]._provider_override = True
        return _text_indexers[key]


def reset_indexer(project_id: str = None) -> None:
    project_id = _project_id(project_id)
    with _indexers_lock:
        for key in [key for key in _indexers if key[0] == project_id]:
            _indexers.pop(key, None)
        for key in [key for key in _text_indexers if key[0] == project_id]:
            _text_indexers.pop(key, None)


def reset_all_indexers() -> None:
    with _indexers_lock:
        _indexers.clear()
        _text_indexers.clear()


def delete_project_index(project_id: str) -> bool:
    try:
        project_id = _project_id(project_id)
        db_path = safe_project_chroma_path(project_id, create=False)
        reset_indexer(project_id)
        reset_chroma_client(str(db_path))
        gc.collect()
        if db_path.exists():
            shutil.rmtree(db_path)
        return True
    except Exception as exc:
        logger.error("删除工程 %s 的向量数据库失败: %s", project_id, exc)
        return False
