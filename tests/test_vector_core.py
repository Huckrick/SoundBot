from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import indexer as indexer_module
from core.indexer import (
    AudioIndexer,
    TextMetadataIndexer,
    _collection_dimension,
    file_source_fingerprint,
    normalize_file_path,
    safe_project_chroma_path,
)
from core.searcher import (
    AudioSearcher,
    MetadataTextSearcher,
    build_chroma_where,
    cosine_similarity_from_distance,
)


class FakeCollection:
    def __init__(self, metadata=None):
        self.metadata = metadata or {"hnsw:space": "cosine"}
        self.rows = {}
        self.last_where = None

    def count(self):
        return len(self.rows)

    def upsert(self, ids, embeddings, metadatas):
        for file_id, embedding, metadata in zip(ids, embeddings, metadatas):
            self.rows[file_id] = {
                "embedding": embedding,
                "metadata": dict(metadata),
            }

    def get(self, ids=None, where=None, include=None, **_kwargs):
        selected = list(self.rows)
        if ids is not None:
            selected = [file_id for file_id in ids if file_id in self.rows]
        if where and "normalized_path" in where:
            expected = where["normalized_path"].get("$eq")
            selected = [
                file_id
                for file_id in selected
                if self.rows[file_id]["metadata"].get("normalized_path") == expected
            ]
        result = {
            "ids": selected,
            "metadatas": [self.rows[file_id]["metadata"] for file_id in selected],
        }
        if include and "embeddings" in include:
            result["embeddings"] = [
                self.rows[file_id]["embedding"] for file_id in selected
            ]
        return result

    def delete(self, ids):
        for file_id in ids:
            self.rows.pop(file_id, None)

    def query(self, query_embeddings, n_results, where=None, include=None):
        self.last_where = where
        selected = list(self.rows)[:n_results]
        return {
            "ids": [selected],
            "metadatas": [[self.rows[file_id]["metadata"] for file_id in selected]],
            "distances": [[0.2 for _ in selected]],
        }


class FakeClient:
    def __init__(self):
        self.collections = {}

    def get_or_create_collection(self, name, metadata, embedding_function=None):
        if name not in self.collections:
            self.collections[name] = FakeCollection(dict(metadata))
        return self.collections[name]

    def delete_collection(self, name):
        self.collections.pop(name, None)


class FakeEmbedder:
    def __init__(self, fingerprint="clap:test"):
        self.fingerprint = fingerprint

    def audio_to_embedding(self, _path):
        return np.array([3.0, 4.0], dtype=np.float32)


class FakeTextProvider:
    def __init__(self, fingerprint="text:test"):
        self.fingerprint = fingerprint
        self.calls = 0

    async def embed_texts(self, texts):
        self.calls += 1
        return [np.array([1.0, index + 1.0]) for index, _ in enumerate(texts)]


class VectorCoreTests(unittest.TestCase):
    def test_reset_chroma_client_closes_cached_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            key = os.path.normcase(str(Path(directory).resolve()))
            client = mock.Mock()
            indexer_module._chroma_clients[key] = client
            indexer_module.reset_chroma_client(directory)

        client.close.assert_called_once_with()
        self.assertNotIn(key, indexer_module._chroma_clients)

    def test_windows_path_key_normalizes_case_and_separators(self):
        self.assertEqual(
            normalize_file_path(r"C:\声音\FX\Impact.WAV"),
            normalize_file_path("c:/声音/fx/impact.wav"),
        )

    def test_project_path_rejects_escape_before_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(indexer_module.config, "get_user_data_dir", return_value=root):
                resolved = safe_project_chroma_path("legacy-project_01")
                self.assertEqual(resolved.parent, (root / "chroma_projects").resolve())
                for invalid in (
                    "../escape", "..", "a/b", r"C:\escape", "", "CON", "project."
                ):
                    with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                        safe_project_chroma_path(invalid)
            self.assertFalse((root / "escape").exists())

    def test_multi_filter_is_a_valid_and_tree(self):
        where = build_chroma_where(
            {"duration": {"$gte": 1.0, "$lte": 3.0}, "format": "wav"}
        )
        self.assertEqual(
            where,
            {
                "$and": [
                    {"duration": {"$gte": 1.0}},
                    {"duration": {"$lte": 3.0}},
                    {"format": {"$eq": "wav"}},
                ]
            },
        )

    def test_cosine_distance_conversion_is_one_minus_distance(self):
        self.assertAlmostEqual(cosine_similarity_from_distance(0.2), 0.8)
        self.assertAlmostEqual(cosine_similarity_from_distance(1.5), -0.5)

    def test_audio_index_uses_upsert_and_collection_as_authority(self):
        fake_client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tone.wav"
            source.write_bytes(b"wave bytes")
            with (
                mock.patch.object(indexer_module, "get_chroma_client", return_value=fake_client),
                mock.patch.object(indexer_module, "get_embedder", return_value=FakeEmbedder()),
            ):
                indexer = AudioIndexer(directory, project_id="p1")
                metadata = {
                    "file_id": "sqlite-uuid",
                    "filename": source.name,
                    "duration": 1.0,
                    "sample_rate": 48000,
                    "channels": 1,
                    "format": "wav",
                    "size": source.stat().st_size,
                }
                self.assertTrue(indexer.add_single_audio(str(source), metadata))
                self.assertTrue(indexer.add_single_audio(str(source), metadata))

            self.assertEqual(indexer.get_indexed_count(), 1)
            stored = indexer.indexed_files_meta["sqlite-uuid"]
            self.assertEqual(stored["engine_fingerprint"], "clap:test")
            self.assertEqual(stored["source_fingerprint"], file_source_fingerprint(str(source)))
            self.assertEqual(_collection_dimension(indexer.collection), 2)
            self.assertEqual(indexer.get_manifest()["dimensions"], 2)
            self.assertFalse((Path(directory) / "indexed_files_meta.json").exists())

    def test_legacy_l2_collection_is_marked_and_not_mixed_with_new_vectors(self):
        fake_client = FakeClient()
        fake_client.collections["audio_embeddings"] = FakeCollection(
            {"description": "legacy collection"}
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "legacy.wav"
            source.write_bytes(b"legacy")
            with (
                mock.patch.object(indexer_module, "get_chroma_client", return_value=fake_client),
                mock.patch.object(indexer_module, "get_embedder", return_value=FakeEmbedder()),
            ):
                indexer = AudioIndexer(directory)
                accepted = indexer.add_single_audio(
                    str(source), {"filename": source.name, "file_id": "legacy-id"}
                )
        self.assertTrue(indexer.needs_rebuild)
        self.assertFalse(accepted)
        self.assertEqual(indexer.get_indexed_count(), 0)

    def test_audio_index_rejects_model_change_until_collection_is_empty(self):
        fake_client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.wav"
            second = Path(directory) / "second.wav"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            current = FakeEmbedder("clap:model-a")
            with (
                mock.patch.object(indexer_module, "get_chroma_client", return_value=fake_client),
                mock.patch.object(indexer_module, "get_embedder", side_effect=lambda: current),
            ):
                indexer = AudioIndexer(directory)
                self.assertTrue(
                    indexer.add_single_audio(
                        str(first), {"file_id": "first", "filename": first.name}
                    )
                )
                current = FakeEmbedder("clap:model-b")
                self.assertFalse(
                    indexer.add_single_audio(
                        str(second), {"file_id": "second", "filename": second.name}
                    )
                )
                self.assertTrue(indexer.needs_rebuild)
                self.assertEqual(indexer.get_indexed_count(), 1)

                self.assertTrue(indexer.remove_audio(str(first), file_id="first"))
                self.assertFalse(indexer.needs_rebuild)
                self.assertTrue(
                    indexer.add_single_audio(
                        str(second), {"file_id": "second", "filename": second.name}
                    )
                )
                self.assertEqual(
                    indexer.indexed_files_meta["second"]["engine_fingerprint"],
                    "clap:model-b",
                )

    def test_audio_manifest_routes_loaded_model_change_to_shadow_rebuild(self):
        fake_client = FakeClient()
        collection = fake_client.get_or_create_collection(
            "audio_embeddings", {"hnsw:space": "cosine"}
        )
        collection.upsert(
            ids=["old-vector"],
            embeddings=[[1.0, 0.0]],
            metadatas=[
                {
                    "file_path": "/sounds/old.wav",
                    "filename": "old.wav",
                    "engine_fingerprint": "clap:model-a",
                }
            ],
        )
        with (
            mock.patch.object(indexer_module, "get_chroma_client", return_value=fake_client),
            mock.patch.object(
                indexer_module,
                "get_embedder_fingerprint",
                return_value="clap:model-b",
            ),
            mock.patch.object(
                indexer_module,
                "get_clap_engine_manifest",
                return_value={
                    "engine_fingerprint": "clap:model-b",
                    "model_id": "test/clap",
                    "model_revision": "revision-b",
                    "preprocessing_version": "test-v2",
                    "dimensions": 2,
                },
            ),
        ):
            manifest = AudioIndexer("/tmp/audio-model-restart").get_manifest()

        self.assertTrue(manifest["needs_rebuild"])
        self.assertEqual(manifest["engine_fingerprint"], "clap:model-a")
        self.assertEqual(manifest["target_engine_fingerprint"], "clap:model-b")

    def test_audio_manifest_does_not_guess_revision_before_model_is_loaded(self):
        fake_client = FakeClient()
        collection = fake_client.get_or_create_collection(
            "audio_embeddings", {"hnsw:space": "cosine"}
        )
        collection.upsert(
            ids=["existing"],
            embeddings=[[1.0, 0.0]],
            metadatas=[{"engine_fingerprint": "clap:model-a"}],
        )
        with (
            mock.patch.object(indexer_module, "get_chroma_client", return_value=fake_client),
            mock.patch.object(
                indexer_module,
                "get_embedder_fingerprint",
                return_value="clap:unavailable",
            ),
            mock.patch.object(
                indexer_module,
                "get_clap_engine_manifest",
                return_value={"engine_fingerprint": "package:unloaded-model-b"},
            ),
        ):
            manifest = AudioIndexer("/tmp/audio-model-not-loaded").get_manifest()

        self.assertFalse(manifest["needs_rebuild"])
        self.assertEqual(manifest["engine_fingerprint"], "clap:model-a")
        self.assertIsNone(manifest["target_engine_fingerprint"])

    def test_audio_search_rejects_mixed_fingerprints_beyond_first_row(self):
        fake_client = FakeClient()
        collection = fake_client.get_or_create_collection(
            "audio_embeddings", {"hnsw:space": "cosine"}
        )
        collection.upsert(
            ids=["matching-first", "different-second"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
            metadatas=[
                {
                    "file_path": "/sounds/one.wav",
                    "filename": "one.wav",
                    "engine_fingerprint": "clap:model-a",
                },
                {
                    "file_path": "/sounds/two.wav",
                    "filename": "two.wav",
                    "engine_fingerprint": "clap:model-b",
                },
            ],
        )
        with (
            mock.patch("core.searcher.get_chroma_client", return_value=fake_client),
            mock.patch(
                "core.searcher.get_embedder_fingerprint", return_value="clap:model-a"
            ),
        ):
            searcher = AudioSearcher("/tmp/test-mixed-vectors", project_id="p1")
            results = searcher.search_by_embedding(
                np.array([1.0, 0.0]), top_k=10, min_similarity=0.0
            )
        self.assertTrue(searcher.needs_rebuild)
        self.assertEqual(results, [])

    def test_audio_search_uses_cosine_and_normalized_and_filters(self):
        fake_client = FakeClient()
        collection = fake_client.get_or_create_collection(
            "audio_embeddings", {"hnsw:space": "cosine"}
        )
        collection.upsert(
            ids=["one"],
            embeddings=[[1.0, 0.0]],
            metadatas=[
                {
                    "file_path": "/sounds/one.wav",
                    "filename": "one.wav",
                    "duration": 1.0,
                    "format": "wav",
                    "engine_fingerprint": "clap:test",
                }
            ],
        )
        with (
            mock.patch("core.searcher.get_chroma_client", return_value=fake_client),
            mock.patch(
                "core.searcher.get_embedder_fingerprint", return_value="clap:test"
            ),
        ):
            searcher = AudioSearcher("/tmp/test-vectors", project_id="p1")
            results = searcher.search_by_embedding(
                np.array([10.0, 0.0]),
                top_k=10,
                min_similarity=0.0,
                filters={"duration": {"$gte": 0.5, "$lte": 2.0}},
            )
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0].similarity, 0.8)
        self.assertEqual(
            collection.last_where,
            {
                "$and": [
                    {"duration": {"$gte": 0.5}},
                    {"duration": {"$lte": 2.0}},
                ]
            },
        )


class TextMetadataIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_metadata_index_upserts_and_clears(self):
        fake_client = FakeClient()
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(indexer_module, "get_chroma_client", return_value=fake_client),
        ):
            source = Path(directory) / "rain.wav"
            source.write_bytes(b"rain")
            indexer = TextMetadataIndexer(
                directory, provider=FakeTextProvider(), project_id="p1"
            )
            result = await indexer.upsert_metadata(
                [
                    {
                        "file_id": "rain-id",
                        "file_path": str(source),
                        "filename": "rain.wav",
                        "tags": ["rain", "weather"],
                    }
                ]
            )
            self.assertEqual(result["indexed"], 1)
            self.assertEqual(indexer.get_indexed_count(), 1)
            self.assertTrue(indexer.remove(file_id="rain-id"))
            self.assertEqual(indexer.get_indexed_count(), 0)
            await indexer.upsert_metadata(
                [{"file_id": "rain-id", "file_path": str(source), "filename": "rain.wav"}]
            )
            indexer.clear_index()
            self.assertEqual(indexer.get_indexed_count(), 0)

    async def test_text_provider_change_requires_rebuild_and_remove_refreshes_state(self):
        fake_client = FakeClient()
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(indexer_module, "get_chroma_client", return_value=fake_client),
        ):
            first = Path(directory) / "first.wav"
            second = Path(directory) / "second.wav"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            provider_a = FakeTextProvider("text:provider-a")
            provider_b = FakeTextProvider("text:provider-b")
            indexer = TextMetadataIndexer(directory, provider=provider_a)
            await indexer.upsert_metadata(
                [{"file_id": "first", "file_path": str(first), "filename": first.name}]
            )
            indexer.provider = provider_b
            with self.assertRaisesRegex(RuntimeError, "shadow rebuild"):
                await indexer.upsert_metadata(
                    [
                        {
                            "file_id": "second",
                            "file_path": str(second),
                            "filename": second.name,
                        }
                    ]
                )
            self.assertEqual(provider_b.calls, 0)
            self.assertTrue(indexer.needs_rebuild)
            self.assertEqual(indexer.get_indexed_count(), 1)

            self.assertTrue(indexer.remove(file_id="first"))
            self.assertFalse(indexer.needs_rebuild)
            await indexer.upsert_metadata(
                [{"file_id": "second", "file_path": str(second), "filename": second.name}]
            )
            self.assertEqual(indexer.get_indexed_count(), 1)

    async def test_text_manifest_routes_provider_change_after_restart_to_shadow(self):
        fake_client = FakeClient()
        collection = fake_client.get_or_create_collection(
            "text_metadata_embeddings", {"hnsw:space": "cosine"}
        )
        collection.upsert(
            ids=["old-vector"],
            embeddings=[[1.0, 0.0]],
            metadatas=[{"engine_fingerprint": "text:provider-a"}],
        )
        with (
            mock.patch.object(indexer_module, "get_chroma_client", return_value=fake_client),
            mock.patch.object(
                indexer_module,
                "get_text_embedding_config_fingerprint",
                return_value="text-config:provider-b",
            ),
        ):
            manifest = TextMetadataIndexer(
                "/tmp/text-provider-restart",
                provider=FakeTextProvider("text:provider-b"),
            ).get_manifest()

        self.assertTrue(manifest["needs_rebuild"])
        self.assertEqual(manifest["engine_fingerprint"], "text:provider-a")
        self.assertEqual(manifest["target_engine_fingerprint"], "text:provider-b")

    async def test_text_manifest_routes_clap_revision_change_to_shadow(self):
        fake_client = FakeClient()
        collection = fake_client.get_or_create_collection(
            "text_metadata_embeddings", {"hnsw:space": "cosine"}
        )
        collection.upsert(
            ids=["old-vector"],
            embeddings=[[1.0, 0.0]],
            metadatas=[{"engine_fingerprint": "clap-text:clap:model-a"}],
        )
        config_manager = mock.Mock()
        config_manager.get_embedding_config.return_value = {"provider": "default"}
        with (
            mock.patch.object(indexer_module, "get_chroma_client", return_value=fake_client),
            mock.patch.object(
                indexer_module,
                "get_embedder_fingerprint",
                return_value="clap:model-b",
            ),
            mock.patch.object(
                indexer_module,
                "get_text_embedding_config_fingerprint",
                return_value="text-config:default",
            ),
            mock.patch(
                "core.llm_config_manager.get_llm_config_manager",
                return_value=config_manager,
            ),
        ):
            manifest = TextMetadataIndexer(
                "/tmp/text-clap-restart"
            ).get_manifest()

        self.assertTrue(manifest["needs_rebuild"])
        self.assertEqual(manifest["engine_fingerprint"], "clap-text:clap:model-a")
        self.assertEqual(
            manifest["target_engine_fingerprint"], "clap-text:clap:model-b"
        )

    async def test_text_search_rejects_mixed_fingerprints_beyond_first_row(self):
        fake_client = FakeClient()
        collection = fake_client.get_or_create_collection(
            "text_metadata_embeddings", {"hnsw:space": "cosine"}
        )
        collection.upsert(
            ids=["matching-first", "different-second"],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
            metadatas=[
                {
                    "file_path": "/sounds/one.wav",
                    "filename": "one.wav",
                    "engine_fingerprint": "text:provider-a",
                },
                {
                    "file_path": "/sounds/two.wav",
                    "filename": "two.wav",
                    "engine_fingerprint": "text:provider-b",
                },
            ],
        )
        provider = FakeTextProvider("text:provider-a")
        with mock.patch("core.searcher.get_chroma_client", return_value=fake_client):
            searcher = MetadataTextSearcher(
                "/tmp/test-mixed-text-vectors", provider=provider
            )
            results = await searcher.search("rain", top_k=10)
        self.assertTrue(searcher.needs_rebuild)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
