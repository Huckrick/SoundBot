# -*- coding: utf-8 -*-
"""Project-scoped active and shadow collection lifecycle.

SQLite owns the active collection pointer. Rebuilds write uniquely named
cosine collections, validate them, and switch the manifest in one SQLite
transaction. Existing collections are deliberately retained so a failed or
cancelled rebuild cannot destroy working search data.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import uuid
from typing import Any, Dict, Iterable, Optional

from core.database import DatabaseManager
from core.indexer import AudioIndexer, TextMetadataIndexer, get_indexer, get_text_indexer


DEFAULT_AUDIO_COLLECTION = "audio_embeddings"
DEFAULT_TEXT_COLLECTION = "text_metadata_embeddings"
_COLLECTION_TOKEN = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class ActiveCollections:
    audio: str = DEFAULT_AUDIO_COLLECTION
    text: str = DEFAULT_TEXT_COLLECTION


def resolve_active_collections(
    db_manager: DatabaseManager, project_id: str
) -> ActiveCollections:
    audio_manifest = db_manager.get_index_manifest(project_id, "audio_vector") or {}
    text_manifest = db_manager.get_index_manifest(project_id, "text_vector") or {}
    return ActiveCollections(
        audio=str(audio_manifest.get("collection_name") or DEFAULT_AUDIO_COLLECTION),
        text=str(text_manifest.get("collection_name") or DEFAULT_TEXT_COLLECTION),
    )


def get_active_audio_indexer(
    db_manager: DatabaseManager, project_id: str
) -> AudioIndexer:
    active = resolve_active_collections(db_manager, project_id)
    return get_indexer(project_id, collection_name=active.audio)


def get_active_text_indexer(
    db_manager: DatabaseManager, project_id: str
) -> TextMetadataIndexer:
    active = resolve_active_collections(db_manager, project_id)
    return get_text_indexer(project_id, collection_name=active.text)


def shadow_collection_name(kind: str, token: Optional[str] = None) -> str:
    if kind not in {"audio_vector", "text_vector"}:
        raise ValueError(f"invalid vector kind: {kind}")
    prefix = "audio" if kind == "audio_vector" else "text"
    raw_token = token or uuid.uuid4().hex
    safe_token = _COLLECTION_TOKEN.sub("_", str(raw_token)).strip("_-")[:40]
    if not safe_token:
        safe_token = uuid.uuid4().hex
    return f"{prefix}_shadow_{safe_token}"


def create_shadow_indexer(project_id: str, kind: str, token: Optional[str] = None):
    collection_name = shadow_collection_name(kind, token)
    if kind == "audio_vector":
        return get_indexer(project_id, collection_name=collection_name)
    if kind == "text_vector":
        return get_text_indexer(project_id, collection_name=collection_name)
    raise ValueError(f"invalid vector kind: {kind}")


def verified_manifest(indexer: Any, expected_count: int) -> Dict[str, Any]:
    manifest = dict(indexer.get_manifest())
    actual_count = int(manifest.get("count", -1))
    if manifest.get("needs_rebuild"):
        raise RuntimeError("shadow collection is not cosine-compatible")
    if manifest.get("metric") != "cosine":
        raise RuntimeError("shadow collection metric is not cosine")
    if actual_count != int(expected_count):
        raise RuntimeError(
            f"shadow collection count mismatch: expected={expected_count} actual={actual_count}"
        )
    collection_name = str(manifest.get("collection") or "")
    if "_shadow_" not in collection_name:
        raise RuntimeError("refusing to activate a non-shadow collection")
    return {
        "collection_name": collection_name,
        "engine_fingerprint": manifest.get("engine_fingerprint"),
        "model_id": manifest.get("model_id"),
        "model_revision": manifest.get("model_revision"),
        "dimensions": manifest.get("dimensions"),
        "preprocessing_version": manifest.get("preprocessing_version"),
        "metric": "cosine",
    }


def activate_verified_shadows(
    db_manager: DatabaseManager,
    project_id: str,
    shadows: Dict[str, Any],
    expected_counts: Dict[str, int],
) -> Dict[str, Dict[str, Any]]:
    manifests = {
        kind: verified_manifest(indexer, expected_counts[kind])
        for kind, indexer in shadows.items()
    }
    return db_manager.activate_index_manifests(project_id, manifests)


def vector_kinds(kinds: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        kind for kind in dict.fromkeys(kinds)
        if kind in {"audio_vector", "text_vector"}
    )
