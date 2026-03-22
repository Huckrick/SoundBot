# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for SoundBot backend
"""

import sys
import os
from pathlib import Path

block_cipher = None

# 获取项目根目录（spec 文件所在目录的父目录）
spec_dir = Path(os.getcwd())
backend_dir = spec_dir
project_root = backend_dir.parent

# 添加数据文件
datas = [
    # 配置文件
    (str(backend_dir / 'core'), 'core'),
    (str(backend_dir / 'routers'), 'routers'),
    (str(backend_dir / 'models'), 'models'),
    (str(backend_dir / 'utils'), 'utils'),
    (str(backend_dir / 'config.py'), '.'),
    (str(backend_dir / 'database.py'), '.'),
]

# 添加模型文件（如果存在）
models_dir = project_root / 'models'
if models_dir.exists():
    datas.append((str(models_dir), 'models'))

# 隐藏导入（PyInstaller 自动检测不到的依赖）
hiddenimports = [
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'fastapi.middleware.cors',
    'chromadb',
    'chromadb.config',
    'transformers',
    'torch',
    'torchaudio',
    'numpy',
    'soundfile',
    'pydantic',
    'pydantic_settings',
]

a = Analysis(
    [str(backend_dir / 'main.py')],
    pathex=[str(backend_dir)],
    binaries=[],
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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SoundBot-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 显示控制台窗口用于调试
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# macOS 单文件模式
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='SoundBot-backend.app',
        icon=None,
        bundle_identifier='com.nagisahuckrick.soundbot.backend',
    )
