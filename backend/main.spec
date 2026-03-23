# -*- mode: python ; coding: utf-8 -*-
"""
SoundBot Backend PyInstaller Spec
打包为独立可执行文件，支持 Windows/macOS/Linux
"""

import sys
import os
from pathlib import Path

# 获取当前 spec 文件所在目录
spec_file = Path(os.path.abspath(SPECFILE))
spec_dir = spec_file.parent
backend_dir = spec_dir
project_root = backend_dir.parent

block_cipher = None

# 数据文件配置 - 只包含代码，不包含模型
datas = []

# 添加 core 目录
if (backend_dir / 'core').exists():
    datas.append((str(backend_dir / 'core'), 'core'))

# 添加 utils 目录
if (backend_dir / 'utils').exists():
    datas.append((str(backend_dir / 'utils'), 'utils'))

# 添加 config.py
if (backend_dir / 'config.py').exists():
    datas.append((str(backend_dir / 'config.py'), '.'))

# 添加 models/schemas.py (Pydantic模型)
if (backend_dir / 'models').exists():
    datas.append((str(backend_dir / 'models'), 'models'))

# 添加 bootstrap.py
if (backend_dir / 'bootstrap.py').exists():
    datas.append((str(backend_dir / 'bootstrap.py'), '.'))

# 隐藏导入 - 包含所有需要的依赖
hiddenimports = [
    # FastAPI / Uvicorn
    'uvicorn.logging',
    'uvicorn.loops.auto',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto',
    'fastapi.middleware.cors',
    'starlette.middleware.cors',
    'starlette.middleware.errors',
    
    # 数据库
    'chromadb',
    'chromadb.config',
    'chromadb.api.segment',
    'aiosqlite',
    'sqlite3',
    
    # 数据验证
    'pydantic',
    'pydantic_settings',
    'pydantic_core',
    'pydantic.deprecated.decorator',
    
    # AI/ML 核心
    'torch',
    'torchaudio',
    'torchvision',
    'torch.nn',
    'torch.utils',
    'transformers',
    'transformers.models.clap',
    'transformers.models.clap.modeling_clap',
    'transformers.models.clap.configuration_clap',
    'transformers.models.clap.feature_extraction_clap',
    'transformers.models.clap.processing_clap',
    'transformers.models.clap.tokenization_clap',
    'sentence_transformers',
    
    # 数值计算
    'numpy',
    'numpy.core._dtype_ctypes',
    'scipy',
    'scipy.sparse',
    'scipy.linalg',
    'scipy.special',
    'scipy.integrate',
    
    # 音频处理
    'librosa',
    'librosa.core',
    'librosa.feature',
    'librosa.util',
    'soundfile',
    'audioread',
    'audioread.rawread',
    'audioread.ffdec',
    'mutagen',
    'tinytag',
    'numba',
    'numba.core',
    'llvmlite',
    'soxr',
    
    # 机器学习
    'sklearn',
    'sklearn.utils',
    'sklearn.preprocessing',
    'sklearn.decomposition',
    'joblib',
    'threadpoolctl',
    
    # 工具库
    'jieba',
    'jieba.posseg',
    'yaml',
    'requests',
    'httpx',
    'huggingface_hub',
    'regex',
    'tokenizers',
    'safetensors',
    'packaging',
    'packaging.version',
    'packaging.specifiers',
    'filelock',
    'fsspec',
    'tqdm',
    'typing_extensions',
    
    # 其他
    'asyncio',
    'concurrent.futures',
    'pathlib',
    'json',
    're',
    'math',
    'random',
    'datetime',
    'hashlib',
    'urllib',
    'urllib.parse',
    'collections',
    'functools',
    'itertools',
    'contextlib',
    'typing',
    'inspect',
    'warnings',
    'traceback',
    'logging',
    'logging.handlers',
]

# 排除项 - 大幅减小体积
excludes = [
    # 测试相关
    'pytest', '_pytest', 'unittest', 'unittest.mock', 'doctest', 'test', 'tests',
    'nose', 'nose2', 'trial', 'tox',
    
    # GUI 相关 (后端不需要)
    'matplotlib', 'matplotlib.pyplot', 'matplotlib.backends',
    'PIL', 'PIL.Image', 'cv2', 'opencv',
    'tkinter', 'Tkinter', '_tkinter',
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'PyQt4',
    'wx', 'wxPython', 'kivy', 'pyglet',
    
    # 文档工具
    'sphinx', 'docutils', 'jinja2.ext.debug',
    
    # 开发工具
    'ipython', 'IPython', 'jupyter', 'notebook', 'nbconvert', 'nbformat',
    'pdb', 'pdbpp', 'ipdb', 'pudb', 'pydevd',
    'cProfile', 'profile', 'pstats',
    
    # 不必要的 torch 模块
    'torch.testing', 'torch.distributions', 
    'torch.utils.tensorboard', 'torch.utils.cpp_extension',
    'torch.jit.frontend', 'torch.jit.annotations',
    'torch.onnx', 'torch.export',
    
    # 不必要的 transformers 功能
    'transformers.pipelines.automatic_speech_recognition',
    'transformers.pipelines.image_classification',
    'transformers.pipelines.object_detection',
    'transformers.pipelines.image_segmentation',
    'transformers.pipelines.zero_shot_image_classification',
    'transformers.models.vision_encoder_decoder',
    'transformers.models.vit', 'transformers.models.deit',
    'transformers.models.beit', 'transformers.models.swin',
    
    # 其他大体积但不需要的
    'google.protobuf.pyext',
    'grpc_tools', 'grpcio_tools',
    'tensorflow', 'tf', 'keras',
    'tensorboard', 'tensorboardX',
    'wandb', 'mlflow', 'comet_ml',
    'datasets', 'evaluate', 'accelerate',
    'optuna', 'ray', 'hyperopt',
]

# 分析阶段
a = Analysis(
    [str(backend_dir / 'main.py')],
    pathex=[str(backend_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=1,
)

# 过滤掉不需要的二进制文件
binaries_to_exclude = [
    'Qt5', 'Qt6', 'QtCore', 'QtGui', 'QtWidgets',
    'opencv', 'cv2',
    'tk', 'tcl',
]
a.binaries = [b for b in a.binaries if not any(x in str(b[0]) for x in binaries_to_exclude)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# 可执行文件配置
exe_name = 'soundbot-backend'
if sys.platform == 'win32':
    exe_name += '.exe'

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
