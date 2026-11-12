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
from models.schemas import AIChatRequest
from pydantic import ValidationError


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
        result = {"file_path": "C:/Audio/impact.wma", "filename": "impact.wma",
                  "similarity": 0.9, "duration": 1.0, "format": "wma",
                  "metadata": {"file_id": "stable-wma-id"}}
        with mock.patch.object(service, "_search", return_value=[result]):
            chunks = [chunk async for chunk in service.chat("impact")]

        payload = next(chunk for chunk in chunks if chunk["type"] == "results")
        self.assertEqual(payload["results"][0]["file_id"], "stable-wma-id")
        self.assertEqual(
            payload["results"][0]["metadata"]["file_id"], "stable-wma-id"
        )

    async def test_conversation_reply_uses_validated_locale(self):
        class ChatLLM:
            is_available = True

            def __init__(self):
                self.prompts = []

            async def chat(self, **options):
                self.prompts.append(options["system_prompt"])
                if len(self.prompts) == 1:
                    yield {"type": "content", "content": '{"type":"chat"}'}
                else:
                    yield {"type": "content", "content": "¡Hola!"}

        request = AIChatRequest(message="Hello", locale="es")
        service = AIChatService(project_id="project-a")
        service._llm_client = llm = ChatLLM()
        chunks = [chunk async for chunk in service.chat(request.message, locale=request.locale)]
        self.assertIn("Reply in Spanish.", llm.prompts[-1])
        self.assertEqual(chunks[-1], {"type": "chat", "content": "¡Hola!"})
        with self.assertRaises(ValidationError):
            AIChatRequest(message="Hello", locale="unsupported")

    def test_json_extractor_accepts_fenced_and_prefixed_object(self):
        value = AIChatService._extract_json(
            'analysis first\n```json\n{"type":"search","keywords":["rain"]}\n```'
        )
        self.assertEqual(value["keywords"], ["rain"])


if __name__ == "__main__":
    unittest.main()
