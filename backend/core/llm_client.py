# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Nagisa_Huckrick (胡杨)

"""Async OpenAI-compatible LLM and text-embedding client.

Normal chat requests go directly to the requested endpoint; availability is
not probed first.  Explicit connection tests remain available through
``check_available`` and the configuration manager.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from core.llm_config_manager import LLMProvider, get_llm_config_manager
logger = logging.getLogger(__name__)

SUPPORTED_LLM_PROVIDERS = {
    LLMProvider.LM_STUDIO,
    LLMProvider.OLLAMA,
    LLMProvider.OPENAI,
    LLMProvider.KIMI,
    LLMProvider.DEEPSEEK,
    LLMProvider.SILICONFLOW,
    LLMProvider.CUSTOM,
}


class LLMError(RuntimeError):
    """Safe, structured provider error returned across API/IPC boundaries."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        details = dict(self.details)
        if self.status_code is not None:
            details.setdefault("status_code", self.status_code)
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": details,
        }


class LLMClient:
    """Shared async client for the supported OpenAI-compatible providers."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        client: Optional[httpx.AsyncClient] = None,
        max_retries: int = 2,
    ):
        self._config_manager = get_llm_config_manager() if config is None else None
        self._explicit_config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        self.max_retries = max(0, int(max_retries))
        self._load_config()

    def _load_config(self) -> None:
        configured = self._explicit_config or self._config_manager.get_llm_config()
        if "llm" in configured:
            configured = configured["llm"]
        provider = str(configured.get("provider", "lm_studio"))
        if "base_url" in configured:
            provider_cfg = configured
        else:
            provider_cfg = configured.get(provider, {})
        self._config = configured
        self.provider = provider
        self.base_url = str(provider_cfg.get("base_url", "")).rstrip("/")
        self.model = str(provider_cfg.get("model", ""))
        self.api_key = str(provider_cfg.get("api_key", ""))
        self.headers = dict(provider_cfg.get("headers", {}) or {})

    def reload_config(self) -> None:
        self._explicit_config = None
        self._load_config()

    @property
    def is_available(self) -> bool:
        """Configuration readiness only; intentionally performs no network I/O."""
        return self.provider in SUPPORTED_LLM_PROVIDERS and bool(self.base_url)

    def _ensure_supported(self) -> None:
        if self.provider not in SUPPORTED_LLM_PROVIDERS:
            raise LLMError(
                "unsupported_provider",
                f"当前版本未启用 LLM 提供者: {self.provider}",
                retryable=False,
                details={"provider": self.provider},
            )
        if not self.base_url:
            raise LLMError("invalid_config", "LLM API 地址未配置", retryable=False)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update({str(key): str(value) for key, value in self.headers.items()})
        return headers

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    @staticmethod
    def _response_error(response: httpx.Response) -> LLMError:
        status = response.status_code
        if status in {401, 403}:
            code, message, retryable = "authentication_failed", "LLM API 认证失败", False
        elif status == 429:
            code, message, retryable = "rate_limited", "LLM API 请求过于频繁", True
        elif status >= 500:
            code, message, retryable = "provider_unavailable", "LLM 服务暂时不可用", True
        else:
            code, message, retryable = "provider_rejected", f"LLM 请求失败 (HTTP {status})", False
        return LLMError(code, message, retryable=retryable, status_code=status)

    @staticmethod
    def _transport_error(exc: Exception) -> LLMError:
        if isinstance(exc, httpx.TimeoutException):
            return LLMError("timeout", "LLM 请求超时", retryable=True)
        if isinstance(exc, httpx.RequestError):
            return LLMError("connection_failed", "无法连接到 LLM 服务", retryable=True)
        if isinstance(exc, LLMError):
            return exc
        return LLMError("invalid_response", f"LLM 响应处理失败: {exc}", retryable=False)

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(min(1.0, 0.15 * (2**attempt)))

    async def _post_json(
        self, endpoint: str, payload: Dict[str, Any], *, timeout: float = 120.0
    ) -> Dict[str, Any]:
        self._ensure_supported()
        last_error: Optional[LLMError] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(
                    self._url(endpoint),
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                )
                if response.is_error:
                    raise self._response_error(response)
                data = response.json()
                if not isinstance(data, dict):
                    raise LLMError("invalid_response", "LLM 返回了无效 JSON 结构")
                return data
            except Exception as exc:
                error = self._transport_error(exc)
                last_error = error
                if not error.retryable or attempt >= self.max_retries:
                    raise error
                await self._backoff(attempt)
        raise last_error or LLMError("unknown", "LLM 请求失败")

    async def check_available(self) -> bool:
        """Explicit network probe used only by the Test Connection action."""
        if not self.is_available:
            return False
        try:
            response = await self._client.get(
                self._url("models"), headers=self._headers(), timeout=10.0
            )
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = True,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        full_messages: List[Dict[str, str]] = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        try:
            if stream:
                async for chunk in self._chat_stream(full_messages, temperature, max_tokens):
                    yield chunk
            else:
                content = await self._chat_non_stream(full_messages, temperature, max_tokens)
                yield {"type": "content", "content": content}
                yield {"type": "done", "full_content": content}
        except Exception as exc:
            error = self._transport_error(exc)
            logger.warning("LLM 调用失败 [%s]: %s", error.code, error.message)
            yield {"type": "error", "content": error.message, **error.to_dict()}

    @staticmethod
    def _stream_content(data: Dict[str, Any]) -> str:
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] or {}
            delta = choice.get("delta") or {}
            if isinstance(delta, dict):
                return str(delta.get("content") or "")
            return str(choice.get("text") or "")
        message = data.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "")
        return str(data.get("response") or "")

    async def _chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self._ensure_supported()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        full_content = ""
        emitted = False
        for attempt in range(self.max_retries + 1):
            try:
                async with self._client.stream(
                    "POST",
                    self._url("chat/completions"),
                    headers=self._headers(),
                    json=payload,
                    timeout=120.0,
                ) as response:
                    if response.is_error:
                        raise self._response_error(response)
                    # httpx buffers arbitrary TCP chunks into complete lines,
                    # so SSE JSON split across network chunks is preserved.
                    async for raw_line in response.aiter_lines():
                        line = raw_line.strip()
                        if not line or line.startswith(":") or line.startswith("event:"):
                            continue
                        if line.startswith("data:"):
                            line = line[5:].lstrip()
                        if line == "[DONE]":
                            break
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            logger.debug("忽略无法解析的流式行")
                            continue
                        content = self._stream_content(data)
                        if content:
                            emitted = True
                            full_content += content
                            yield {"type": "content", "content": content}
                yield {"type": "done", "full_content": full_content}
                return
            except Exception as exc:
                error = self._transport_error(exc)
                if emitted or not error.retryable or attempt >= self.max_retries:
                    raise error
                await self._backoff(attempt)

    async def _chat_non_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        data = await self._post_json(
            "chat/completions",
            {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") or {}
            return str(message.get("content") or "")
        message = data.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "")
        if "response" in data:
            return str(data.get("response") or "")
        raise LLMError("invalid_response", "LLM 返回了未知响应格式")

    async def embeddings(
        self,
        texts: List[str],
        embedding_config: Optional[Dict[str, Any]] = None,
    ) -> List[List[float]]:
        """Get metadata-text vectors from an OpenAI-compatible endpoint."""
        from core.llm_config_manager import EmbeddingProvider

        if embedding_config is not None:
            emb_config = embedding_config
        elif self._config_manager is not None:
            emb_config = self._config_manager.get_embedding_config()
        else:
            emb_config = get_llm_config_manager().get_embedding_config()
        provider = str(emb_config.get("provider", EmbeddingProvider.DEFAULT))
        if provider == EmbeddingProvider.DEFAULT:
            raise LLMError(
                "wrong_embedding_space",
                "默认 CLAP 文本向量请通过 get_text_embedding_provider 获取",
            )
        if provider not in {EmbeddingProvider.LOCAL, EmbeddingProvider.EXTERNAL}:
            raise LLMError("unsupported_provider", f"不支持的 Embedding 提供者: {provider}")
        cfg = emb_config.get(provider, {})
        base_url = str(cfg.get("base_url", "")).rstrip("/")
        if not base_url:
            raise LLMError("invalid_config", "Embedding API 地址未配置")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        api_key = str(cfg.get("api_key", ""))
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        last_error: Optional[LLMError] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(
                    f"{base_url}/embeddings",
                    headers=headers,
                    json={"model": cfg.get("model", ""), "input": texts},
                    timeout=30.0,
                )
                if response.is_error:
                    raise self._response_error(response)
                items = response.json().get("data", [])
                ordered = sorted(items, key=lambda item: int(item.get("index", 0)))
                vectors = [item["embedding"] for item in ordered]
                if len(vectors) != len(texts):
                    raise LLMError("invalid_response", "Embedding 向量数量不匹配")
                return vectors
            except Exception as exc:
                error = self._transport_error(exc)
                last_error = error
                if not error.retryable or attempt >= self.max_retries:
                    raise error
                await self._backoff(attempt)
        raise last_error or LLMError("unknown", "Embedding 请求失败")

    async def chat_simple(
        self,
        message: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        stream: bool = True,
    ) -> AsyncGenerator[str, None]:
        async for chunk in self.chat(
            messages=[{"role": "user", "content": message}],
            system_prompt=system_prompt,
            temperature=temperature,
            stream=stream,
        ):
            if chunk["type"] == "content":
                yield chunk["content"]
            elif chunk["type"] == "error":
                raise LLMError(
                    chunk.get("code", "llm_error"),
                    chunk.get("message", chunk.get("content", "LLM 调用失败")),
                    retryable=bool(chunk.get("retryable", False)),
                    details=chunk.get("details") or {},
                )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client() -> None:
    """Drop configuration and HTTP connection state after a settings change."""
    global _llm_client
    previous = _llm_client
    _llm_client = None
    if previous is None or not previous._owns_client:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(previous.aclose())
