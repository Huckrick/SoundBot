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
    "electron://*",
    "file://*",
    "*"  # 允许所有 origin 用于本地开发
]

# ==================== 模型配置 ====================

# HuggingFace 镜像配置（国内加速）
HF_ENDPOINT = "https://hf-mirror.com"

# CLAP 模型选择（可配置）
# 可选值:
#   - "laion/larger_clap_general": 大型模型，精度高，加载慢 (~25s)，推荐用于生产环境
#   - "laion/clap-htsat-unfused": 中型模型，平衡精度和速度 (~10s)，推荐用于开发环境
#   - "laion/clap-htsat-fused": 轻量级模型，速度快 (~5s)，精度略低，推荐用于测试环境
#
# 模型引用 / Model Citation:
#   Wu, Y., Chen, K., Zhang, T., Hui, Y., Berg-Kirkpatrick, T., & Dubnov, S. (2022).
#   Large-scale Contrastive Language-Audio Pretraining with Feature Fusion and Keyword-to-Caption Augmentation.
#   arXiv preprint arXiv:2211.06687.
#   https://huggingface.co/laion/larger_clap_general
#
CLAP_MODEL_NAME = os.getenv("CLAP_MODEL", "laion/larger_clap_general")
CLAP_DEVICE = "auto"  # auto/cpu/cuda/mps

# 模型加载超时（秒）
MODEL_LOAD_TIMEOUT = int(os.getenv("MODEL_LOAD_TIMEOUT", "60"))

# 是否启用模型预加载（启动时后台加载）
ENABLE_MODEL_PRELOAD = os.getenv("ENABLE_MODEL_PRELOAD", "true").lower() == "true"

# ==================== 向量数据库配置 ====================

# ChromaDB 基础存储路径
CHROMA_DB_BASE_PATH = BASE_DIR / "db" / "chroma_projects"

# 获取当前工程的 ChromaDB 路径
def get_chroma_db_path(project_id: str = None) -> Path:
    """
    获取指定工程的 ChromaDB 存储路径

    Args:
        project_id: 工程ID，默认为当前激活的工程

    Returns:
        ChromaDB 路径
    """
    if project_id is None:
        project_id = CURRENT_PROJECT_ID

    db_path = CHROMA_DB_BASE_PATH / project_id
    db_path.mkdir(parents=True, exist_ok=True)
    return db_path

# 向后兼容的变量（使用默认工程路径）
CHROMA_DB_PATH = get_chroma_db_path("default")

# ==================== 音频扫描配置 ====================

SUPPORTED_FORMATS = ['.wav', '.mp3', '.flac', '.aiff', '.ogg', '.m4a', '.aac']
MAX_AUDIO_DURATION = 300  # 最大处理 5 分钟音频

# ==================== 临时文件配置 ====================

# 默认临时文件目录（应用目录下的 temp_clips 文件夹）
def get_default_temp_clip_dir() -> str:
    """获取默认临时文件目录（应用目录下）"""
    import os
    default_dir = os.path.join(BASE_DIR, '..', 'temp_clips')
    # 确保目录存在
    os.makedirs(default_dir, exist_ok=True)
    return default_dir

DEFAULT_TEMP_CLIP_DIR = get_default_temp_clip_dir()

# 获取临时文件目录（支持用户自定义）
def get_temp_clip_dir() -> str:
    """
    获取临时文件存放目录
    优先从配置文件读取，否则使用应用目录下的默认路径
    """
    import json
    import os
    
    # 尝试从配置文件读取
    config_path = os.path.join(BASE_DIR, '..', 'config', 'user_config.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                temp_dir = config.get('tempClipDir')
                if temp_dir and os.path.exists(temp_dir):
                    return temp_dir
        except Exception:
            pass
    
    # 回退到默认路径（应用目录下）
    return get_default_temp_clip_dir()

# 向后兼容的变量（实际使用 get_temp_clip_dir() 函数）
TEMP_CLIP_DIR = get_temp_clip_dir()

# ==================== 工程管理配置 ====================

# 当前激活的工程ID（默认工程）
CURRENT_PROJECT_ID = "default"

# ==================== 搜索配置 ====================

TOP_K_RESULTS = 1000  # 默认返回 1000 个结果（几乎无限制）
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
