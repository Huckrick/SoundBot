"""
SoundMind 后端配置管理

集中管理所有配置：模型路径、API 密钥、参数等。
"""

import os
from pathlib import Path

# ==================== 项目基础配置 ====================

# 项目根目录
BASE_DIR = Path(__file__).parent

# 应用信息
APP_NAME = "SoundMind"
APP_VERSION = "0.1.0"
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# ==================== 服务器配置 ====================

HOST = "127.0.0.1"  # 只监听本地，安全
PORT = 8000
API_PREFIX = "/api/v1"

# ==================== CORS 配置 ====================

CORS_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "electron://*"
]

# ==================== 模型配置 ====================

# CLAP 音频嵌入模型
CLAP_MODEL_NAME = "microsoft/msclap"
CLAP_DEVICE = "auto"  # auto/cpu/cuda/mps

# ==================== 向量数据库配置 ====================

# ChromaDB 存储路径
CHROMA_DB_PATH = BASE_DIR / "db" / "chroma_store"

# ==================== 音频扫描配置 ====================

SUPPORTED_FORMATS = ['.wav', '.mp3', '.flac', '.aiff', '.ogg', '.m4a', '.aac']
MAX_AUDIO_DURATION = 300  # 最大处理 5 分钟音频

# ==================== 临时文件配置 ====================

TEMP_CLIP_DIR = "/tmp/soundmind"  # 裁切临时文件存放目录

# ==================== 搜索配置 ====================

TOP_K_RESULTS = 20  # 默认返回 20 个结果
SIMILARITY_THRESHOLD = 0.15  # 相似度阈值

# ==================== 大语言模型配置（AI 助手功能） ====================

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")  # 可选: openai/anthropic/local
LLM_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")  # 自定义 API 地址

# 本地模型配置（如果使用 llama.cpp/ollama）
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://localhost:11434/api/generate")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen2.5:7b")

# ==================== 工具函数 ====================


def get_db_path() -> Path:
    """获取数据库存储路径"""
    db_path = BASE_DIR / CHROMA_DB_PATH
    db_path.mkdir(parents=True, exist_ok=True)
    return db_path


def get_device() -> str:
    """自动检测可用的计算设备"""
    import torch
    
    if torch.cuda.is_available():
        return "cuda"
    elif torch.backends.mps.is_available():
        return "mps"
    else:
        return "cpu"


def get_clap_device() -> str:
    """获取 CLAP 模型使用的设备"""
    if CLAP_DEVICE == "auto":
        return get_device()
    return CLAP_DEVICE


def is_safe_path(file_path: str) -> bool:
    """
    检查文件路径是否存在路径遍历攻击

    Args:
        file_path: 要检查的文件路径

    Returns:
        路径是否安全
    """
    try:
        path = Path(file_path).resolve()
        return path.exists()
    except (OSError, RuntimeError):
        return False


def validate_audio_path(file_path: str) -> Path:
    """
    验证音频文件路径是否安全

    Args:
        file_path: 音频文件路径

    Returns:
        验证后的 Path 对象

    Raises:
        HTTPException: 路径无效或不存在
    """
    from fastapi import HTTPException

    if not file_path:
        raise HTTPException(status_code=400, detail="文件路径不能为空")

    try:
        path = Path(file_path).resolve()

        if not path.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")

        if not path.is_file():
            raise HTTPException(status_code=400, detail=f"不是有效文件: {file_path}")

        if path.suffix.lower() not in SUPPORTED_FORMATS:
            raise HTTPException(status_code=400, detail=f"不支持的文件格式: {path.suffix}")

        return path

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无效的路径: {e}")
