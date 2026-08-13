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
import json
from pathlib import Path

# ==================== 动态路径解析 ====================

def get_executable_dir() -> Path:
    """
    获取可执行文件所在目录（resolve() 确保符号链接被正确解析）
    - 开发环境: backend/ 目录
    - PyInstaller: 解压后的临时目录或单文件目录
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    else:
        return Path(__file__).resolve().parent


def get_user_data_dir() -> Path:
    """
    获取用户数据目录（跨平台）
    - macOS: ~/Library/Application Support/SoundBot
    - Windows: %APPDATA%/SoundBot
    - Linux: ~/.local/share/SoundBot
    """
    override = os.environ.get('SOUNDBOT_USER_DATA_DIR')
    if override:
        data_dir = Path(override).expanduser()
    elif sys.platform == 'darwin':
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
    """获取受工程根目录约束的 ChromaDB 存储路径。"""
    import re
    value = str(project_id or "").strip()
    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    stem = value.split(".", 1)[0].upper()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)
        or value.endswith(".")
        or stem in reserved
    ):
        raise ValueError("工程 ID 包含非法字符")
    root = (get_user_data_dir() / 'chroma_projects').resolve(strict=False)
    db_path = (root / value).resolve(strict=False)
    db_path.relative_to(root)
    db_path.mkdir(parents=True, exist_ok=True)
    return db_path


# ==================== 项目基础配置 ====================

APP_NAME = "SoundBot"
APP_VERSION = "0.2.0"
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
        本地模型目录路径；目录缺失时仍返回期望位置供调用方快速报错
    """
    # 每次都重新查找模型目录（绕过任何可能的缓存）
    models_dir = find_models_dir_runtime()
    clap_path = models_dir / 'clap'
    
    return str(clap_path)


# 自动查找模型目录（使用运行时版本确保每次都读取最新环境变量）
MODELS_DIR = str(find_models_dir_runtime())
CLAP_MODEL_PATH = str(Path(MODELS_DIR) / 'clap')

# 仅允许本地、经 manifest 校验安装的 CLAP 模型。模型缺失时应用仍可
# 启动元数据功能，但绝不在请求路径隐式访问 Hugging Face。
CLAP_MODEL_NAME = str(CLAP_MODEL_PATH)

# 注意：在 PyInstaller 打包后的环境中，CLAP_MODEL_NAME 可能是错误的绝对路径
# 应该使用 get_clap_model_name() 函数来获取正确的模型路径

CLAP_DEVICE = os.getenv("CLAP_DEVICE", "auto")
MODEL_LOAD_TIMEOUT = int(os.getenv("MODEL_LOAD_TIMEOUT", "120"))
ENABLE_MODEL_PRELOAD = os.getenv("ENABLE_MODEL_PRELOAD", "true").lower() == "true"

# ==================== 音频能力配置 ====================

def _load_audio_capabilities() -> dict:
    """Load the one format manifest shared by Python and Electron."""
    candidates = [
        Path(__file__).resolve().parent.parent / 'config' / 'audio_capabilities.json',
        Path(getattr(sys, '_MEIPASS', get_executable_dir()))
        / 'config'
        / 'audio_capabilities.json',
    ]
    for manifest_path in candidates:
        if not manifest_path.is_file():
            continue
        payload = json.loads(manifest_path.read_text(encoding='utf-8'))
        formats = payload.get('formats') if isinstance(payload, dict) else None
        if not isinstance(formats, dict) or not formats:
            raise RuntimeError(f'音频能力表无效: {manifest_path}')
        for extension, capability in formats.items():
            if (
                not isinstance(extension, str)
                or not extension.startswith('.')
                or not isinstance(capability, dict)
                or not isinstance(capability.get('mime_type'), str)
                or not isinstance(capability.get('requires_playback_transcode'), bool)
            ):
                raise RuntimeError(f'音频能力条目无效: {extension!r}')
        return formats
    raise RuntimeError('缺少 config/audio_capabilities.json')


AUDIO_FORMAT_CAPABILITIES = _load_audio_capabilities()

SUPPORTED_FORMATS = tuple(AUDIO_FORMAT_CAPABILITIES)
WAVEFORM_PEAK_COUNT = 2000
WAVEFORM_VERSION = '2'
PLAYBACK_WAV_CACHE_MAX_BYTES = int(
    os.getenv('PLAYBACK_WAV_CACHE_MAX_BYTES', str(512 * 1024 * 1024))
)
PLAYBACK_WAV_CACHE_MAX_FILES = int(os.getenv('PLAYBACK_WAV_CACHE_MAX_FILES', '128'))
MAX_AUDIO_DURATION = 300  # 最大处理 5 分钟音频

# ==================== 临时文件配置 ====================

DEFAULT_TEMP_CLIP_DIR = str(get_temp_dir())
PROJECT_TEMP_CLIP_DIR = None

# 获取临时文件目录（支持用户自定义）
def get_temp_clip_dir() -> str:
    """
    获取临时文件存放目录
    优先使用当前工程目录，其次读取用户配置，否则使用默认路径
    """
    import json

    if PROJECT_TEMP_CLIP_DIR and Path(PROJECT_TEMP_CLIP_DIR).exists():
        return str(PROJECT_TEMP_CLIP_DIR)
    
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


def set_project_temp_clip_dir(temp_dir: str = None) -> str:
    """设置当前工程的临时文件目录覆盖值。"""
    global PROJECT_TEMP_CLIP_DIR, TEMP_CLIP_DIR

    if temp_dir and Path(temp_dir).exists():
        PROJECT_TEMP_CLIP_DIR = str(Path(temp_dir))
    else:
        PROJECT_TEMP_CLIP_DIR = None

    TEMP_CLIP_DIR = get_temp_clip_dir()
    return TEMP_CLIP_DIR

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
