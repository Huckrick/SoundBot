# -*- coding: utf-8 -*-
"""
AI 对话服务 - 自然语言音效搜索
"""

import json
import asyncio
from typing import Optional, List, Dict, Any, AsyncGenerator
from dataclasses import dataclass

from utils.logger import get_logger
from core.llm_client import get_llm_client
from core.search_engine import get_optimized_searcher_sync

logger = get_logger(__name__)


# ==================== 系统提示词 ====================

SYSTEM_PROMPT = """你是 SoundMind 音效管理器的智能助手。

## 你的任务
判断用户消息是"闲聊"还是"找音效"，然后按格式返回 JSON。

## 判断规则
1. **找音效** - 用户提到任何声音、音效、拟声词，或要求找声音
   - "找个爆炸声"
   - "噼里啪啦的篝火"
   - "有没有雨声"
   - "很闷的撞击声"
   - "帮我找音效"
   → 返回 search 格式

2. **闲聊** - 纯聊天、问候、问问题
   - "你好"
   - "你能做什么"
   - "今天天气怎么样"
   - "谢谢"
   → 返回 chat 格式

## 返回格式

**找音效时**:
{
    "type": "search",
    "keywords": ["explosion", "boom"],
    "response": "帮你找爆炸音效"
}

**闲聊时**:
{
    "type": "chat",
    "response": "你好呀！需要找什么音效吗？"
}

**极其重要的规则**：
- type 只能是 "search" 或 "chat"
- search 时必须有 keywords（英文关键词）
- response 字段中绝对不能包含双引号 "，请使用单引号 ' 或避免使用引号
- 如果需要在 response 中引用词语，请用单引号包裹，例如：'爆炸声'、'雨声'
"""


@dataclass
class SearchResult:
    """搜索结果"""
    id: str
    filename: str
    filepath: str
    similarity: float
    duration: float = 0.0
    format: str = ""
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "filepath": self.filepath,
            "similarity": round(self.similarity, 3),
            "duration": self.duration,
            "format": self.format
        }


class AIChatService:
    """AI 对话服务"""
    
    def __init__(self):
        self._llm_client = None
        self._searcher = None
    
    @property
    def llm_client(self):
        if self._llm_client is None:
            self._llm_client = get_llm_client()
        return self._llm_client
    
    @property
    def searcher(self):
        if self._searcher is None:
            self._searcher = get_optimized_searcher_sync()
        return self._searcher
    
    def reload(self):
        self._llm_client = None
        self._searcher = None
    
    async def chat(
        self,
        message: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        top_k: int = 20,
        threshold: float = 0.1
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        处理对话
        
        Yields:
            - {"type": "thinking", "content": "..."}
            - {"type": "chat", "content": "..."}  # 闲聊模式
            - {"type": "searching", "query": "..."}  # 搜索模式
            - {"type": "results", "results": [...], "summary": "..."}
        """
        try:
            yield {"type": "thinking", "content": "..."}
            
            # 调用 LLM 判断意图
            result = await self._ask_llm(message, conversation_history)
            
            if result.get("type") == "chat":
                # 闲聊模式
                yield {
                    "type": "chat",
                    "content": result.get("response", "你好！有什么可以帮你的？")
                }
            else:
                # 搜索模式
                keywords = result.get("keywords", [])
                query = " ".join(keywords) if keywords else message
                
                yield {
                    "type": "searching",
                    "query": query,
                    "content": result.get("response", f"正在搜索: {query}")
                }
                
                # 执行搜索
                results = await self._search(query, top_k, threshold)
                
                # 生成摘要
                summary = self._make_summary(message, results)
                
                yield {
                    "type": "results",
                    "results": [r.to_dict() for r in results],
                    "count": len(results),
                    "summary": summary
                }
            
        except Exception as e:
            logger.error(f"AI 处理失败: {e}")
            yield {"type": "error", "content": f"处理失败: {str(e)}"}
    
    async def _ask_llm(self, message: str, history: Optional[List[Dict]] = None) -> Dict:
        """询问 LLM 判断意图"""
        messages = []
        if history:
            for h in history[-3:]:
                messages.append(h)
        messages.append({"role": "user", "content": message})
        
        full_response = ""
        async for chunk in self.llm_client.chat(
            messages=messages,
            system_prompt=SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=256,
            stream=True
        ):
            if chunk["type"] == "content":
                full_response += chunk["content"]
            elif chunk["type"] == "error":
                raise RuntimeError(f"LLM 错误: {chunk['content']}")
        
        # 解析 JSON
        try:
            # 清理 markdown 代码块
            json_str = full_response.strip()
            
            # 处理 ```json 或 ``` 开头的代码块
            if json_str.startswith("```"):
                # 移除开头的 ``` 或 ```json
                json_str = json_str[3:].strip()
                if json_str.startswith("json"):
                    json_str = json_str[4:].strip()
                # 移除结尾的 ```
                if "```" in json_str:
                    json_str = json_str[:json_str.rfind("```")].strip()
            
            # 修复中文引号
            json_str = json_str.replace('\u201c', '"').replace('\u201d', '"')
            json_str = json_str.replace('\u2018', "'").replace('\u2019', "'")
            
            data = json.loads(json_str)
            
            # 确保有 type 字段
            if "type" not in data:
                data["type"] = "search"
            
            return data
            
        except json.JSONDecodeError as e:
            # JSON 解析失败，默认当作搜索
            logger.warning(f"LLM 返回非 JSON: {e}, 内容: {full_response[:200]}")
            return {"type": "search", "keywords": [message], "response": f"搜索: {message}"}
    
    async def _search(self, query: str, top_k: int, threshold: float) -> List[SearchResult]:
        """执行搜索"""
        try:
            results = self.searcher.search(
                query=query,
                top_k=top_k,
                min_similarity=threshold
            )
            
            return [
                SearchResult(
                    id=r.get("id", ""),
                    filename=r.get("filename", ""),
                    filepath=r.get("filepath", ""),
                    similarity=r.get("similarity", 0),
                    duration=r.get("duration", 0),
                    format=r.get("format", "")
                )
                for r in results
            ]
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return []
    
    def _make_summary(self, query: str, results: List[SearchResult]) -> str:
        """生成搜索摘要"""
        count = len(results)
        if count == 0:
            return f"没找到「{query}」相关的音效，换个词试试？"
        if count == 1:
            return f"找到 1 个音效: {results[0].filename}"
        return f"找到 {count} 个相关音效"


async def stream_to_sse(generator: AsyncGenerator[Dict[str, Any], None]) -> AsyncGenerator[str, None]:
    """
    将异步生成器转换为 SSE 格式
    
    Args:
        generator: 异步生成器，产生字典对象
        
    Yields:
        SSE 格式的字符串
    """
    try:
        async for chunk in generator:
            # 将字典转换为 JSON 字符串
            data = json.dumps(chunk, ensure_ascii=False)
            # SSE 格式: data: {...}\n\n
            yield f"data: {data}\n\n"
        
        # 发送结束标记
        yield "data: [DONE]\n\n"
        
    except Exception as e:
        logger.error(f"SSE 流转换错误: {e}")
        error_chunk = json.dumps({"type": "error", "content": str(e)}, ensure_ascii=False)
        yield f"data: {error_chunk}\n\n"
        yield "data: [DONE]\n\n"


# ==================== 单例 ====================

_ai_chat_service: Optional[AIChatService] = None


def get_ai_chat_service() -> AIChatService:
    global _ai_chat_service
    if _ai_chat_service is None:
        _ai_chat_service = AIChatService()
    return _ai_chat_service


def reset_ai_chat_service():
    global _ai_chat_service
    if _ai_chat_service is not None:
        _ai_chat_service.reload()
