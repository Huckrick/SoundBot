from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.ai_chat_service import AIChatService
from core.searcher import SearchResult


class UnavailableLLM:
    is_available = False


class AIChatServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_current_user_message_is_not_duplicated(self):
        messages = AIChatService._prepare_messages(
            "find rain",
            [
                {"role": "user", "content": "older"},
                {"role": "assistant", "content": "response"},
                {"role": "user", "content": "find rain"},
            ],
            limit=10,
        )
        self.assertEqual(messages[-1], {"role": "user", "content": "find rain"})
        self.assertEqual(
            sum(item == {"role": "user", "content": "find rain"} for item in messages),
            1,
        )
        self.assertIn({"role": "assistant", "content": "response"}, messages)

    def test_searchers_are_cached_by_project(self):
        created = []

        def factory(*, project_id, **_kwargs):
            value = object()
            created.append((project_id, value))
            return value

        service = AIChatService(project_id="p1")
        manager = mock.Mock()
        manager.get_index_manifest.return_value = None
        with (
            mock.patch(
                "core.ai_chat_service.get_optimized_searcher_sync",
                side_effect=factory,
            ),
            mock.patch("core.database.get_db_manager", return_value=manager),
        ):
            first = service.get_searcher("p1")
            again = service.get_searcher("p1")
            second = service.get_searcher("p2")
        self.assertIs(first, again)
        self.assertIsNot(first, second)
        self.assertEqual([item[0] for item in created], ["p1", "p2"])

    async def test_unavailable_llm_falls_back_to_raw_project_search(self):
        service = AIChatService(project_id="project-a")
        service._llm_client = UnavailableLLM()
        with mock.patch.object(service, "_search", return_value=[]) as search:
            chunks = [chunk async for chunk in service.chat("暴雨", top_k=5)]
        search.assert_awaited_once_with(
            "暴雨", 5, 0.1, project_id="project-a"
        )
        searching = next(chunk for chunk in chunks if chunk["type"] == "searching")
        results = next(chunk for chunk in chunks if chunk["type"] == "results")
        self.assertEqual(searching["query"], "暴雨")
        self.assertEqual(results["project_id"], "project-a")

    async def test_results_expose_stable_file_id_for_transcoded_playback(self):
        service = AIChatService(project_id="project-a")
        service._llm_client = UnavailableLLM()
        result = SearchResult(
            file_path="C:/Audio/impact.wma",
            filename="impact.wma",
            similarity=0.9,
            duration=1.0,
            format="wma",
            metadata={"file_id": "stable-wma-id"},
        )
        with mock.patch.object(service, "_search", return_value=[result]):
            chunks = [chunk async for chunk in service.chat("impact")]

        payload = next(chunk for chunk in chunks if chunk["type"] == "results")
        self.assertEqual(payload["results"][0]["file_id"], "stable-wma-id")
        self.assertEqual(
            payload["results"][0]["metadata"]["file_id"], "stable-wma-id"
        )

    def test_json_extractor_accepts_fenced_and_prefixed_object(self):
        value = AIChatService._extract_json(
            'analysis first\n```json\n{"type":"search","keywords":["rain"]}\n```'
        )
        self.assertEqual(value["keywords"], ["rain"])


if __name__ == "__main__":
    unittest.main()
