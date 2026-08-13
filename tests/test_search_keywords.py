from __future__ import annotations

from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import search_engine as search_engine_module
from core.search_engine import OptimizedAudioSearcher, QueryCache


class KeywordTagTests(unittest.TestCase):
    def score(self, tags):
        return OptimizedAudioSearcher._keyword_match_score(
            object(),
            "explosion",
            "unrelated.wav",
            {"metadata_tags": tags},
        )

    def test_sqlite_json_list_tags_are_searchable(self) -> None:
        score, level = self.score('["Explosion", "impact"]')
        self.assertGreaterEqual(score, 0.75)
        self.assertEqual(level, "partial")

    def test_mapping_metadata_tags_are_searchable(self) -> None:
        score, level = self.score({"category": "Explosion"})
        self.assertGreaterEqual(score, 0.75)
        self.assertEqual(level, "partial")

    def test_malformed_tag_payload_does_not_raise(self) -> None:
        score, level = self.score("[")
        self.assertEqual((score, level), (0.0, "none"))

    def test_sqlite_snapshot_exposes_stable_file_uuid(self) -> None:
        record = SimpleNamespace(
            file_uuid="stable-file-uuid",
            path="/sounds/explosion.wav",
            filename="explosion.wav",
            duration=1.0,
            file_size=123,
            sample_rate=48000,
            channels=2,
            tags=["impact"],
        )
        manager = mock.Mock()
        manager.get_files_by_project.return_value = [record]
        searcher = OptimizedAudioSearcher.__new__(OptimizedAudioSearcher)
        searcher.project_id = "project-one"
        with mock.patch("core.database.get_db_manager", return_value=manager):
            snapshot = searcher._get_all_files()
        self.assertEqual(snapshot[0]["file_id"], "stable-file-uuid")
        manager.get_files_by_project.assert_called_once_with("project-one")

    def test_chroma_query_id_overrides_legacy_metadata_id(self) -> None:
        collection = mock.Mock()
        collection.count.return_value = 1
        collection.query.return_value = {
            "ids": [["collection-uuid"]],
            "distances": [[0.1]],
            "metadatas": [[{
                "file_id": "legacy-wrong-id",
                "file_path": "/sounds/explosion.wav",
                "filename": "explosion.wav",
                "duration": 1.0,
                "format": "wav",
            }]],
        }
        searcher = OptimizedAudioSearcher.__new__(OptimizedAudioSearcher)
        searcher.collection = collection
        searcher.needs_rebuild = False
        results = searcher._semantic_search(
            np.array([1.0, 0.0]), query="", top_k=5, min_similarity=0.0
        )
        self.assertEqual(results[0].metadata["file_id"], "collection-uuid")

    def test_stale_sqlite_artifacts_suppress_old_chroma_results(self) -> None:
        searcher = OptimizedAudioSearcher.__new__(OptimizedAudioSearcher)
        searcher.project_id = "project-one"
        results = [
            search_engine_module.SearchResult(
                file_path=f"/sounds/{file_id}.wav",
                filename=f"{file_id}.wav",
                similarity=0.9,
                duration=1.0,
                format="wav",
                metadata={"file_id": file_id},
            )
            for file_id in ("ready-id", "stale-id")
        ]
        manager = mock.Mock()
        manager.get_ready_artifact_ids.return_value = {"ready-id"}
        with mock.patch("core.database.get_db_manager", return_value=manager):
            filtered = searcher._ready_vector_results(results, "audio_vector")

        self.assertEqual([item.metadata["file_id"] for item in filtered], ["ready-id"])
        manager.get_ready_artifact_ids.assert_called_once()


class AsyncKeywordSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_async_uses_no_load_peek_and_one_worker_snapshot(self):
        main_thread = threading.get_ident()
        snapshot_calls = []

        class TextProcessor:
            @staticmethod
            def expand_query(query):
                return [query]

            @staticmethod
            def tokenize(_query):
                return []

        class EmptyCollection:
            @staticmethod
            def count():
                return 0

        searcher = OptimizedAudioSearcher.__new__(OptimizedAudioSearcher)
        searcher.project_id = "project-one"
        searcher.index_revision = 7
        searcher.model_fingerprint = "manifest-clap"
        searcher.persist_directory = "/tmp/project-one"
        searcher.collection_name = "audio_embeddings"
        searcher.collection = EmptyCollection()
        searcher.needs_rebuild = False
        searcher._query_cache = QueryCache()
        searcher._text_processor = TextProcessor()
        searcher._text_searcher = SimpleNamespace(
            needs_rebuild=True,
            collection=EmptyCollection(),
            collection_name="text_metadata_embeddings",
        )
        searcher._configured_text_fingerprint = lambda: "text:test"
        manager = mock.Mock()
        manager.get_artifact_counts.return_value = {
            "audio_vector": {"ready": 0},
            "text_vector": {"ready": 0},
        }

        def take_snapshot(_filters=None):
            snapshot_calls.append(threading.get_ident())
            return [{
                "file_id": "stable-file-uuid",
                "file_path": "/sounds/explosion.wav",
                "filename": "explosion.wav",
                "duration": 1.0,
                "format": "wav",
                "metadata_tags": ["impact"],
            }]

        searcher._get_all_files = take_snapshot
        with (
            mock.patch(
                "core.embedder.get_embedder",
                side_effect=AssertionError("async search must not load CLAP"),
            ),
            mock.patch.object(search_engine_module, "peek_embedder", return_value=None),
            mock.patch.object(
                search_engine_module,
                "get_embedder_fingerprint",
                return_value="clap:unavailable",
            ),
            mock.patch.object(
                search_engine_module, "get_collection_revision", return_value=0
            ),
            mock.patch("core.database.get_db_manager", return_value=manager),
        ):
            results, stats = await searcher.search_async(
                "explosion", top_k=10, min_similarity=0.0, use_cache=False
            )

        self.assertEqual(len(snapshot_calls), 1)
        self.assertNotEqual(snapshot_calls[0], main_thread)
        self.assertTrue(results)
        self.assertEqual(results[0].metadata["file_id"], "stable-file-uuid")
        self.assertFalse(stats["embedder_available"])


if __name__ == "__main__":
    unittest.main()
