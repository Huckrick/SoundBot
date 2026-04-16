# -*- mode: python ; coding: utf-8 -*-
"""
SoundBot Backend PyInstaller Spec
打包为目录模式，支持 Windows/macOS/Linux

使用 collect_all() / collect_submodules() 自动收集依赖，
替代手动维护的 hiddenimports 列表，确保打包完整。
"""

import sys
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# ==================== 路径设置 ====================

spec_file = Path(os.path.abspath(sys.argv[0]))
spec_dir = spec_file.parent
backend_dir = spec_dir
project_root = backend_dir.parent

block_cipher = None

# ==================== 本地模块数据文件 ====================
datas = []

# 主入口和配置文件
for filename in ['main.py', 'config.py', 'bootstrap.py']:
    filepath = backend_dir / filename
    if filepath.exists():
        datas.append((str(filepath), '.'))

# 子包目录
for dirname in ['core', 'utils', 'models']:
    dirpath = backend_dir / dirname
    if dirpath.exists():
        datas.append((str(dirpath), dirname))

# ==================== 自动收集第三方包 ====================
binaries = []
hiddenimports = []

# 需要完整收集的包（数据文件 + 二进制扩展 + 子模块）
# 这些包含有 PyInstaller 无法自动发现的运行时数据文件
_collect_all_packages = [
    'chromadb',              # 数据库迁移 SQL、配置文件
    'jieba',                 # 中文分词词典
    'soundfile',             # libsndfile 原生库
    'sounddevice',           # PortAudio 原生库 (Windows: portaudio-x86_64.dll)
    'tokenizers',            # Rust 原生扩展
    'safetensors',           # Rust 原生扩展
    'sentence_transformers', # 模型配置和数据
    'soxr',                  # 音频重采样原生库
    'onnxruntime',           # ONNX 推理引擎
]

# 需要收集子模块 + 数据文件的包（体量大，分开收集更可控）
_collect_submodules_and_data_packages = [
    'transformers',          # CLAP 模型配置 JSON
    'torch',                 # 深度学习框架
    'librosa',               # 音频分析数据文件
    'sklearn',               # 机器学习子模块
    'numba',                 # JIT 编译器
    'llvmlite',              # LLVM 绑定
]

# 只需要收集子模块的包（纯 Python，PyInstaller 自动分析可能遗漏动态导入）
_collect_submodules_packages = [
    'uvicorn',
    'starlette',
    'fastapi',
    'pydantic',
    'pydantic_core',
    'httpx',
    'httpcore',
    'anyio',
    'numpy',
    'scipy',
    'huggingface_hub',
]

for pkg in _collect_all_packages:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
        print(f'[collect_all] {pkg}: {len(d)} datas, {len(b)} binaries, {len(h)} imports')
    except Exception as e:
        print(f'Warning: collect_all({pkg}) failed: {e}')

for pkg in _collect_submodules_and_data_packages:
    try:
        h = collect_submodules(pkg)
        hiddenimports += h
        print(f'[collect_submodules] {pkg}: {len(h)} imports')
    except Exception as e:
        print(f'Warning: collect_submodules({pkg}) failed: {e}')
    try:
        d = collect_data_files(pkg)
        datas += d
        print(f'[collect_data_files] {pkg}: {len(d)} datas')
    except Exception as e:
        print(f'Warning: collect_data_files({pkg}) failed: {e}')

for pkg in _collect_submodules_packages:
    try:
        h = collect_submodules(pkg)
        hiddenimports += h
        print(f'[collect_submodules] {pkg}: {len(h)} imports')
    except Exception as e:
        print(f'Warning: collect_submodules({pkg}) failed: {e}')

# ==================== 本地模块隐藏导入 ====================
hiddenimports += [
    # 入口和配置
    'main',
    'config',
    'bootstrap',

    # core 子模块
    'core',
    'core.database',
    'core.embedder',
    'core.indexer',
    'core.scanner',
    'core.searcher',
    'core.search_engine',
    'core.audio_cache',
    'core.playback_manager',
    'core.websocket_manager',
    'core.model_preloader',
    'core.llm_config_manager',
    'core.llm_client',
    'core.ai_chat_service',
    'core.ucs_keywords',

    # utils / models 子模块
    'utils',
    'utils.logger',
    'utils.audio_utils',
    'models',
    'models.schemas',
]

# ==================== 额外手动补充 ====================
hiddenimports += [
    # multiprocessing (Windows freeze_support 需要)
    'multiprocessing',
    'multiprocessing.context',
    'multiprocessing.pool',
    'multiprocessing.process',
    'multiprocessing.spawn',
    'multiprocessing.synchronize',
    'multiprocessing.reduction',

    # asyncio（uvicorn 事件循环）
    'asyncio',
    'asyncio.base_events',
    'asyncio.events',
    'asyncio.streams',
    'concurrent.futures',
    'concurrent.futures.thread',
    'concurrent.futures.process',

    # 数据库
    'aiosqlite',
    'sqlite3',

    # 网络
    'h11',
    'websockets',
    'requests',
    'urllib3',

    # 数据验证
    'pydantic_settings',
    'annotated_types',
    'typing_extensions',

    # 音频
    'audioread',
    'audioread.rawread',
    'audioread.ffdec',
    'mutagen',
    'mutagen.mp4',
    'mutagen.flac',
    'mutagen.oggvorbis',
    'mutagen.easymp4',
    'tinytag',
    'sounddevice',
    'wave',

    # 工具
    'yaml',
    'regex',
    'tqdm',
    'filelock',
    'fsspec',
    'packaging',
    'packaging.version',
    'packaging.specifiers',
    'joblib',
    'threadpoolctl',
    'pkg_resources',

    # 编码
    'encodings',
    'encodings.utf_8',
    'encodings.ascii',
    'encodings.latin_1',
    'encodings.idna',
]

# ==================== 过滤不必要的数据文件（减小体积 + 避免 EMFILE）====================
# torch/include/ 包含数千个 ATen C++ 头文件，仅用于编译 PyTorch 扩展，
# 运行时推理完全不需要。保留这些文件会导致 macOS 签名时 EMFILE: too many open files。
# torch/share/  包含 CMake 配置文件，同样仅编译时用。
# caffe2/proto/ 包含 .proto 源文件，运行时不需要。
_build_only_extensions = ('.h', '.hpp', '.cmake', '.pc', '.prl', '.proto')
_build_only_dest_prefixes = ('torch/include', 'torch/share', 'caffe2/proto')

datas = [
    (src, dest) for (src, dest) in datas
    if not (
        any(str(src).endswith(ext) for ext in _build_only_extensions)
        or any(dest.replace('\\', '/').startswith(p) for p in _build_only_dest_prefixes)
    )
]
print(f'[spec] After filtering build-only files: {len(datas)} datas remaining')

# ==================== 分析阶段 ====================
a = Analysis(
    [str(backend_dir / 'main.py')],
    pathex=[str(backend_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 过滤不需要的二进制文件以减小体积
_binaries_to_exclude = [
    'Qt5', 'Qt6', 'QtCore', 'QtGui', 'QtWidgets',
    'opencv', 'cv2',
    'tk', 'tcl',
]
a.binaries = [
    b for b in a.binaries
    if not any(x in str(b[0]) for x in _binaries_to_exclude)
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ==================== 构建阶段 - onedir 模式 ====================
exe_name = 'soundbot-backend'
exe_name_with_ext = exe_name + ('.exe' if sys.platform == 'win32' else '')

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=exe_name_with_ext,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=exe_name,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
)
