from __future__ import annotations

from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.search_engine import OptimizedAudioSearcher, QueryCache
from core.searcher import SearchResult


class QueryCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_key_contains_project_filters_revision_and_fingerprint(self):
        cache = QueryCache()
        result = SearchResult(
            file_path="/one.wav",
            filename="one.wav",
            similarity=0.8,
            duration=1.0,
            format="wav",
            metadata={},
        )
        context = {
            "project_id": "p1",
            "filters": {"duration": {"$gte": 1, "$lte": 2}},
            "index_revision": 3,
            "model_fingerprint": "model-a",
        }
        await cache.set("rain", [result], 1, **context)
        self.assertIsNotNone(await cache.get("rain", **context))
        self.assertIsNone(await cache.get("rain", **{**context, "project_id": "p2"}))
        self.assertIsNone(await cache.get("rain", **{**context, "index_revision": 4}))
        self.assertIsNone(
            await cache.get("rain", **{**context, "filters": {"format": "wav"}})
        )

    async def test_nested_dict_order_does_not_change_key(self):
        cache = QueryCache()
        await cache.set(
            "impact",
            [],
            0,
            filters={"format": "wav", "duration": {"$lte": 2, "$gte": 1}},
            project_id="p1",
        )
        cached = await cache.get(
            "impact",
            project_id="p1",
            filters={"duration": {"$gte": 1, "$lte": 2}, "format": "wav"},
        )
        self.assertIsNotNone(cached)


class HybridWeightTests(unittest.TestCase):
    @staticmethod
    def result(score, **metadata):
        return SearchResult(
            file_path="/one.wav",
            filename="one.wav",
            similarity=score,
            duration=1.0,
            format="wav",
            metadata=metadata,
        )

    def test_three_available_branches_use_approved_weights(self):
        searcher = OptimizedAudioSearcher.__new__(OptimizedAudioSearcher)
        merged, weights = searcher._weighted_merge_results(
            [self.result(1.0, keyword_score=1.0)],
            [self.result(0.8, audio_score=0.8)],
            [self.result(0.5, text_score=0.5)],
            top_k=10,
            min_similarity=0.0,
            audio_available=True,
            text_available=True,
        )
        self.assertEqual(weights, {"audio": 0.55, "text": 0.3, "keyword": 0.15})
        self.assertAlmostEqual(merged[0].similarity, 0.8 * 0.55 + 0.5 * 0.3 + 0.15)
        self.assertEqual(merged[0].metadata["audio_score"], 0.8)
        self.assertEqual(merged[0].metadata["text_score"], 0.5)

    def test_unavailable_vector_branches_renormalize_keyword(self):
        searcher = OptimizedAudioSearcher.__new__(OptimizedAudioSearcher)
        merged, weights = searcher._weighted_merge_results(
            [self.result(0.9, keyword_score=0.9)],
            [],
            [],
            top_k=10,
            min_similarity=0.0,
            audio_available=False,
            text_available=False,
        )
        self.assertEqual(weights["keyword"], 1.0)
        self.assertAlmostEqual(merged[0].similarity, 0.9)


if __name__ == "__main__":
    unittest.main()
