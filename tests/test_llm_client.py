from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.llm_client import LLMClient


def llm_config(provider="openai"):
    return {
        "provider": provider,
        "base_url": "https://llm.test/v1",
        "api_key": "secret",
        "model": "test-model",
    }


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class LLMClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_stream_chat_is_async_openai_compatible(self):
        async def handler(request: httpx.Request):
            self.assertEqual(request.url.path, "/v1/chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            payload = json.loads(request.content)
            self.assertEqual(payload["messages"][-1]["content"], "hello")
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "world"}}]},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = LLMClient(llm_config(), client=http)
        chunks = [
            chunk
            async for chunk in client.chat(
                [{"role": "user", "content": "hello"}], stream=False
            )
        ]
        self.assertEqual(chunks[0], {"type": "content", "content": "world"})
        self.assertEqual(chunks[1]["full_content"], "world")
        await http.aclose()

    async def test_stream_parser_preserves_json_split_across_network_chunks(self):
        async def handler(_request: httpx.Request):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=ChunkedStream(
                    [
                        b'data: {"choices":[{"delta":{"con',
                        b'tent":"hel"}}]}\n\ndata: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
                        b"data: [DONE]\n\n",
                    ]
                ),
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = LLMClient(llm_config(), client=http)
        chunks = [
            chunk
            async for chunk in client.chat(
                [{"role": "user", "content": "hi"}], stream=True
            )
        ]
        self.assertEqual(
            [chunk["content"] for chunk in chunks if chunk["type"] == "content"],
            ["hel", "lo"],
        )
        self.assertEqual(chunks[-1]["full_content"], "hello")
        await http.aclose()

    async def test_http_error_is_structured_and_does_not_echo_provider_body(self):
        async def handler(_request: httpx.Request):
            return httpx.Response(401, text="secret provider diagnostic")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = LLMClient(llm_config(), client=http, max_retries=0)
        chunks = [
            chunk
            async for chunk in client.chat(
                [{"role": "user", "content": "hi"}], stream=False
            )
        ]
        self.assertEqual(chunks[0]["type"], "error")
        self.assertEqual(chunks[0]["code"], "authentication_failed")
        self.assertFalse(chunks[0]["retryable"])
        self.assertNotIn("secret provider diagnostic", str(chunks[0]))
        await http.aclose()

    async def test_retryable_server_error_retries_before_returning(self):
        calls = 0

        async def handler(_request: httpx.Request):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]}
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = LLMClient(llm_config(), client=http, max_retries=1)
        chunks = [
            chunk
            async for chunk in client.chat(
                [{"role": "user", "content": "hi"}], stream=False
            )
        ]
        self.assertEqual(calls, 2)
        self.assertEqual(chunks[-1]["full_content"], "ok")
        await http.aclose()

    async def test_embedding_response_is_sorted_by_index(self):
        async def handler(request: httpx.Request):
            self.assertEqual(request.url.path, "/v1/embeddings")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0]},
                        {"index": 0, "embedding": [1.0, 0.0]},
                    ]
                },
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = LLMClient(llm_config(), client=http)
        vectors = await client.embeddings(
            ["one", "two"],
            embedding_config={
                "provider": "external",
                "external": {
                    "base_url": "https://llm.test/v1",
                    "api_key": "secret",
                    "model": "embed",
                },
            },
        )
        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])
        await http.aclose()

    async def test_legacy_provider_is_rejected_without_network_request(self):
        calls = 0

        async def handler(_request: httpx.Request):
            nonlocal calls
            calls += 1
            return httpx.Response(200)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = LLMClient(llm_config("anthropic"), client=http)
        self.assertFalse(client.is_available)
        chunks = [
            chunk
            async for chunk in client.chat(
                [{"role": "user", "content": "hi"}], stream=False
            )
        ]
        self.assertEqual(chunks[0]["code"], "unsupported_provider")
        self.assertEqual(calls, 0)
        await http.aclose()


if __name__ == "__main__":
    unittest.main()
