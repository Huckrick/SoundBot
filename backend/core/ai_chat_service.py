# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Nagisa_Huckrick (胡杨)

"""AI intent routing with deterministic search fallback and project isolation."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

import config
from core.llm_client import get_llm_client
from core.search_engine import get_optimized_searcher_sync, reset_optimized_searcher
from core.searcher import SearchResult
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是 SoundBot 音效管理器的智能助手。
判断用户消息是闲聊还是找音效，只返回一个 JSON 对象。
找音效: {"type":"search","keywords":["english keyword"],"response":"简短说明"}
闲聊: {"type":"chat","response":"简短回复"}
type 只能是 search 或 chat；search 必须提供非空 keywords。"""


class IntentResult(BaseModel):
    type: Literal["search", "chat"] = "search"
    keywords: List[str] = Field(default_factory=list)
    response: str = ""

    @field_validator("keywords", mode="before")
    @classmethod
    def _keywords_are_list(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]


class AIChatService:
    def __init__(self, project_id: Optional[str] = None):
        self._project_id = str(project_id) if project_id else None
        self._llm_client = None
        self._searchers: Dict[str, Any] = {}
        self._searcher_signatures: Dict[str, str] = {}

    def _resolve_project(self, project_id: Optional[str] = None) -> str:
        return str(project_id or self._project_id or config.CURRENT_PROJECT_ID)

    @property
    def project_id(self) -> str:
        return self._resolve_project()

    def set_project(self, project_id: str) -> None:
        """Bind future calls to a project without sharing old search state."""
        self._project_id = str(project_id)

    @property
    def llm_client(self):
        if self._llm_client is None:
            self._llm_client = get_llm_client()
        return self._llm_client

    def get_searcher(self, project_id: Optional[str] = None):
        resolved = self._resolve_project(project_id)
        from core.database import get_db_manager

        db_manager = get_db_manager()
        audio_manifest = db_manager.get_index_manifest(resolved, "audio_vector") or {}
        text_manifest = db_manager.get_index_manifest(resolved, "text_vector") or {}
        audio_collection = audio_manifest.get("collection_name") or "audio_embeddings"
        text_collection = (
            text_manifest.get("collection_name") or "text_metadata_embeddings"
        )
        revision = int(audio_manifest.get("revision", 0)) + int(
            text_manifest.get("revision", 0)
        )
        fingerprint = "|".join(
            filter(
                None,
                (
                    audio_manifest.get("engine_fingerprint"),
                    text_manifest.get("engine_fingerprint"),
                ),
            )
        ) or None
        signature = json.dumps(
            {
                "audio": audio_collection,
                "text": text_collection,
                "revision": revision,
                "fingerprint": fingerprint,
            },
            sort_keys=True,
        )
        if self._searcher_signatures.get(resolved) != signature:
            self._searchers[resolved] = get_optimized_searcher_sync(
                project_id=resolved,
                collection_name=audio_collection,
                text_collection_name=text_collection,
                index_revision=revision,
                model_fingerprint=fingerprint,
            )
            self._searcher_signatures[resolved] = signature
        return self._searchers[resolved]

    @property
    def searcher(self):
        return self.get_searcher()

    def reload(self) -> None:
        self._llm_client = None
        self._searchers.clear()
        self._searcher_signatures.clear()

    @staticmethod
    def _prepare_messages(
        message: str,
        history: Optional[List[Dict[str, str]]],
        *,
        limit: int,
    ) -> List[Dict[str, str]]:
        prepared: List[Dict[str, str]] = []
        for item in (history or [])[-limit:]:
            role = str(item.get("role", ""))
            content = str(item.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                prepared.append({"role": role, "content": content})
        # Some renderer versions include the current user message in history.
        # Do not submit it twice.
        if not prepared or prepared[-1] != {"role": "user", "content": message}:
            prepared.append({"role": "user", "content": message})
        return prepared

    async def chat(
        self,
        message: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        top_k: int = 20,
        threshold: float = 0.1,
        project_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        bound_project = self._resolve_project(project_id)
        try:
            yield {"type": "thinking", "content": "..."}
            if self.llm_client.is_available:
                try:
                    intent = await self._ask_llm(message, conversation_history)
                except Exception as exc:
                    logger.warning("意图模型不可用，直接搜索原始查询: %s", exc)
                    intent = IntentResult(
                        type="search", keywords=[message], response=f"正在搜索: {message}"
                    )
            else:
                intent = IntentResult(
                    type="search", keywords=[message], response=f"正在搜索: {message}"
                )

            if intent.type == "chat":
                response = await self._generate_chat_response(message, conversation_history)
                yield {"type": "chat", "content": response}
                return

            query = " ".join(intent.keywords).strip() or message
            yield {
                "type": "searching",
                "query": query,
                "content": intent.response or f"正在搜索: {query}",
            }
            results = await self._search(
                query, top_k, threshold, project_id=bound_project
            )
            serialized = []
            for result in results:
                item = (
                    result.model_dump()
                    if hasattr(result, "model_dump")
                    else result.dict()
                )
                metadata = dict(item.get("metadata") or {})
                # Stable IDs are required by playback-source and waveform APIs.
                # Keep the metadata copy for backwards compatibility while
                # exposing the public field consumed by the renderer.
                item["file_id"] = metadata.get("file_id")
                serialized.append(item)
            yield {
                "type": "results",
                "results": serialized,
                "count": len(results),
                "summary": self._make_summary(message, results),
                "project_id": bound_project,
            }
        except Exception as exc:
            logger.error("AI 处理失败: %s", exc)
            yield {
                "type": "error",
                "code": "ai_chat_failed",
                "content": f"处理失败: {exc}",
                "retryable": False,
                "details": {"project_id": bound_project},
            }

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        cleaned = cleaned.replace("“", '"').replace("”", '"')
        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(cleaned[index:])
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                continue
        raise ValueError("LLM 未返回有效 JSON 对象")

    async def _ask_llm(
        self, message: str, history: Optional[List[Dict[str, str]]] = None
    ) -> IntentResult:
        messages = self._prepare_messages(message, history, limit=6)
        full_response = ""
        async for chunk in self.llm_client.chat(
            messages=messages,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=256,
            stream=True,
        ):
            if chunk["type"] == "content":
                full_response += chunk["content"]
            elif chunk["type"] == "error":
                raise RuntimeError(chunk.get("message", chunk.get("content", "LLM 错误")))
        try:
            result = IntentResult.model_validate(self._extract_json(full_response))
            if result.type == "search" and not result.keywords:
                result.keywords = [message]
            return result
        except (ValidationError, ValueError) as exc:
            logger.warning("意图 JSON 无效，回退原始查询: %s", exc)
            return IntentResult(type="search", keywords=[message], response=f"搜索: {message}")

    async def _search(
        self,
        query: str,
        top_k: int,
        threshold: float,
        *,
        project_id: Optional[str] = None,
    ) -> List[SearchResult]:
        try:
            searcher = self.get_searcher(project_id)
            results, stats = await searcher.search_async(
                query=query,
                top_k=top_k,
                min_similarity=threshold,
                use_cache=True,
            )
            logger.info(
                "AI 搜索 '%s': project=%s count=%s cache=%s",
                query,
                self._resolve_project(project_id),
                len(results),
                stats.get("cache_hit", False),
            )
            return results
        except Exception as exc:
            logger.error("搜索失败: %s", exc)
            return []

    def _make_summary(self, query: str, results: List[SearchResult]) -> str:
        count = len(results)
        if count == 0:
            return f"没找到「{query}」相关的音效，换个词试试？"
        if count == 1:
            return f"找到 1 个音效: {results[0].filename}"
        return f"找到 {count} 个相关音效"

    async def _generate_chat_response(
        self, message: str, history: Optional[List[Dict[str, str]]] = None
    ) -> str:
        messages = self._prepare_messages(message, history, limit=10)
        full_response = ""
        async for chunk in self.llm_client.chat(
            messages=messages,
            system_prompt="你是一个有帮助的 AI 助手。请简洁、直接地回答用户。",
            temperature=0.7,
            max_tokens=512,
            stream=True,
        ):
            if chunk["type"] == "content":
                full_response += chunk["content"]
            elif chunk["type"] == "error":
                return "抱歉，我现在无法连接到 AI 服务。你仍然可以直接输入关键词搜索音效。"
        return full_response.strip() or "你好！需要找什么音效吗？"


async def stream_to_sse(
    generator: AsyncGenerator[Dict[str, Any], None]
) -> AsyncGenerator[str, None]:
    try:
        async for chunk in generator:
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as exc:
        logger.error("SSE 流转换错误: %s", exc)
        error = {
            "type": "error",
            "code": "sse_stream_failed",
            "content": str(exc),
            "retryable": False,
            "details": {},
        }
        yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"


_ai_chat_services: Dict[str, AIChatService] = {}


def get_ai_chat_service(project_id: Optional[str] = None) -> AIChatService:
    resolved = str(project_id or config.CURRENT_PROJECT_ID)
    if resolved not in _ai_chat_services:
        _ai_chat_services[resolved] = AIChatService(project_id=resolved)
    return _ai_chat_services[resolved]


def reset_ai_chat_service(project_id: Optional[str] = None) -> None:
    if project_id is None:
        for service in _ai_chat_services.values():
            service.reload()
        _ai_chat_services.clear()
        reset_optimized_searcher()
        return
    service = _ai_chat_services.pop(str(project_id), None)
    if service is not None:
        service.reload()
    reset_optimized_searcher(str(project_id))
