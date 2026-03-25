# -*- mode: python ; coding: utf-8 -*-
"""
SoundBot Backend PyInstaller Spec
打包为目录模式，支持 Windows/macOS/Linux
方案A: 补充所有缺失的 hiddenimports
方案C: 使用 onedir 模式提高稳定性
"""

import sys
import os
from pathlib import Path

# 获取当前 spec 文件所在目录
spec_file = Path(os.path.abspath(sys.argv[0]))
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

# 隐藏导入 - 包含所有需要的依赖 (方案A：补充所有缺失模块)
hiddenimports = [
    # FastAPI / Uvicorn
    'uvicorn',
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.wsproto_impl',
    'uvicorn.asyncio',
    'uvicorn.asyncio_driver',
    'fastapi',
    'fastapi.middleware.cors',
    'starlette',
    'starlette.middleware',
    'starlette.middleware.cors',
    'starlette.middleware.errors',
    'starlette.responses',
    'starlette.routing',
    'starlette.status',

    # 数据库
    'chromadb',
    'chromadb.config',
    'chromadb.api',
    'chromadb.api.segment',
    'chromadb.api.models',
    'chromadb.db',
    'chromadb.db.sqlite',
    'chromadb.db.duckdb',
    'chromadb.collection',
    'chromadb.errors',
    'aiosqlite',
    'sqlite3',

    # 数据验证
    'pydantic',
    'pydantic_settings',
    'pydantic_core',
    'pydantic.deprecated',
    'pydantic.deprecated.decorator',
    'annotated_types',
    'typing_extensions',

    # AI/ML 核心 - torch (方案A：添加完整模块)
    'torch',
    'torch.testing',
    'torch.nn',
    'torch.nn.modules',
    'torch.nn.functional',
    'torch.nn.utils',
    'torch.utils',
    'torch.utils.data',
    'torch.cuda',
    'torch.backends',
    'torch.backends.cudnn',
    'torch.backends.mps',
    'torch.optim',
    'torch.distributed',
    'torch.multiprocessing',
    'torch.jit',
    'torch.jit._script',
    'torch.jit._trace',
    'torch.jit._onnx',
    'torch.jit._serialization',
    'torch._dynamo',
    'torch._dynamo.config',
    'torch._dynamo.backends',
    'torch._dynamo.utils',
    'torch._dynamo.output_graph',
    'torch._dynamo.bytecode_analysis',
    'torch._dynamo.bytecode_transformation',
    'torch._inductor',
    'torch._inductor.config',
    'torch._inductor.utils',
    'torch._inductor.codegen',
    'torch._inductor.graph',
    'torch._inductor.ir',
    'torch._C',
    'torch._VF',
    'torchaudio',
    'torchaudio.models',
    'torchaudio.utils',
    'torchvision',
    'torchvision.models',
    'torchvision.ops',
    'torchvision.transforms',

    # transformers (方案A：添加完整 clap 模块)
    'transformers',
    'transformers.utils',
    'transformers.utils.generic',
    'transformers.utils.import_utils',
    'transformers.configuration_utils',
    'transformers.feature_extraction_utils',
    'transformers.modeling_utils',
    'transformers.models',
    'transformers.models.clap',
    'transformers.models.clap.modeling_clap',
    'transformers.models.clap.configuration_clap',
    'transformers.models.clap.feature_extraction_clap',
    'transformers.models.clap.processing_clap',
    'transformers.models.clap.tokenization_clap',
    'transformers.models.clap.audio_processing_clap',
    'transformers.models.clap.modular_clap',
    'sentence_transformers',
    'sentence_transformers.SentenceTransformer',
    'sentence_transformers.models',

    # 数值计算
    'numpy',
    'numpy.core',
    'numpy.core._dtype_ctypes',
    'numpy.core.multiarray',
    'numpy.core.numeric',
    'numpy.linalg',
    'numpy.linalg.linalg',
    'numpy.linalg._umath_linalg',
    'numpy.fft',
    'numpy.fft._pocketfft',
    'numpy.random',
    'numpy.random.mtrand',
    'numpy.lib',
    'numpy.lib.function_base',
    'scipy',
    'scipy.sparse',
    'scipy.sparse.csgraph',
    'scipy.sparse.linalg',
    'scipy.linalg',
    'scipy.linalg.cython_lapack',
    'scipy.linalg.flapack_gen',
    'scipy.special',
    'scipy.special._orthogonal',
    'scipy.integrate',
    'scipy.stats',
    'scipy.optimize',

    # 音频处理
    'librosa',
    'librosa.core',
    'librosa.core.audio',
    'librosa.core.spectrum',
    'librosa.feature',
    'librosa.feature.utils',
    'librosa.util',
    'librosa.display',
    'librosa.effects',
    'librosa.beat',
    'soundfile',
    'soundfile.library',
    'audioread',
    'audioread.rawread',
    'audioread.ffdec',
    'audioread.gdec',
    'mutagen',
    'mutagen.easymp4',
    'mutagen.mp4',
    'mutagen.flac',
    'mutagen.oggvorbis',
    'tinytag',
    'numba',
    'numba.core',
    'numba.core.runtime',
    'numba.core.compiler',
    'numba.core.cpu',
    'numba.misc',
    'llvmlite',
    'llvmlite.ir',
    'llvmlite.binding',
    'soxr',

    # 机器学习
    'sklearn',
    'sklearn.utils',
    'sklearn.utils._encode',
    'sklearn.utils._mask',
    'sklearn.preprocessing',
    'sklearn.preprocessing.data',
    'sklearn.preprocessing.label',
    'sklearn.decomposition',
    'sklearn.decomposition._dict_learning',
    'sklearn.decomposition._nmf',
    'sklearn.cluster',
    'sklearn.cluster._kmeans',
    'joblib',
    'joblib.externals',
    'joblib.externals.cloudpickle',
    'joblib.memory',
    'threadpoolctl',

    # 网络/HTTP
    'requests',
    'requests.api',
    'requests.models',
    'requests.sessions',
    'urllib3',
    'urllib3.util',
    'urllib3.request',
    'urllib3.response',
    'httpx',
    'httpx._client',
    'httpcore',
    'httpcore._sync',
    'h11',
    'h11._util',

    # 工具库
    'jieba',
    'jieba.posseg',
    'jieba.analyse',
    'yaml',
    'yaml.cyaml',
    'regex',
    'regex._regex',
    'tokenizers',
    'tokenizers.implementations',
    'tokenizers.models',
    'safetensors',
    'safetensors.torch',
    'safetensors.numpy',
    'packaging',
    'packaging.version',
    'packaging.specifiers',
    'packaging.requirements',
    'filelock',
    'fsspec',
    'fsspec.implementations',
    'fsspec.utils',
    'tqdm',
    'tqdm.std',
    'tqdm.auto',

    # Hugging Face
    'huggingface_hub',
    'huggingface_hub.file_download',
    'huggingface_hub.utils',

    # 其他
    'asyncio',
    'asyncio.base_events',
    'asyncio.coroutines',
    'asyncio.events',
    'asyncio.queues',
    'concurrent.futures',
    'concurrent.futures._base',
    'pathlib',
    'pathlib._local',
    'json',
    'json.decoder',
    'json.encoder',
    're',
    'math',
    'random',
    'datetime',
    'hashlib',
    'hashlib._md5',
    'hashlib._sha1',
    'hashlib._sha256',
    'hashlib._sha512',
    'urllib',
    'urllib.parse',
    'urllib.request',
    'collections',
    'collections.abc',
    'functools',
    'functools._functools',
    'itertools',
    'itertools._itertools',
    'contextlib',
    'contextlib._contextlib',
    'typing',
    'typing.io',
    'typing.re',
    'inspect',
    'warnings',
    'warnings._warnings',
    'traceback',
    'traceback.format_exc',
    'logging',
    'logging.handlers',
    'logging.config',
    'io',
    'io.BytesIO',
    'io.StringIO',
    'struct',
    'weakref',
    'gc',
    'sys',
    'os',
    'time',
    'shutil',
    'tempfile',
    'errno',
    'signal',
    'mmap',
    'zipfile',
    'zipfile._path',
    'tarfile',
    'gzip',
    'bz2',
    'lzma',
    'copy',
    'pickle',
    'pickle._pickle',
    'base64',
    'binascii',
    'calendar',
    'codecs',
    'copyreg',
    'dis',
    'getopt',
    'getpass',
    'glob',
    'linecache',
    'optparse',
    'pprint',
    'queue',
    'quopri',
    'reprlib',
    'shlex',
    'string',
    'textwrap',
    'token',
    'tokenize',
    'types',
    'typing_extensions._utils',
    'urllib.error',
    'urllib.robotparser',
]

# 排除项 - 大幅减小体积（保留必要的 torch 模块）
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
    # 注意：不要排除 'pdb'，PyTorch 需要它
    'pdbpp', 'ipdb', 'pudb', 'pydevd',
    'cProfile', 'profile', 'pstats',

    # 注意：不要排除 torch.testing，PyTorch 需要它
    # 'torch.testing',
    'torch.distributions',

    # 不必要的 transformers 功能（已移除，避免排除必要的）
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

# 方案C: 使用 onedir 模式
# 先创建 EXE，再通过 COLLECT 收集所有依赖到目录
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
