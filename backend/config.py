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
SoundBot 后端配置管理 - PyInstaller 适配版

支持动态路径解析，确保打包后可执行文件在任何机器上都能正常运行。
"""

import os
import sys
from pathlib import Path

# ==================== 动态路径解析 ====================

def get_executable_dir() -> Path:
    """
    获取可执行文件所在目录
    - 开发环境: backend/ 目录
    - PyInstaller: 解压后的临时目录或单文件目录
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的可执行文件
        return Path(sys.executable).parent
    else:
        # 开发环境
        return Path(__file__).parent


def get_user_data_dir() -> Path:
    """
    获取用户数据目录（跨平台）
    - macOS: ~/Library/Application Support/SoundBot
    - Windows: %APPDATA%/SoundBot
    - Linux: ~/.local/share/SoundBot
    """
    if sys.platform == 'darwin':
        data_dir = Path.home() / 'Library' / 'Application Support' / 'SoundBot'
    elif sys.platform == 'win32':
        appdata = os.environ.get('APPDATA') or os.environ.get('LOCALAPPDATA')
        if appdata:
            data_dir = Path(appdata) / 'SoundBot'
        else:
            data_dir = Path.home() / 'AppData' / 'Roaming' / 'SoundBot'
    else:
        data_dir = Path.home() / '.local' / 'share' / 'SoundBot'
    
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def find_models_dir() -> Path:
    """
    自动检索模型目录（多路径优先级）
    
    检索顺序:
    1. 环境变量 SOUNDBOT_MODELS_PATH
    2. 可执行文件同级目录的 models/
    3. 可执行文件上级目录的 models/
    4. 用户数据目录的 models/
    5. 开发环境项目根目录的 models/
    
    Returns:
        模型目录路径（无论是否存在）
    """
    exe_dir = get_executable_dir()
    user_data = get_user_data_dir()
    
    # 所有可能的路径（按优先级）
    possible_paths = []
    
    # 1. 环境变量（最高优先级）
    env_path = os.getenv('SOUNDBOT_MODELS_PATH')
    if env_path:
        possible_paths.append(Path(env_path))
    
    # 2. 可执行文件同级目录
    possible_paths.append(exe_dir / 'models')
    
    # 3. 可执行文件上级目录（Electron 资源目录结构）
    possible_paths.append(exe_dir.parent / 'models')
    possible_paths.append(exe_dir.parent.parent / 'models')
    
    # 4. 用户数据目录
    possible_paths.append(user_data / 'models')
    
    # 5. 开发环境
    if not getattr(sys, 'frozen', False):
        dev_root = Path(__file__).parent.parent
        possible_paths.append(dev_root / 'models')
    
    # 查找第一个包含 clap 子目录的路径
    for models_path in possible_paths:
        clap_dir = models_path / 'clap'
        if clap_dir.exists() and clap_dir.is_dir():
            return models_path
    
    # 如果没有找到，返回第一个路径（用于错误提示）
    return possible_paths[0] if possible_paths else exe_dir / 'models'


def get_db_path() -> Path:
    """获取数据库存储路径（用户数据目录）"""
    db_path = get_user_data_dir() / 'db'
    db_path.mkdir(parents=True, exist_ok=True)
    return db_path


def get_temp_dir() -> Path:
    """获取临时文件目录"""
    temp_dir = get_user_data_dir() / 'temp'
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def get_chroma_db_path(project_id: str = "default") -> Path:
    """获取 ChromaDB 存储路径"""
    db_path = get_user_data_dir() / 'chroma_projects' / project_id
    db_path.mkdir(parents=True, exist_ok=True)
    return db_path


# ==================== 项目基础配置 ====================

APP_NAME = "SoundBot"
APP_VERSION = "0.1.2"
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# ==================== 服务器配置 ====================

HOST = "127.0.0.1"
PORT = int(os.getenv("SOUNDBOT_PORT", "8000"))
API_PREFIX = "/api/v1"

# ==================== CORS 配置 ====================

CORS_ORIGINS = [
    "http://127.0.0.1:*",
    "http://localhost:*",
    "electron://*",
    "file://*",
    "null",
]

# ==================== 模型配置 ====================

HF_ENDPOINT = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")


def find_models_dir_runtime() -> Path:
    """
    运行时动态查找模型目录（每次都重新评估环境变量）
    
    与 find_models_dir() 的区别：
    - find_models_dir() 在模块导入时执行一次，路径固定
    - find_models_dir_runtime() 每次调用都重新检查环境变量
    
    检索顺序:
    1. 环境变量 SOUNDBOT_MODELS_PATH（最高优先级）
    2. 可执行文件同级目录的 models/
    3. 可执行文件上级目录的 models/
    4. 用户数据目录的 models/
    5. 开发环境项目根目录的 models/
    
    Returns:
        模型目录路径（无论是否存在）
    """
    exe_dir = get_executable_dir()
    user_data = get_user_data_dir()
    
    # 所有可能的路径（按优先级）
    possible_paths = []
    
    # 1. 环境变量（最高优先级）- 每次都重新读取
    env_path = os.getenv('SOUNDBOT_MODELS_PATH')
    if env_path:
        possible_paths.append(Path(env_path))
    
    # 2. 可执行文件同级目录
    possible_paths.append(exe_dir / 'models')
    
    # 3. 可执行文件上级目录（Electron 资源目录结构）
    possible_paths.append(exe_dir.parent / 'models')
    possible_paths.append(exe_dir.parent.parent / 'models')
    
    # 4. 用户数据目录
    possible_paths.append(user_data / 'models')
    
    # 5. 开发环境
    if not getattr(sys, 'frozen', False):
        dev_root = Path(__file__).parent.parent
        possible_paths.append(dev_root / 'models')
    
    # 查找第一个包含 clap 子目录的路径
    for models_path in possible_paths:
        clap_dir = models_path / 'clap'
        if clap_dir.exists() and clap_dir.is_dir():
            return models_path
    
    # 如果没有找到，返回第一个路径（用于错误提示）
    return possible_paths[0] if possible_paths else exe_dir / 'models'


def get_clap_model_name() -> str:
    """
    运行时动态获取 CLAP 模型路径
    
    此函数在调用时实时查找模型目录，支持 SOUNDBOT_MODELS_PATH 环境变量
    这是解决 PyInstaller 打包后路径问题的关键
    
    Returns:
        模型路径字符串（本地路径或 HuggingFace 模型名）
    """
    # 每次都重新查找模型目录（绕过任何可能的缓存）
    models_dir = find_models_dir_runtime()
    clap_path = models_dir / 'clap'
    
    if clap_path.exists():
        return str(clap_path)
    else:
        # 回退到 HuggingFace
        return os.getenv("CLAP_MODEL", "laion/larger_clap_general")


# 自动查找模型目录（模块导入时执行一次）
MODELS_DIR = find_models_dir()
CLAP_MODEL_PATH = MODELS_DIR / 'clap'

# 确定 CLAP 模型名称/路径（模块导入时执行一次，仅作为默认值）
if CLAP_MODEL_PATH.exists():
    CLAP_MODEL_NAME = str(CLAP_MODEL_PATH)
else:
    # 回退到 HuggingFace
    CLAP_MODEL_NAME = os.getenv("CLAP_MODEL", "laion/larger_clap_general")

# 注意：在 PyInstaller 打包后的环境中，CLAP_MODEL_NAME 可能是错误的绝对路径
# 应该使用 get_clap_model_name() 函数来获取正确的模型路径

CLAP_DEVICE = os.getenv("CLAP_DEVICE", "auto")
MODEL_LOAD_TIMEOUT = int(os.getenv("MODEL_LOAD_TIMEOUT", "120"))
ENABLE_MODEL_PRELOAD = os.getenv("ENABLE_MODEL_PRELOAD", "true").lower() == "true"

# ==================== 音频扫描配置 ====================

SUPPORTED_FORMATS = ['.wav', '.mp3', '.flac', '.aiff', '.ogg', '.m4a', '.aac', '.wma']
MAX_AUDIO_DURATION = 300  # 最大处理 5 分钟音频

# ==================== 临时文件配置 ====================

DEFAULT_TEMP_CLIP_DIR = str(get_temp_dir())

# 获取临时文件目录（支持用户自定义）
def get_temp_clip_dir() -> str:
    """
    获取临时文件存放目录
    优先从用户配置读取，否则使用默认路径
    """
    import json
    
    config_path = get_user_data_dir() / 'user_config.json'
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                user_config = json.load(f)
                temp_dir = user_config.get('tempClipDir')
                if temp_dir and Path(temp_dir).exists():
                    return temp_dir
        except Exception:
            pass
    
    return str(DEFAULT_TEMP_CLIP_DIR)

TEMP_CLIP_DIR = get_temp_clip_dir()

# ==================== 工程管理配置 ====================

CURRENT_PROJECT_ID = "default"

# ==================== 搜索配置 ====================

TOP_K_RESULTS = 1000
SIMILARITY_THRESHOLD = 0.15
KEYWORD_BOOST_FACTOR = 1.2
SEMANTIC_DECAY_FACTOR = 1.0
SEARCH_MODE = "hybrid"

# ==================== 大语言模型配置 ====================

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")
LLM_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://localhost:11434/api/generate")
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "qwen2.5:7b")

# ==================== 工具函数 ====================

def get_device() -> str:
    """自动检测可用的计算设备"""
    import torch
    
    if torch.cuda.is_available():
        return "cuda"
    elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
        return "mps"
    else:
        return "cpu"


def get_clap_device() -> str:
    """获取 CLAP 模型使用的设备"""
    if CLAP_DEVICE == "auto":
        return get_device()
    return CLAP_DEVICE


def is_safe_path(file_path: str) -> bool:
    """检查文件路径是否安全"""
    try:
        path = Path(file_path).resolve()
        return path.exists()
    except (OSError, RuntimeError):
        return False


def validate_audio_path(file_path: str, allowed_base: Path = None) -> Path:
    """验证音频文件路径是否安全"""
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

        if allowed_base is not None:
            allowed_base = allowed_base.resolve()
            try:
                path.relative_to(allowed_base)
            except ValueError:
                raise HTTPException(
                    status_code=403,
                    detail=f"路径 '{file_path}' 不在允许的目录 '{allowed_base}' 内"
                )

        return path

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无效的路径: {e}")


# 启动时打印路径信息（用于调试）
if __name__ == "__main__" or DEBUG:
    print(f"[Config] 可执行文件目录: {get_executable_dir()}")
    print(f"[Config] 用户数据目录: {get_user_data_dir()}")
    print(f"[Config] 模型目录: {MODELS_DIR}")
    print(f"[Config] 数据库目录: {get_db_path()}")
    print(f"[Config] 临时目录: {get_temp_dir()}")
