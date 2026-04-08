# -*- mode: python ; coding: utf-8 -*-
"""
SoundBot Backend PyInstaller Spec
打包为目录模式，支持 Windows/macOS/Linux

修复说明：
1. 添加了完整的 hiddenimports，包括 ChromaDB、soundfile、torch 的子模块
2. 添加了二进制文件收集（soundfile 的 libsndfile，torch 的库文件）
3. 添加了数据文件收集（transformers 的配置文件）
4. 针对 Windows 添加了运行时钩子支持 multiprocessing
"""

import sys
import os
from pathlib import Path
import site

# 获取当前 spec 文件所在目录
spec_file = Path(os.path.abspath(sys.argv[0]))
spec_dir = spec_file.parent
backend_dir = spec_dir
project_root = backend_dir.parent

block_cipher = None

# ==================== 数据文件配置 ====================
datas = []

# 添加 main.py (关键：uvicorn 需要能 import main)
if (backend_dir / 'main.py').exists():
    datas.append((str(backend_dir / 'main.py'), '.'))

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

# 添加初始化文件确保目录被识别为包
for subdir in ['core', 'utils', 'models']:
    init_file = backend_dir / subdir / '__init__.py'
    if init_file.exists():
        datas.append((str(init_file), subdir))

# ==================== 查找二进制文件 ====================
binaries = []

# 查找 soundfile 的 libsndfile
try:
    import soundfile
    soundfile_dir = Path(soundfile.__file__).parent
    # Windows
    if sys.platform == 'win32':
        libsndfile = soundfile_dir / '_soundfile_data' / 'libsndfile-1.dll'
        if libsndfile.exists():
            binaries.append((str(libsndfile), '.'))
    # macOS
    elif sys.platform == 'darwin':
        libsndfile = soundfile_dir / '_soundfile_data' / 'libsndfile.dylib'
        if libsndfile.exists():
            binaries.append((str(libsndfile), '.'))
    # Linux
    else:
        libsndfile = soundfile_dir / '_soundfile_data' / 'libsndfile.so'
        if libsndfile.exists():
            binaries.append((str(libsndfile), '.'))
except Exception as e:
    print(f"Warning: Could not find soundfile library: {e}")

# 添加 torch 库文件（如果有）
try:
    import torch
    torch_lib = Path(torch.__file__).parent / 'lib'
    if torch_lib.exists():
        # 收集所有必要的库文件
        for lib_file in torch_lib.glob('*.dll'):
            binaries.append((str(lib_file), 'torch/lib'))
        for lib_file in torch_lib.glob('*.so*'):
            binaries.append((str(lib_file), 'torch/lib'))
        for lib_file in torch_lib.glob('*.dylib'):
            binaries.append((str(lib_file), 'torch/lib'))
except Exception as e:
    print(f"Warning: Could not find torch libraries: {e}")

# ==================== 隐藏导入配置 ====================
hiddenimports = [
    # 入口模块 (关键)
    'main',
    'config',
    'bootstrap',

    # 确保本地模块被打包
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
    'utils',
    'utils.logger',
    'utils.audio_utils',
    'models',
    'models.schemas',

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
    'uvicorn.protocols.websockets.websockets_impl',
    'fastapi',
    'fastapi.middleware.cors',
    'fastapi.middleware.trustedhost',
    'fastapi.middleware.httpsredirect',
    'fastapi.middleware.gzip',
    'fastapi.openapi',
    'fastapi.openapi.docs',
    'fastapi.openapi.utils',
    'starlette',
    'starlette.middleware',
    'starlette.middleware.cors',
    'starlette.middleware.errors',
    'starlette.middleware.exceptions',
    'starlette.middleware.gzip',
    'starlette.middleware.trustedhost',
    'starlette.middleware.httpsredirect',
    'starlette.responses',
    'starlette.routing',
    'starlette.status',
    'starlette.requests',
    'starlette.websockets',
    'starlette.background',
    'starlette.concurrency',
    'starlette.datastructures',
    'starlette.types',
    'starlette.config',
    'starlette.exceptions',
    'starlette.staticfiles',
    'starlette.templating',

    # 后端核心模块
    'core.database',
    'core.audio_cache',
    'core.websocket_manager',
    'core.playback_manager',
    'core.scanner',
    'core.embedder',
    'core.indexer',
    'core.searcher',
    'core.search_engine',
    'core.llm_config_manager',
    'core.llm_client',
    'core.ai_chat_service',
    'core.model_preloader',
    'core.ucs_keywords',

    # 工具模块
    'utils.logger',
    'utils.audio_utils',

    # ChromaDB - 完整的导入链（基于 ChromaDB 1.5.5 实际模块结构）
    'chromadb',
    'chromadb.config',
    'chromadb.api',
    'chromadb.api.segment',
    'chromadb.api.models',
    'chromadb.db',
    'chromadb.db.impl',
    'chromadb.db.impl.sqlite',
    'chromadb.db.impl.sqlite_pool',
    'chromadb.db.base',
    'chromadb.db.mixins',
    'chromadb.db.system',
    'chromadb.segment',
    'chromadb.segment.impl',
    'chromadb.segment.impl.metadata',
    'chromadb.segment.impl.metadata.sqlite',
    'chromadb.segment.impl.vector',
    'chromadb.segment.impl.vector.batch',
    'chromadb.segment.impl.vector.brute_force_index',
    'chromadb.segment.impl.vector.hnsw_params',
    'chromadb.segment.impl.vector.local_hnsw',
    'chromadb.segment.impl.vector.local_persistent_hnsw',
    'chromadb.segment.impl.manager',
    'chromadb.segment.impl.manager.local',
    'chromadb.segment.impl.manager.distributed',
    'chromadb.segment.distributed',
    'chromadb.execution',
    'chromadb.execution.executor',
    'chromadb.execution.expression',
    'chromadb.ingest',
    'chromadb.ingest.impl',
    'chromadb.telemetry',
    'chromadb.telemetry.product',
    'chromadb.errors',
    'chromadb.utils',
    'chromadb.utils.embedding_functions',
    'chromadb.migrations',
    'chromadb.base_types',
    'aiosqlite',
    'sqlite3',
    'sqlite3.dbapi2',

    # 数据验证
    'pydantic',
    'pydantic_settings',
    'pydantic_core',
    'pydantic.deprecated',
    'pydantic.deprecated.decorator',
    'pydantic.json_schema',
    'pydantic.root_model',
    'pydantic.v1',
    'pydantic.v1.fields',
    'pydantic.v1.main',
    'pydantic.v1.types',
    'annotated_types',
    'typing_extensions',

    # AI/ML 核心 - torch
    'torch',
    'torch.testing',
    'torch.nn',
    'torch.nn.modules',
    'torch.nn.modules.activation',
    'torch.nn.modules.batchnorm',
    'torch.nn.modules.container',
    'torch.nn.modules.conv',
    'torch.nn.modules.dropout',
    'torch.nn.modules.flatten',
    'torch.nn.modules.linear',
    'torch.nn.modules.loss',
    'torch.nn.modules.module',
    'torch.nn.modules.normalization',
    'torch.nn.modules.padding',
    'torch.nn.modules.pooling',
    'torch.nn.functional',
    'torch.nn.utils',
    'torch.nn.utils.rnn',
    'torch.utils',
    'torch.utils.data',
    'torch.utils.data.dataloader',
    'torch.utils.data.dataset',
    'torch.utils.data.sampler',
    'torch.utils._contextlib',
    'torch.cuda',
    'torch.backends',
    'torch.backends.cudnn',
    'torch.backends.mps',
    'torch.backends.cpu',
    'torch.optim',
    'torch.optim.adam',
    'torch.optim.adamw',
    'torch.optim.sgd',
    'torch.optim.lr_scheduler',
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
    'torchaudio.backend',
    'torchvision',
    'torchvision.models',
    'torchvision.ops',
    'torchvision.transforms',

    # transformers - 完整的导入链
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
    'transformers.models.auto',
    'transformers.models.auto.modeling_auto',
    'transformers.models.auto.configuration_auto',
    'transformers.models.auto.tokenization_auto',
    'transformers.models.auto.feature_extraction_auto',
    'transformers.models.auto.processing_auto',
    'transformers.tokenization_utils',
    'transformers.tokenization_utils_base',
    'transformers.pipelines',
    'transformers.pipelines.base',
    'transformers.pipelines.audio_classification',
    'transformers.pipelines.automatic_speech_recognition',
    'transformers.onnx',
    'transformers.onnx.config',
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
    'soundfile._soundfile',  # 关键：soundfile 的底层绑定
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
    'requests.adapters',
    'requests.cookies',
    'requests.exceptions',
    'requests.hooks',
    'requests.status_codes',
    'requests.structures',
    'requests.utils',
    'urllib3',
    'urllib3.util',
    'urllib3.request',
    'urllib3.response',
    'urllib3.connection',
    'urllib3.connectionpool',
    'urllib3.exceptions',
    'urllib3.fields',
    'urllib3.filepost',
    'urllib3.poolmanager',
    'httpx',
    'httpx._client',
    'httpx._config',
    'httpx._exceptions',
    'httpx._models',
    'httpx._types',
    'httpx._urls',
    'httpx._utils',
    'httpcore',
    'httpcore._sync',
    'httpcore._async',
    'httpcore._exceptions',
    'httpcore._models',
    'httpcore._backends',
    'h11',
    'h11._util',
    'h11._connection',
    'h11._events',
    'h11._headers',
    'h11._readers',
    'h11._state',
    'h11._writers',
    'h11._receivebuffer',

    # 工具库
    'jieba',
    'jieba.posseg',
    'jieba.analyse',
    'yaml',
    'yaml.cyaml',
    'yaml.constructor',
    'yaml.composer',
    'yaml.parser',
    'yaml.scanner',
    'yaml.reader',
    'yaml.resolver',
    'yaml.dumper',
    'yaml.loader',
    'yaml.representer',
    'yaml.serializer',
    'yaml.emitter',
    'regex',
    'regex._regex',
    'tokenizers',
    'tokenizers.implementations',
    'tokenizers.models',
    'tokenizers.trainers',
    'tokenizers.pre_tokenizers',
    'tokenizers.decoders',
    'tokenizers.processors',
    'tokenizers.normalizers',
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
    'tqdm.utils',
    'tqdm.cli',

    # Hugging Face
    'huggingface_hub',
    'huggingface_hub.file_download',
    'huggingface_hub.utils',
    'huggingface_hub.constants',
    'huggingface_hub.hf_api',
    'huggingface_hub.repository',
    'huggingface_hub.snapshot_download',

    # ONNX Runtime
    'onnxruntime',
    'onnxruntime.capi',
    'onnxruntime.capi.onnxruntime_pybind11_state',

    # Python 标准库 - unittest（torch 依赖）
    'unittest',
    'unittest.mock',
    'unittest.case',
    'unittest.suite',
    'unittest.loader',
    'unittest.runner',
    'unittest.result',
    'unittest.signals',
    'unittest.util',

    # Python 标准库 - 常用但被 PyInstaller 遗漏的模块
    'textwrap',           # transformers/torch 常用
    'string',
    'numbers',
    'types',
    'importlib',
    'importlib.metadata',
    'importlib._bootstrap',
    'importlib._bootstrap_external',
    'importlib.machinery',
    'importlib.util',
    'pkgutil',
    'pkg_resources',      # setuptools
    'site',
    '_thread',
    'threading',
    'queue',
    'multiprocessing',
    'multiprocessing.context',
    'multiprocessing.pool',
    'multiprocessing.process',
    'multiprocessing.queues',
    'multiprocessing.reduction',
    'multiprocessing.sharedctypes',
    'multiprocessing.spawn',
    'multiprocessing.synchronize',
    'select',
    'selectors',
    'errno',
    'platform',
    'getpass',
    'getopt',
    'argparse',
    'configparser',
    'codecs',
    'encodings',
    'encodings.utf_8',
    'encodings.latin_1',
    'encodings.ascii',
    'encodings.unicode_escape',
    'encodings.idna',
    'encodings.raw_unicode_escape',
    'fnmatch',
    'glob',
    'linecache',
    'tokenize',
    'keyword',
    'token',
    'ast',
    'dis',
    'csv',
    'pprint',
    'html',
    'html.entities',
    'html.parser',
    'xml',
    'xml.etree',
    'xml.etree.ElementTree',
    'xml.dom',
    'xml.dom.minidom',
    'xml.sax',
    'xml.parsers',
    'xml.parsers.expat',
    'email',
    'email.mime',
    'email.mime.text',
    'email.mime.multipart',
    'email.mime.base',
    'email.header',
    'email.charset',
    'email.encoders',
    'email.utils',
    'email.parser',
    'email.feedparser',
    'mimetypes',
    'netrc',
    'wave',               # WAV音频处理
    'ctypes',
    'ctypes._endian',
    'ctypes.wintypes',    # Windows

    # Python 标准库 - 可能被 torch/transformers 依赖的额外模块
    '_collections',
    '_collections_abc',
    '_functools',
    '_json',
    '_locale',
    '_operator',
    '_posixsubprocess',   # Unix
    '_weakref',
    '_weakrefset',
    'cmath',
    'filecmp',
    'fileinput',
    'heapq',
    'operator',
    'reprlib',
    'stat',
    'statistics',
    '_stat',
    '_io',
    '_warnings',
    '_sitebuiltins',
    'genericpath',
    'posixpath',          # Unix
    'ntpath',             # Windows
    'os.path',

    # 其他必要模块
    'asyncio',
    'asyncio.base_events',
    'asyncio.coroutines',
    'asyncio.events',
    'asyncio.queues',
    'asyncio.tasks',
    'asyncio.streams',
    'asyncio.subprocess',
    'asyncio.threads',
    'concurrent.futures',
    'concurrent.futures._base',
    'concurrent.futures.process',
    'concurrent.futures.thread',
    'pathlib',
    'json',
    're',
    'math',
    'random',
    'datetime',
    'hashlib',
    'urllib',
    'urllib.parse',
    'urllib.request',
    'collections',
    'collections.abc',
    'functools',
    'itertools',
    'contextlib',
    'typing',
    'inspect',
    'warnings',
    'traceback',
    'logging',
    'logging.handlers',
    'logging.config',
    'io',
    'struct',
    'weakref',
    'gc',
    'sys',
    'os',
    'time',
    'shutil',
    'tempfile',
    'signal',
    'mmap',
    'zipfile',
    'tarfile',
    'gzip',
    'bz2',
    'lzma',
    'copy',
    'pickle',
    'base64',
    'binascii',
    'uuid',
    'decimal',
    'fractions',
    'enum',
    'dataclasses',
    'abc',
    'atexit',
    'builtins',
]

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
    optimize=1,
)

# 过滤掉不需要的二进制文件以减小体积
binaries_to_exclude = [
    'Qt5', 'Qt6', 'QtCore', 'QtGui', 'QtWidgets',
    'opencv', 'cv2',
    'tk', 'tcl',
]
a.binaries = [b for b in a.binaries if not any(x in str(b[0]) for x in binaries_to_exclude)]

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
