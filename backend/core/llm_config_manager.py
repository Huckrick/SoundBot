# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Nagisa_Huckrick (胡杨)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
LLM 和 Embedding 配置管理器

集中管理 LLM 模型和 Embedding 模型的配置，支持：
- 本地模型：LM Studio、Ollama
- 外部 API：OpenAI、Azure OpenAI、Gemini、Kimi、Anthropic、DeepSeek、SiliconFlow 等
- 默认配置：CLAP 模型（写死）
"""

import json
import os
import re
import copy
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from urllib.parse import urlparse
import httpx

import config


def validate_url(url: str, allowed_schemes: List[str] = None, allow_local: bool = False) -> bool:
    """
    验证 URL 是否安全，防止 SSRF 攻击
    
    Args:
        url: 要验证的 URL
        allowed_schemes: 允许的协议列表，默认为 ['http', 'https']
        allow_local: 是否允许本机/内网地址。本地 LLM/Embedding 服务需要开启。
        
    Returns:
        bool: URL 是否安全
    """
    if not url:
        return False
    
    if allowed_schemes is None:
        allowed_schemes = ['http', 'https']
    
    try:
        parsed = urlparse(url)
        
        # 检查协议
        if parsed.scheme not in allowed_schemes:
            return False
        
        # 检查是否有主机名
        if not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        if re.search(r"(?:api[-_]?key|token|secret)=", parsed.query, re.IGNORECASE):
            return False
        
        # 默认禁止访问内网地址；本地模型服务会显式放开。
        hostname = parsed.hostname.lower()
        
        # 检查是否是 IP 地址
        ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if re.match(ip_pattern, hostname):
            # 检查是否是内网 IP
            ip_parts = hostname.split('.')
            first_octet = int(ip_parts[0])
            second_octet = int(ip_parts[1])
            
            # 10.0.0.0/8
            if first_octet == 10 and not allow_local:
                return False
            # 172.16.0.0/12
            if first_octet == 172 and 16 <= second_octet <= 31 and not allow_local:
                return False
            # 192.168.0.0/16
            if first_octet == 192 and second_octet == 168 and not allow_local:
                return False
            # 127.0.0.0/8 (localhost)
            if first_octet == 127 and not allow_local:
                return False
            # 0.0.0.0
            if hostname == '0.0.0.0':
                return False
        
        # 禁止 localhost 域名
        if hostname in ['localhost', '127.0.0.1', '::1'] and not allow_local:
            return False
        
        return True
    except Exception:
        return False

logger = logging.getLogger(__name__)


_SENSITIVE_HEADER_PATTERN = re.compile(
    r"(?:authorization|api[-_]?key|access[-_]?token|secret)", re.IGNORECASE
)


def _contains_plaintext_secret(value: Any) -> bool:
    """Return whether a config tree contains a non-empty credential value."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized == "api_key" and isinstance(child, str) and child:
                return True
            if normalized == "headers" and isinstance(child, dict):
                if any(
                    _SENSITIVE_HEADER_PATTERN.search(str(header)) and bool(header_value)
                    for header, header_value in child.items()
                ):
                    return True
            if _contains_plaintext_secret(child):
                return True
    elif isinstance(value, list):
        return any(_contains_plaintext_secret(item) for item in value)
    return False


def _config_for_disk(value: Any, *, inside_headers: bool = False) -> Any:
    """Deep-copy a configuration while removing every credential field.

    Runtime clients still receive secrets from the in-memory configuration.  The
    JSON file is metadata-only; Electron safeStorage is the sole persistent
    credential source.
    """
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized == "api_key":
                continue
            if inside_headers and _SENSITIVE_HEADER_PATTERN.search(str(key)):
                continue
            sanitized[key] = _config_for_disk(
                child,
                inside_headers=normalized == "headers",
            )
        return sanitized
    if isinstance(value, list):
        return [_config_for_disk(item, inside_headers=inside_headers) for item in value]
    return copy.deepcopy(value)


# ==================== 常量定义 ====================

# LLM 提供者类型
class LLMProvider:
    # 本地
    LM_STUDIO = "lm_studio"
    OLLAMA = "ollama"
    # 云服务
    OPENAI = "openai"
    AZURE = "azure"
    GEMINI = "gemini"
    KIMI = "kimi"
    KIMI_CODING = "kimi_coding"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    SILICONFLOW = "siliconflow"
    # 自定义
    CUSTOM = "custom"

    # v0.2 exposes only providers with one verified OpenAI-compatible adapter.
    ALL = [LM_STUDIO, OLLAMA, OPENAI, KIMI, DEEPSEEK, SILICONFLOW, CUSTOM]
    LEGACY = [AZURE, GEMINI, KIMI_CODING, ANTHROPIC]


# LLM 提供者元数据（显示名称、默认端点、API 版本等）
LLM_PROVIDER_META: Dict[str, Dict[str, Any]] = {
    "lm_studio": {
        "name": "LM Studio",
        "icon": "server",
        "default_url": "http://localhost:1234/v1",
        "need_api_key": False,
        "auth_type": "none",
        "default_model": "",
        "description": "通过 LM Studio 运行本地大模型",
        "supports_streaming": True,
    },
    "ollama": {
        "name": "Ollama",
        "icon": "cpu",
        "default_url": "http://localhost:11434/v1",
        "need_api_key": False,
        "auth_type": "none",
        "default_model": "",
        "description": "通过 Ollama 运行本地大模型",
        "supports_streaming": True,
    },
    "openai": {
        "name": "OpenAI",
        "icon": "zap",
        "default_url": "https://api.openai.com/v1",
        "need_api_key": True,
        "auth_type": "bearer",
        "default_model": "gpt-4o-mini",
        "description": "使用 OpenAI 官方 API",
        "supports_streaming": True,
    },
    "azure": {
        "name": "Azure OpenAI",
        "icon": "cloud",
        "default_url": "https://YOUR_RESOURCE.openai.azure.com",
        "need_api_key": True,
        "auth_type": "azure",
        "default_model": "",
        "description": "使用 Azure OpenAI 服务",
        "supports_streaming": True,
    },
    "gemini": {
        "name": "Google Gemini",
        "icon": "gem",
        "default_url": "https://generativelanguage.googleapis.com",
        "need_api_key": True,
        "auth_type": "api_key",
        "default_model": "gemini-2.0-flash",
        "description": "使用 Google Gemini 模型",
        "supports_streaming": True,
    },
    "kimi": {
        "name": "Kimi (Moonshot)",
        "icon": "moon",
        "default_url": "https://api.moonshot.cn/v1",
        "need_api_key": True,
        "auth_type": "bearer",
        "default_model": "moonshot-v1-8k",
        "description": "使用 Kimi (Moonshot) AI 模型",
        "supports_streaming": True,
    },
    "kimi_coding": {
        "name": "Kimi Coding",
        "icon": "code-2",
        "default_url": "https://api.kimi.com/coding",
        "need_api_key": True,
        "auth_type": "kimi_claw",
        "default_model": "k2p5",
        "description": "使用 Kimi Coding 专用 API（Anthropic 格式）",
        "supports_streaming": True,
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "icon": "brain",
        "default_url": "https://api.anthropic.com/v1",
        "need_api_key": True,
        "auth_type": "anthropic",
        "default_model": "claude-3-haiku-20240307",
        "description": "使用 Anthropic Claude 系列模型",
        "supports_streaming": True,
    },
    "deepseek": {
        "name": "DeepSeek",
        "icon": "fish",
        "default_url": "https://api.deepseek.com/v1",
        "need_api_key": True,
        "auth_type": "bearer",
        "default_model": "deepseek-chat",
        "description": "使用 DeepSeek 系列模型",
        "supports_streaming": True,
    },
    "siliconflow": {
        "name": "SiliconFlow",
        "icon": "layers",
        "default_url": "https://api.siliconflow.cn/v1",
        "need_api_key": True,
        "auth_type": "bearer",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
        "description": "使用 SiliconFlow 聚合的模型服务",
        "supports_streaming": True,
    },
    "custom": {
        "name": "自定义 API",
        "icon": "settings",
        "default_url": "https://your-api.com/v1",
        "need_api_key": True,
        "auth_type": "bearer",
        "default_model": "",
        "description": "使用其他 OpenAI 兼容格式的 API",
        "supports_streaming": True,
    },
}

for _provider_name, _provider_meta in LLM_PROVIDER_META.items():
    _provider_meta["enabled"] = _provider_name in LLMProvider.ALL


# Embedding 提供者类型
class EmbeddingProvider:
    DEFAULT = "default"  # CLAP 模型（默认，写死）
    LOCAL = "local"      # 本地 Embedding
    EXTERNAL = "external"  # 外部 API

    ALL = [DEFAULT, LOCAL, EXTERNAL]


# Embedding 提供者元数据
EMBEDDING_PROVIDER_META: Dict[str, Dict[str, Any]] = {
    "default": {
        "name": "CLAP (默认)",
        "icon": "music",
        "description": "使用内置的 CLAP 音频-文本嵌入模型",
        "need_api_key": False,
    },
    "local": {
        "name": "本地模型",
        "icon": "server",
        "description": "使用 LM Studio 或 Ollama 的 Embedding 模型",
        "need_api_key": False,
    },
    "external": {
        "name": "外部 API",
        "icon": "cloud",
        "description": "使用 OpenAI 或其他兼容的 Embedding API",
        "need_api_key": True,
    },
}


# 默认配置
DEFAULT_CONFIG = {
    "llm": {
        "provider": "lm_studio",
        "lm_studio": {
            "base_url": "http://localhost:1234/v1",
            "model": "",
        },
        "ollama": {
            "base_url": "http://localhost:11434/v1",
            "model": "",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
        },
        "azure": {
            "base_url": "https://YOUR_RESOURCE.openai.azure.com",
            "api_key": "",
            "model": "",
            "api_version": "2024-02-01",
        },
        "gemini": {
            "base_url": "https://generativelanguage.googleapis.com",
            "api_key": "",
            "model": "gemini-2.0-flash",
        },
        "kimi": {
            "base_url": "https://api.moonshot.cn/v1",
            "api_key": "",
            "model": "moonshot-v1-8k",
        },
        "kimi_coding": {
            "base_url": "https://api.kimi.com/coding",
            "api_key": "",
            "model": "k2p5",
            "headers": {
                "User-Agent": "Kimi Claw Plugin",
                "X-Kimi-Claw-ID": ""
            }
        },
        "anthropic": {
            "base_url": "https://api.anthropic.com/v1",
            "api_key": "",
            "model": "claude-3-haiku-20240307",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "deepseek-chat",
        },
        "siliconflow": {
            "base_url": "https://api.siliconflow.cn/v1",
            "api_key": "",
            "model": "Qwen/Qwen2.5-7B-Instruct",
        },
        "custom": {
            "base_url": "https://your-api.com/v1",
            "api_key": "",
            "model": "",
        },
    },
    "embedding": {
        "provider": "default",
        "default": {
            "model_name": "laion/larger_clap_general",
            "dimension": 512,
            "description": "CLAP 音频-文本嵌入模型（默认）",
        },
        "local": {
            "type": "lm_studio",
            "base_url": "http://localhost:1234/v1",
            "model": "",
        },
        "external": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "text-embedding-3-small",
            "dimension": 1536,
        },
    },
}


@dataclass
class LLMConfig:
    """LLM 配置数据类"""
    provider: str
    base_url: str
    model: str
    api_key: str = ""
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EmbeddingConfig:
    """Embedding 配置数据类"""
    provider: str
    model_name: str
    dimension: int
    base_url: str = ""
    api_key: str = ""
    local_type: str = ""  # lm_studio 或 ollama
    
    def to_dict(self) -> dict:
        return asdict(self)


# ==================== 配置管理器 ====================

class LLMConfigManager:
    """LLM 和 Embedding 配置管理器（单例）"""
    
    _instance: Optional['LLMConfigManager'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        self._config_dir = config.get_user_data_dir()
        self._config_path = self._config_dir / "ai_config.json"
        self._default_config_path = Path(__file__).resolve().parent.parent.parent / "config" / "ai_config.json"
        
        # 确保配置目录存在
        self._config_dir.mkdir(parents=True, exist_ok=True)
        
        # 加载配置
        self._config = self._load_config()
        
        logger.info(f"LLMConfigManager 初始化完成，配置文件: {self._config_path}")
    
    def _load_config(self) -> dict:
        """加载配置文件"""
        if self._config_path.exists():
            try:
                with open(self._config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    # 合并默认配置，确保所有字段都存在
                    merged = self._normalize_provider_selection(
                        self._merge_config(DEFAULT_CONFIG, loaded)
                    )
                    if _contains_plaintext_secret(loaded):
                        logger.warning(
                            "检测到旧版明文 AI 密钥；已仅加载到当前进程内存并从 JSON 配置移除"
                        )
                    # Always rewrite through the redacting serializer.  This
                    # also removes empty legacy api_key placeholders.
                    self._save_config(merged)
                    return merged
            except Exception as e:
                logger.warning(f"加载配置文件失败: {e}，使用默认配置")

        if self._default_config_path.exists():
            try:
                with open(self._default_config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                merged = self._normalize_provider_selection(
                    self._merge_config(DEFAULT_CONFIG, loaded)
                )
                self._save_config(merged)
                return merged
            except Exception as e:
                logger.warning(f"加载默认 AI 配置失败: {e}，使用内置默认配置")
        
        # 创建默认配置文件
        defaults = self._normalize_provider_selection(copy.deepcopy(DEFAULT_CONFIG))
        self._save_config(defaults)
        return defaults

    def _normalize_provider_selection(self, value: dict) -> dict:
        """Keep legacy config blocks but never select an unverified adapter."""
        result = copy.deepcopy(value)
        llm = result.setdefault("llm", {})
        selected = llm.get("provider", LLMProvider.LM_STUDIO)
        if selected not in LLMProvider.ALL:
            llm["legacy_provider"] = selected
            llm["provider"] = LLMProvider.LM_STUDIO
            logger.warning(
                "LLM 提供者 %s 在 v0.2 中未启用，已回退到 LM Studio；原配置仍保留",
                selected,
            )
        # Older defaults accidentally selected an embedding-only model for
        # SiliconFlow chat. Preserve custom choices but repair that known bad
        # default during config migration.
        siliconflow = llm.get(LLMProvider.SILICONFLOW, {})
        if siliconflow.get("model") == "BAAI/bge-m3":
            siliconflow["model"] = LLM_PROVIDER_META[LLMProvider.SILICONFLOW][
                "default_model"
            ]
        return result
    
    def _merge_config(self, default: dict, loaded: dict) -> dict:
        """深度合并配置"""
        result = copy.deepcopy(default)
        for key, value in loaded.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_config(result[key], value)
            else:
                result[key] = value
        return result
    
    def _save_config(self, config_data: dict):
        """Atomically persist non-sensitive metadata only."""
        temporary_path = self._config_path.with_name(
            f".{self._config_path.name}.tmp-{os.getpid()}"
        )
        try:
            disk_config = _config_for_disk(config_data)
            self._config_dir.mkdir(parents=True, exist_ok=True)
            with open(temporary_path, 'w', encoding='utf-8') as f:
                json.dump(disk_config, f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(temporary_path, 0o600)
            except OSError:
                pass
            os.replace(temporary_path, self._config_path)
            logger.info("AI 配置元数据已保存（密钥未写入磁盘）")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    
    # ==================== 配置访问方法 ====================
    
    def get_llm_config(self) -> dict:
        """获取完整的 LLM 配置"""
        return copy.deepcopy(self._config.get("llm", DEFAULT_CONFIG["llm"]))
    
    def get_embedding_config(self) -> dict:
        """获取完整的 Embedding 配置"""
        return copy.deepcopy(self._config.get("embedding", DEFAULT_CONFIG["embedding"]))

    def get_public_llm_config(self) -> dict:
        """Return settings safe for renderer/API responses."""
        public = self.get_llm_config()
        for provider in set(LLMProvider.ALL + LLMProvider.LEGACY):
            provider_config = public.get(provider)
            if not isinstance(provider_config, dict):
                continue
            has_key = bool(provider_config.pop("api_key", ""))
            provider_config["has_api_key"] = has_key
            # Custom headers can carry credentials too.
            if isinstance(provider_config.get("headers"), dict):
                provider_config["headers"] = _config_for_disk(
                    provider_config["headers"], inside_headers=True
                )
        return public

    def get_public_embedding_config(self) -> dict:
        public = self.get_embedding_config()
        for provider in (EmbeddingProvider.LOCAL, EmbeddingProvider.EXTERNAL):
            provider_config = public.get(provider)
            if isinstance(provider_config, dict):
                provider_config["has_api_key"] = bool(provider_config.pop("api_key", ""))
        return public

    @staticmethod
    def _merge_provider_config(current: dict, update: Optional[dict]) -> dict:
        """Merge metadata while preserving an omitted runtime secret.

        `api_key` omitted means keep, while an explicit empty string means
        clear.  Renderer-only status fields are never retained.
        """
        merged = copy.deepcopy(current) if isinstance(current, dict) else {}
        supplied = copy.deepcopy(update) if isinstance(update, dict) else {}
        supplied.pop("has_api_key", None)
        supplied.pop("secret_action", None)
        if "api_key" in supplied and supplied["api_key"] is None:
            supplied.pop("api_key")
        merged.update(supplied)
        return merged

    def resolve_llm_provider_config(
        self, provider: str, provider_config: Optional[dict]
    ) -> dict:
        current = self._config.get("llm", {}).get(provider, {})
        return self._merge_provider_config(current, provider_config)

    def resolve_embedding_provider_config(
        self, provider: str, provider_config: Optional[dict]
    ) -> dict:
        current = self._config.get("embedding", {}).get(provider, {})
        return self._merge_provider_config(current, provider_config)
    
    def get_llm_provider(self) -> str:
        """获取当前 LLM 提供者"""
        return self._config.get("llm", {}).get("provider", "lm_studio")
    
    def get_embedding_provider(self) -> str:
        """获取当前 Embedding 提供者"""
        return self._config.get("embedding", {}).get("provider", "default")
    
    def get_current_llm_config(self) -> LLMConfig:
        """获取当前 LLM 配置（解析后的）"""
        llm_config = self.get_llm_config()
        provider = llm_config.get("provider", "lm_studio")

        # 获取对应 provider 的配置，使用默认值
        meta = LLM_PROVIDER_META.get(provider, LLM_PROVIDER_META["custom"])
        provider_cfg = llm_config.get(provider, {})
        default_model = meta.get("default_model", "")

        if provider in (LLMProvider.LM_STUDIO, LLMProvider.OLLAMA):
            return LLMConfig(
                provider=provider,
                base_url=provider_cfg.get("base_url", meta.get("default_url", "")),
                model=provider_cfg.get("model", default_model),
                api_key=""
            )
        else:
            return LLMConfig(
                provider=provider,
                base_url=provider_cfg.get("base_url", meta.get("default_url", "")),
                model=provider_cfg.get("model", default_model),
                api_key=provider_cfg.get("api_key", "")
            )
    
    def get_current_embedding_config(self) -> EmbeddingConfig:
        """获取当前 Embedding 配置（解析后的）"""
        emb_config = self.get_embedding_config()
        provider = emb_config.get("provider", "default")
        
        if provider == EmbeddingProvider.DEFAULT:
            cfg = emb_config.get("default", {})
            return EmbeddingConfig(
                provider=provider,
                model_name=cfg.get("model_name", "laion/larger_clap_general"),
                dimension=cfg.get("dimension", 512)
            )
        elif provider == EmbeddingProvider.LOCAL:
            cfg = emb_config.get("local", {})
            return EmbeddingConfig(
                provider=provider,
                model_name=cfg.get("model", ""),
                dimension=1536,  # 默认值
                base_url=cfg.get("base_url", "http://localhost:1234/v1"),
                api_key=cfg.get("api_key", ""),
                local_type=cfg.get("type", "lm_studio")
            )
        else:  # external
            cfg = emb_config.get("external", {})
            return EmbeddingConfig(
                provider=provider,
                model_name=cfg.get("model", "text-embedding-3-small"),
                dimension=cfg.get("dimension", 1536),
                base_url=cfg.get("base_url", "https://api.openai.com/v1"),
                api_key=cfg.get("api_key", "")
            )
    
    # ==================== 配置更新方法 ====================
    
    def update_llm_config(self, provider: str, provider_config: dict):
        """更新 LLM 配置"""
        if provider not in LLMProvider.ALL:
            raise ValueError(f"当前版本未启用 LLM 提供者: {provider}")
        previous = copy.deepcopy(self._config)
        try:
            self._config["llm"]["provider"] = provider
            if provider not in self._config["llm"]:
                self._config["llm"][provider] = {}
            self._config["llm"][provider] = self.resolve_llm_provider_config(
                provider, provider_config
            )
            self._save_config(self._config)
        except Exception:
            self._config = previous
            raise
    
    def update_embedding_config(self, provider: str, provider_config: dict):
        """更新 Embedding 配置"""
        if provider not in EmbeddingProvider.ALL:
            raise ValueError(f"不支持的 Embedding 提供者: {provider}")
        previous = copy.deepcopy(self._config)
        try:
            self._config["embedding"]["provider"] = provider
            if provider != EmbeddingProvider.DEFAULT:
                self._config["embedding"][provider] = self.resolve_embedding_provider_config(
                    provider, provider_config
                )
            self._save_config(self._config)
        except Exception:
            self._config = previous
            raise
    
    def save_full_config(self, llm_provider: str, llm_config: dict, 
                          embedding_provider: str, embedding_config: dict):
        """保存完整配置"""
        if llm_provider not in LLMProvider.ALL:
            raise ValueError(f"当前版本未启用 LLM 提供者: {llm_provider}")
        if embedding_provider not in EmbeddingProvider.ALL:
            raise ValueError(f"不支持的 Embedding 提供者: {embedding_provider}")
        previous = copy.deepcopy(self._config)
        try:
            self._config["llm"]["provider"] = llm_provider
            self._config["llm"][llm_provider] = self.resolve_llm_provider_config(
                llm_provider, llm_config
            )
            self._config["embedding"]["provider"] = embedding_provider
            self._config["embedding"][embedding_provider] = self.resolve_embedding_provider_config(
                embedding_provider, embedding_config
            )
            self._save_config(self._config)
        except Exception:
            self._config = previous
            raise
    
    # ==================== 连接测试 ====================
    
    async def test_llm_connection(self, provider: str = None, 
                                   provider_config: dict = None) -> dict:
        """
        测试 LLM 连接
        
        Args:
            provider: 提供者类型（可选，使用当前配置）
            provider_config: 提供者配置（可选，使用当前配置）
            
        Returns:
            {"success": bool, "message": str, "models": List[str]}
        """
        provider = provider or self.get_llm_provider()
        if provider not in LLMProvider.ALL:
            return {
                "success": False,
                "message": f"当前版本未启用提供者: {provider}",
                "models": [],
                "code": "unsupported_provider",
                "retryable": False,
            }
        provider_config = self.resolve_llm_provider_config(provider, provider_config)

        base_url = str(provider_config.get("base_url", "")).rstrip("/")
        if not base_url:
            return {"success": False, "message": "API 地址不能为空", "models": []}
        allow_local = provider in {
            LLMProvider.LM_STUDIO,
            LLMProvider.OLLAMA,
            LLMProvider.CUSTOM,
        }
        if not validate_url(base_url, allow_local=allow_local):
            return {"success": False, "message": "API 地址不安全", "models": []}

        headers = {"Accept": "application/json"}
        api_key = str(provider_config.get("api_key", ""))
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                response = await client.get(f"{base_url}/models", headers=headers)
            if response.status_code != 200:
                retryable = response.status_code == 429 or response.status_code >= 500
                return {
                    "success": False,
                    "message": f"连接失败: HTTP {response.status_code}",
                    "models": [],
                    "code": "provider_rejected",
                    "retryable": retryable,
                }
            data = response.json()
            if isinstance(data, dict) and "data" in data:
                models = [str(item.get("id", "")) for item in data.get("data", [])]
            elif isinstance(data, dict) and "models" in data:
                models = [str(item.get("name", "")) for item in data.get("models", [])]
            elif isinstance(data, list):
                models = [str(item.get("id") or item.get("name") or item) for item in data]
            else:
                models = []
            return {
                "success": True,
                "message": f"连接成功，找到 {len(models)} 个模型",
                "models": models,
            }
        except httpx.TimeoutException:
            return {
                "success": False,
                "message": "连接超时",
                "models": [],
                "code": "timeout",
                "retryable": True,
            }
        except httpx.RequestError:
            return {
                "success": False,
                "message": f"无法连接到 {base_url}",
                "models": [],
                "code": "connection_failed",
                "retryable": True,
            }
        except Exception as exc:
            return {
                "success": False,
                "message": f"测试失败: {exc}",
                "models": [],
                "code": "invalid_response",
                "retryable": False,
            }
    
    async def test_embedding_connection(self, provider: str = None,
                                         provider_config: dict = None) -> dict:
        """
        测试 Embedding 连接
        
        Args:
            provider: 提供者类型（可选，使用当前配置）
            provider_config: 提供者配置（可选，使用当前配置）
            
        Returns:
            {"success": bool, "message": str}
        """
        provider = provider or self.get_embedding_provider()
        
        # CLAP is local, but it is not "always available": packaged installs
        # are allowed to start before the optional model bundle is installed.
        if provider == EmbeddingProvider.DEFAULT:
            from core.embedder import is_embedder_loaded

            model_path = Path(config.get_clap_model_name())
            available = model_path.exists() or is_embedder_loaded()
            return {
                "success": available,
                "message": "默认 CLAP 模型已就绪" if available else "CLAP 模型尚未安装或加载",
                "code": None if available else "model_unavailable",
                "retryable": not available,
            }
        
        provider_config = self.resolve_embedding_provider_config(provider, provider_config)
        
        base_url = provider_config.get("base_url", "")
        api_key = provider_config.get("api_key", "")
        
        if not base_url:
            return {"success": False, "message": "API 地址不能为空"}

        allow_local = provider == EmbeddingProvider.LOCAL
        if not validate_url(base_url, allow_local=allow_local):
            return {"success": False, "message": "API 地址不安全，请使用有效的 HTTP/HTTPS 地址"}
        
        try:
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            
            # 测试 embedding 请求
            payload = {
                "input": "test",
                "model": provider_config.get("model", "")
            }
            
            embeddings_url = base_url.rstrip("/") + "/embeddings"
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.post(embeddings_url, headers=headers, json=payload)
            
            if response.status_code == 200:
                return {
                    "success": True,
                    "message": "Embedding 服务连接成功"
                }
            else:
                return {
                    "success": False,
                    "message": f"连接失败: HTTP {response.status_code}"
                }
                
        except httpx.TimeoutException:
            return {"success": False, "message": "连接超时", "code": "timeout", "retryable": True}
        except httpx.RequestError:
            return {
                "success": False,
                "message": f"无法连接到 {base_url}",
                "code": "connection_failed",
                "retryable": True,
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"测试失败: {str(e)}"
            }
    
    # ==================== 服务检测 ====================
    
    def detect_available_local_services(self) -> dict:
        """检测本地可用的服务"""
        services = {
            "lm_studio": False,
            "ollama": False,
            "lm_studio_url": "",
            "ollama_url": ""
        }
        
        # 检测 LM Studio
        if self._check_port("localhost", 1234):
            services["lm_studio"] = True
            services["lm_studio_url"] = "http://localhost:1234/v1"
        
        # 检测 Ollama
        if self._check_port("localhost", 11434):
            services["ollama"] = True
            services["ollama_url"] = "http://localhost:11434/v1"
        
        return services
    
    def _check_port(self, host: str, port: int) -> bool:
        """检查端口是否开放"""
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        try:
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except:
            return False
    
    # ==================== 配置导出/导入 ====================
    
    def export_config(self) -> dict:
        """导出当前配置（不含敏感信息）"""
        return _config_for_disk(self._config)
    
    def reset_to_defaults(self):
        """重置为默认配置"""
        self._config = copy.deepcopy(DEFAULT_CONFIG)
        self._save_config(self._config)
        logger.info("配置已重置为默认")


# ==================== 全局单例 ====================

_config_manager: Optional[LLMConfigManager] = None


def get_llm_config_manager() -> LLMConfigManager:
    """获取 LLM 配置管理器单例"""
    global _config_manager
    if _config_manager is None:
        _config_manager = LLMConfigManager()
    return _config_manager


def reset_llm_config_manager():
    """重置配置管理器（用于测试或重新加载）"""
    global _config_manager
    _config_manager = None
    LLMConfigManager._instance = None
