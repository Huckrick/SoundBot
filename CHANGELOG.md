# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

#### 🔧 PyInstaller 打包修复
- **修复模型路径解析问题** - 新增 `get_clap_model_name()` 函数，运行时动态获取模型路径，解决打包后路径硬编码问题
- **添加缺失的模块** - 创建 `core/__init__.py` 和 `utils/__init__.py` 确保 Python 正确识别包结构
- **修复 Windows 多进程** - 在 `main.py` 中添加 `multiprocessing.freeze_support()`
- **更新 hiddenimports** - 修复 ChromaDB 1.5.5 模块路径，添加 soundfile 二进制库自动收集

#### 📝 配置文件更新
- **`.github/workflows/build-fast.yml`** - 修改触发条件，从 "每次推送 main" 改为 "仅推送 tag 时触发"

### Added

- **BUILD_FIX_README.md** - PyInstaller 打包修复详细文档
- **scripts/test_pyinstaller.py** - 打包环境检查脚本
- **scripts/verify_model_path.py** - 模型路径解析验证工具

## [0.1.2] - 2024-03-22

### Added
- PyInstaller 一体化架构，无需单独 Python 环境
- 支持 macOS (Universal) 和 Windows (x64)
- AI 语义搜索功能
- 工程管理功能
- 音频波形显示
- 拖拽导出功能

### Dependencies
- Python 3.12
- Electron 28.x
- ChromaDB 1.5.5
- Transformers 5.3.0
- PyTorch 2.10.0
