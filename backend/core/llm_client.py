"""
大语言模型客户端

支持 OpenAI API 和本地 Ollama 模型。
提供统一的 chat() 接口。
"""

import json
from typing import Optional, List, Dict, Any
import requests

import config
from utils.logger import get_logger

logger = get_logger(__name__)


class LLMClient:
    """大语言模型客户端"""
    
    def __init__(self):
        self.provider = config.LLM_PROVIDER
        self.api_key = config.LLM_API_KEY
        self.model = config.LLM_MODEL
        self.base_url = config.LLM_BASE_URL
        self.local_url = config.LOCAL_LLM_URL
        self.local_model = config.LOCAL_LLM_MODEL
        
    def chat(
        self, 
        message: str, 
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> str:
        """
        发送聊天请求
        
        Args:
            message: 用户消息
            system_prompt: 系统提示词
            temperature: 温度参数
            max_tokens: 最大 tokens
            
        Returns:
            模型回复
        """
        if self.provider == "openai":
            return self._chat_openai(message, system_prompt, temperature, max_tokens)
        elif self.provider == "anthropic":
            return self._chat_anthropic(message, system_prompt, temperature, max_tokens)
        elif self.provider == "local":
            return self._chat_local(message, system_prompt, temperature, max_tokens)
        else:
            raise ValueError(f"不支持的 LLM 提供商: {self.provider}")
    
    def _chat_openai(
        self,
        message: str,
        system_prompt: Optional[str],
        temperature: float,
        max_tokens: int
    ) -> str:
        """OpenAI API 调用"""
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY 未设置")
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        base_url = self.base_url or "https://api.openai.com/v1"
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120
            )
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenAI API 调用失败: {e}")
            raise RuntimeError(f"OpenAI API 调用失败: {e}")
    
    def _chat_anthropic(
        self,
        message: str,
        system_prompt: Optional[str],
        temperature: float,
        max_tokens: int
    ) -> str:
        """Anthropic API 调用"""
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY 未设置")
        
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        
        base_url = self.base_url or "https://api.anthropic.com/v1"
        
        messages = [{"role": "user", "content": message}]
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        if system_prompt:
            payload["system"] = system_prompt
        
        try:
            response = requests.post(
                f"{base_url}/messages",
                headers=headers,
                json=payload,
                timeout=120
            )
            response.raise_for_status()
            result = response.json()
            return result["content"][0]["text"]
        except requests.exceptions.RequestException as e:
            logger.error(f"Anthropic API 调用失败: {e}")
            raise RuntimeError(f"Anthropic API 调用失败: {e}")
    
    def _chat_local(
        self,
        message: str,
        system_prompt: Optional[str],
        temperature: float,
        max_tokens: int
    ) -> str:
        """本地 Ollama 模型调用"""
        payload = {
            "model": self.local_model,
            "prompt": message,
            "temperature": temperature,
            "stream": False
        }
        
        if system_prompt:
            payload["system"] = system_prompt
        
        try:
            response = requests.post(
                self.local_url,
                json=payload,
                timeout=300
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
        except requests.exceptions.RequestException as e:
            logger.error(f"本地模型调用失败: {e}")
            raise RuntimeError(f"本地模型调用失败: {e}")
    
    def is_available(self) -> bool:
        """
        检查 LLM 服务是否可用
        
        Returns:
            是否可用
        """
        if self.provider == "local":
            try:
                response = requests.get(
                    self.local_url.replace("/api/generate", "/api/tags"),
                    timeout=5
                )
                return response.status_code == 200
            except:
                return False
        else:
            return bool(self.api_key)


# 全局单例
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端单例"""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
