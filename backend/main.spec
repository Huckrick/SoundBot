# -*- mode: python ; coding: utf-8 -*-
"""
SoundBot Backend PyInstaller Spec
打包为目录模式，仅支持 Windows x64 与 macOS arm64

使用 collect_all() / collect_submodules() 自动收集依赖，
替代手动维护的 hiddenimports 列表，确保打包完整。
"""

import sys
import os
import platform
from importlib.metadata import distribution
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

# ==================== 路径设置 ====================

spec_file = Path(os.path.abspath(sys.argv[0]))
spec_dir = spec_file.parent
backend_dir = spec_dir
project_root = backend_dir.parent

host_system = platform.system()
host_machine = platform.machine().lower()
if host_system == 'Windows':
    if host_machine not in {'amd64', 'x86_64'}:
        raise RuntimeError(f'SoundBot Windows releases require x64, got {host_machine}')
elif host_system == 'Darwin':
    if host_machine not in {'arm64', 'aarch64'}:
        raise RuntimeError(f'SoundBot macOS releases require arm64, got {host_machine}')
else:
    raise RuntimeError(f'SoundBot does not publish a {host_system} backend target')

block_cipher = None

# ==================== 本地模块数据文件 ====================
datas = []

audio_capabilities = project_root / 'config' / 'audio_capabilities.json'
if not audio_capabilities.is_file():
    raise FileNotFoundError(f'Required audio capability manifest is missing: {audio_capabilities}')
datas.append((str(audio_capabilities), 'config'))

# Release notices are kept in the build-test fixtures so the same file is both
# asserted by CI and placed in every frozen runtime.
audio_notices = project_root / 'tests' / 'build' / 'licenses' / 'THIRD_PARTY_AUDIO_NOTICES.txt'
if not audio_notices.is_file():
    raise FileNotFoundError(f'Required third-party audio notice is missing: {audio_notices}')
datas.append((str(audio_notices), 'licenses'))

ucs_workbook = project_root / 'UCS+音效分类中英文对照表.xlsx'
if not ucs_workbook.is_file():
    raise FileNotFoundError(f'Required UCS keyword workbook is missing: {ucs_workbook}')
datas.append((str(ucs_workbook), '.'))

# ==================== 自动收集第三方包 ====================
binaries = []
hiddenimports = []

# 需要完整收集的包（数据文件 + 二进制扩展 + 子模块）
# 这些包含有 PyInstaller 无法自动发现的运行时数据文件
_collect_all_packages = [
    'jieba',                 # 中文分词词典
    'tokenizers',            # Rust 原生扩展
    'safetensors',           # Rust 原生扩展
]

# 只需要收集子模块的包（纯 Python，PyInstaller 自动分析可能遗漏动态导入）
_collect_submodules_packages = [
    'av',                    # Python modules; wheel FFmpeg binaries are collected below
    'uvicorn',
    'starlette',
    'fastapi',
    'pydantic',
    'pydantic_core',
    'httpx',
    'httpcore',
    'anyio',
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

# Chroma's local persistent client only needs its migration SQL and a small
# set of implementations selected by dotted class name at runtime.  Using
# collect_all('chromadb') also freezes the cloud/server/CLI/ONNX/Kubernetes
# stacks, none of which SoundBot invokes.
try:
    datas += collect_data_files('chromadb', includes=['migrations/**/*.sql'])
except Exception as e:
    raise RuntimeError(f'Unable to collect Chroma migration SQL: {e}')

# Preserve PyAV distribution metadata and its bundled license notices.
try:
    datas += copy_metadata('av')
except Exception as e:
    print(f'Warning: copy_metadata(av) failed: {e}')

# auditwheel/delvewheel may put FFmpeg libraries in an ``av.libs`` sibling
# directory rather than inside the import package.  Collect every native file
# declared by the pinned wheel explicitly instead of relying on PATH or a
# system FFmpeg installation.  Also copy the wheel's own license verbatim.
try:
    av_distribution = distribution('av')
    for relative in av_distribution.files or []:
        source = Path(av_distribution.locate_file(relative))
        relative_path = Path(str(relative))
        lower_name = relative_path.name.lower()
        if source.is_file() and source.suffix.lower() in {'.dll', '.dylib', '.so', '.pyd'}:
            binaries.append((str(source), str(relative_path.parent)))
        if source.is_file() and any(token in lower_name for token in ('license', 'copying', 'notice')):
            datas.append((str(source), 'licenses/pyav'))
except Exception as e:
    raise RuntimeError(f'Unable to collect pinned PyAV wheel binaries/licenses: {e}')

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
    'core.index_lifecycle',
    'core.scanner',
    'core.searcher',
    'core.search_engine',
    'core.audio_service',
    'core.websocket_manager',
    'core.model_preloader',
    'core.llm_config_manager',
    'core.llm_client',
    'core.ai_chat_service',
    'core.ucs_keywords',

    # utils / models 子模块
    'utils',
    'utils.logger',
    'models',
    'models.schemas',

    # Transformers lazily resolves these classes from the local CLAP model.
    'transformers.models.clap',
    'transformers.models.clap.configuration_clap',
    'transformers.models.clap.feature_extraction_clap',
    'transformers.models.clap.modeling_clap',
    'transformers.models.clap.processing_clap',
    'transformers.models.roberta',
    'transformers.models.roberta.tokenization_roberta',
    'transformers.models.auto.configuration_auto',
    'transformers.models.auto.feature_extraction_auto',
    'transformers.models.auto.modeling_auto',
    'transformers.models.auto.processing_auto',
    'transformers.models.encoder_decoder.configuration_encoder_decoder',
    'transformers.generation.configuration_utils',
    'transformers.generation.utils',
    'transformers.distributed.configuration_utils',
    'transformers.tokenization_utils_base',
    'transformers.feature_extraction_utils',

    # Chroma selects the local implementations from string settings.
    'chromadb_rust_bindings',
    'chromadb.api.rust',
    'chromadb.db.impl.sqlite',
    'chromadb.execution.executor.local',
    'chromadb.quota.simple_quota_enforcer',
    'chromadb.rate_limit.simple_rate_limit',
    'chromadb.segment.impl.manager.local',
    'chromadb.telemetry.product.posthog',
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
    'sqlite3',

    # 网络
    'h11',
    'websockets',
    'urllib3',

    # 数据验证
    'pydantic_settings',
    'annotated_types',
    'typing_extensions',

    # 音频
    'mutagen',
    'mutagen.mp4',
    'mutagen.flac',
    'mutagen.oggvorbis',
    'mutagen.easymp4',
    'tinytag',
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
    excludes=[
        # Optional developer/cloud/media stacks that are not reachable from
        # SoundBot's local CLAP + Chroma execution path.
        'accelerate',
        'aiofiles',
        'aiosqlite',
        'audioread',
        'cv2',
        'flax',
        'IPython',
        'jax',
        'kubernetes',
        'librosa',
        'llvmlite',
        'matplotlib',
        'numba',
        'onnxruntime',
        'openpyxl',
        'pandas',
        'scipy',
        'sentence_transformers',
        'sklearn',
        'soundfile',
        'soxr',
        'tensorflow',
        'tkinter',
        'torch.utils.tensorboard',
    ],
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

# The Transformers hook copies metadata for every optional package visible in
# the build environment.  Keep frozen availability checks truthful: packages
# excluded above must not reappear as orphaned ``*.dist-info`` directories.
_excluded_metadata_prefixes = (
    'accelerate-', 'aiofiles-', 'aiosqlite-', 'audioread-', 'flax-',
    'jax-', 'jaxlib-', 'kubernetes-', 'librosa-', 'llvmlite-', 'numba-',
    'onnxruntime-', 'openpyxl-', 'pandas-', 'scikit_learn-', 'scipy-',
    'sentence_transformers-', 'soundfile-', 'soxr-', 'tensorflow-',
)
a.datas = [
    item for item in a.datas
    if not any(
        part.casefold().startswith(prefix)
        for part in Path(str(item[0])).parts
        for prefix in _excluded_metadata_prefixes
    )
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
