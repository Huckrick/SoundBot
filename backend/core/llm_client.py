# -*- coding: utf-8 -*-
"""
统一的大语言模型客户端

支持 OpenAI 兼容格式的 API 调用：
- LM Studio
- Ollama
- 通用 OpenAI 兼容 API

提供流式和非流式两种接口。
"""

import json
from typing import Optional, List, Dict, Any, AsyncGenerator
import requests
import asyncio

from utils.logger import get_logger
from core.llm_config_manager import get_llm_config_manager, LLMProvider

logger = get_logger(__name__)


class LLMClient:
    """统一的大语言模型客户端"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化 LLM 客户端
        
        Args:
            config: LLM 配置（可选，默认从配置管理器读取）
        """
        self._config_manager = get_llm_config_manager()
        
        if config:
            self._config = config
        else:
            self._load_config()
    
    def _load_config(self):
        """从配置管理器加载配置"""
        llm_config = self._config_manager.get_llm_config()
        provider = llm_config.get("provider", "lm_studio")

        # 动态获取 provider 配置
        provider_cfg = llm_config.get(provider, {})
        self.provider = provider
        self.base_url = provider_cfg.get("base_url", "")
        self.model = provider_cfg.get("model", "")
        self.api_key = provider_cfg.get("api_key", "")

        # 特殊处理某些 provider
        if provider == "kimi_coding":
            # Kimi Coding 需要特殊 headers
            self.headers = provider_cfg.get("headers", {
                "User-Agent": "Kimi Claw Plugin"
            })
        else:
            self.headers = {}
    
    def reload_config(self):
        """重新加载配置"""
        self._load_config()
    
    @property
    def is_available(self) -> bool:
        """检查 LLM 服务是否可用"""
        try:
            # 尝试获取模型列表
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            # 合并自定义 headers
            if hasattr(self, 'headers') and self.headers:
                headers.update(self.headers)

            url = self.base_url.rstrip("/") + "/models"
            response = requests.get(url, headers=headers, timeout=5)
            return response.status_code == 200
        except:
            return False
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        stream: bool = True
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        发送聊天请求
        
        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            system_prompt: 系统提示词（可选，会添加到 messages）
            temperature: 温度参数（0-1）
            max_tokens: 最大 tokens
            stream: 是否流式返回
            
        Yields:
            Dict 类型的消息片段，包含:
            - type: "content" | "error" | "done"
            - content: 文本内容（type=content 时）
            - full_content: 完整内容（type=done 时）
        """
        # 构建完整的消息列表
        full_messages = []
        
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        
        full_messages.extend(messages)
        
        try:
            if stream:
                async for chunk in self._chat_stream(
                    messages=full_messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                ):
                    yield chunk
            else:
                content = await self._chat_non_stream(
                    messages=full_messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                yield {"type": "content", "content": content}
                yield {"type": "done", "full_content": content}
                
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            yield {"type": "error", "content": f"LLM 调用失败: {str(e)}"}
    
    async def _chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式调用"""
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 合并自定义 headers
        if hasattr(self, 'headers') and self.headers:
            headers.update(self.headers)

        # 根据 provider 确定 API 格式
        if self.provider in ("kimi_coding", "anthropic"):
            # Anthropic 格式：POST /v1/messages
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True
            }
            url = self.base_url.rstrip("/") + "/messages"
        else:
            # OpenAI 格式：POST /chat/completions
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True
            }
            url = self.base_url.rstrip("/") + "/chat/completions"

        full_content = ""
        
        try:
            # 使用 requests 的流式模式
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                stream=True,
                timeout=120
            )
            response.raise_for_status()
            
            for line in response.iter_lines():
                if not line:
                    continue

                line = line.decode('utf-8')

                # Anthropic SSE 格式: data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "..."}}
                if line.startswith("data: "):
                    data_str = line[6:]  # 去掉 "data: " 前缀

                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)

                        # Anthropic 流式格式
                        if data.get("type") == "content_block_delta":
                            delta = data.get("delta", {})
                            if delta.get("type") == "text_delta":
                                content = delta.get("text", "")
                                if content:
                                    full_content += content
                                    yield {"type": "content", "content": content}

                        # OpenAI 格式
                        elif "choices" in data:
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")

                            if content:
                                full_content += content
                                yield {"type": "content", "content": content}

                        # Ollama 格式
                        elif "message" in data:
                            content = data["message"].get("content", "")
                            if content:
                                full_content += content
                                yield {"type": "content", "content": content}

                    except json.JSONDecodeError:
                        continue

            yield {"type": "done", "full_content": full_content}
            
        except requests.exceptions.RequestException as e:
            logger.error(f"LLM 流式请求失败: {e}")
            raise RuntimeError(f"LLM 请求失败: {str(e)}")
    
    async def _chat_non_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int
    ) -> str:
        """非流式调用"""
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 合并自定义 headers
        if hasattr(self, 'headers') and self.headers:
            headers.update(self.headers)

        # 根据 provider 确定 API 格式
        if self.provider in ("kimi_coding", "anthropic"):
            # Anthropic 格式：POST /v1/messages
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            url = self.base_url.rstrip("/") + "/messages"
        else:
            # OpenAI 格式：POST /chat/completions
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False
            }
            url = self.base_url.rstrip("/") + "/chat/completions"
        
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=120
            )
            response.raise_for_status()

            data = response.json()

            # Anthropic 格式
            if "content" in data and isinstance(data["content"], list):
                # Anthropic 响应：{"content": [{"type": "text", "text": "..."}]}
                for block in data["content"]:
                    if block.get("type") == "text":
                        return block.get("text", "")

            # OpenAI 格式
            if "choices" in data:
                return data["choices"][0]["message"]["content"]

            # Ollama 格式
            elif "message" in data:
                return data["message"]["content"]

            else:
                raise RuntimeError(f"未知的响应格式: {data}")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"LLM 请求失败: {e}")
            raise RuntimeError(f"LLM 请求失败: {str(e)}")
    
    async def embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        获取文本嵌入向量
        
        注意：需要 Embedding 提供者支持 OpenAI 兼容的 /embeddings 端点
        
        Args:
            texts: 文本列表
            
        Returns:
            嵌入向量列表
        """
        from core.llm_config_manager import get_llm_config_manager, EmbeddingProvider
        
        config_manager = get_llm_config_manager()
        emb_config = config_manager.get_embedding_config()
        provider = emb_config.get("provider", "default")
        
        # 默认使用 CLAP 模型
        if provider == EmbeddingProvider.DEFAULT:
            raise NotImplementedError(
                "默认 Embedding 使用 CLAP 模型，请通过 search_engine 获取"
            )
        
        # 使用外部或本地 Embedding API
        if provider == EmbeddingProvider.EXTERNAL:
            cfg = emb_config.get("external", {})
        elif provider == EmbeddingProvider.LOCAL:
            cfg = emb_config.get("local", {})
        else:
            raise RuntimeError(f"不支持的 Embedding 提供者: {provider}")
        
        base_url = cfg.get("base_url", "")
        api_key = cfg.get("api_key", "")
        model = cfg.get("model", "")
        
        if not base_url:
            raise ValueError("Embedding API 地址未配置")
        
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        payload = {
            "model": model,
            "input": texts
        }
        
        url = base_url.rstrip("/") + "/embeddings"
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            if "data" in data:
                # OpenAI 格式
                embeddings = [item["embedding"] for item in data["data"]]
                return embeddings
            else:
                raise RuntimeError(f"未知的 Embedding 响应格式: {data}")
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Embedding 请求失败: {e}")
            raise RuntimeError(f"Embedding 请求失败: {str(e)}")
    
    async def chat_simple(
        self,
        message: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        stream: bool = True
    ) -> AsyncGenerator[str, None]:
        """
        简化的聊天接口（只返回文本片段）
        
        Args:
            message: 用户消息
            system_prompt: 系统提示词
            temperature: 温度参数
            stream: 是否流式
            
        Yields:
            文本片段
        """
        messages = [{"role": "user", "content": message}]
        
        async for chunk in self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            stream=stream
        ):
            if chunk["type"] == "content":
                yield chunk["content"]
            elif chunk["type"] == "error":
                raise RuntimeError(chunk["content"])


# ==================== 全局单例 ====================

_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


def reset_llm_client():
    """重置 LLM 客户端（用于配置更新后）"""
    global _llm_client
    if _llm_client is not None:
        _llm_client.reload_config()
