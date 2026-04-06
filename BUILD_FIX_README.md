# SoundBot PyInstaller 打包修复指南

## 修复概述

针对 SoundBot 项目的 PyInstaller 打包问题，进行了以下关键修复：

### 1. 更新了 `backend/main.spec`
- 添加了完整的 `hiddenimports`，包括 ChromaDB、soundfile、torch 的所有子模块
- 添加了 `binaries` 配置，自动收集 soundfile 的 libsndfile 和 torch 的库文件
- 优化了数据文件收集，确保 `__init__.py` 文件被正确包含

### 2. 确保 `__init__.py` 文件存在
- `backend/core/__init__.py`
- `backend/utils/__init__.py`  
- `backend/models/__init__.py`

### 3. 修复了 `backend/main.py`
- 在 `if __name__ == "__main__"` 块中添加了 `multiprocessing.freeze_support()`
- 这是 Windows 下打包后正确运行多进程的必要步骤

---

## 关键问题修复详情

### 问题 1：隐式依赖缺失 ✅ 修复

**原因**：PyInstaller 无法自动检测动态导入的模块

**修复**：在 .spec 文件中添加了完整的 hiddenimports 列表：
- ChromaDB 所有子模块（`chromadb.db.impl.*`, `chromadb.segment.*` 等）
- soundfile 底层绑定（`soundfile._soundfile`）
- torch 和 transformers 的所有子模块
- ONNX Runtime

### 问题 2：静态资源缺失 ✅ 修复

**原因**：`__init__.py` 文件可能缺失，导致 Python 无法识别目录为包

**修复**：创建了所有必要的 `__init__.py` 文件，并在 .spec 中显式包含

### 问题 3：二进制依赖缺失 ✅ 修复

**原因**：soundfile 需要 libsndfile 二进制库，torch 需要平台特定的库文件

**修复**：在 .spec 中添加了自动检测和收集这些二进制文件的逻辑：
```python
# 自动查找 soundfile 的 libsndfile
import soundfile
soundfile_dir = Path(soundfile.__file__).parent
# Windows: libsndfile-1.dll
# macOS: libsndfile.dylib  
# Linux: libsndfile.so

# 自动查找 torch 库文件
torch_lib = Path(torch.__file__).parent / 'lib'
```

### 问题 4：路径问题 ✅ 已正确处理

`backend/config.py` 已正确实现动态路径解析：
```python
def get_executable_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent  # PyInstaller 环境
    else:
        return Path(__file__).parent  # 开发环境
```

### 问题 5：多进程/子进程问题 ✅ 修复

**原因**：Windows 下 PyInstaller 打包的应用需要使用 `multiprocessing.freeze_support()`

**修复**：在 `main.py` 的入口添加了：
```python
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # Windows 必需
    uvicorn.run(...)
```

### 问题 6：ChromaDB 特殊问题 ✅ 修复

**原因**：ChromaDB 1.5.5 使用动态导入加载数据库后端

**修复**：在 hiddenimports 中添加了完整的 ChromaDB 模块链：
- `chromadb.db.impl.*`
- `chromadb.segment.impl.*`
- `chromadb.telemetry.product`
- `duckdb` 和 `aiosqlite`

---

## 构建命令

### 1. 安装依赖

```bash
# 进入项目目录
cd /path/to/SoundBot

# 安装 PyInstaller
pip install pyinstaller

# 安装后端依赖
pip install -r backend/requirements.txt
```

### 2. 构建后端（PyInstaller）

```bash
# 方法 1：使用 spec 文件构建（推荐）
cd backend
python -m PyInstaller main.spec --clean --noconfirm

# 方法 2：使用构建脚本
cd /path/to/SoundBot
python scripts/build.py --skip-electron
```

### 3. 验证后端构建

```bash
# 检查输出目录
ls -la dist/backend/soundbot-backend/

# 应该包含：
# - soundbot-backend (可执行文件)
# - _internal/ 或 lib/ 目录
# - base_library.zip

# 测试运行（Windows）
dist/backend/soundbot-backend/soundbot-backend.exe

# 测试运行（macOS/Linux）
dist/backend/soundbot-backend/soundbot-backend
```

### 4. 完整构建（Electron + PyInstaller）

```bash
# 构建当前平台
npm run build

# 或直接使用 Python 脚本
python scripts/build.py

# 构建特定平台
python scripts/build.py --platform windows
python scripts/build.py --platform macos
python scripts/build.py --platform linux
```

---

## 常见问题排查

### 问题："No module named 'xxx'"

**解决**：将该模块添加到 `backend/main.spec` 的 `hiddenimports` 列表中

### 问题："libsndfile not found"

**解决**：确保 soundfile 包已正确安装：
```bash
pip install soundfile --force-reinstall
```

### 问题：后端启动后立即退出

**排查步骤**：
1. 直接运行后端可执行文件查看错误输出
2. 检查模型文件是否存在
3. 查看日志文件（用户数据目录下的 logs/ 文件夹）

### 问题：Windows Defender 报毒

**解决**：
1. 将软件安装目录加入杀毒软件白名单
2. 使用代码签名证书签名可执行文件
3. 向 Microsoft 提交误报申诉

---

## 文件清单

修复涉及的文件：

1. `backend/main.spec` - PyInstaller 配置文件（已更新）
2. `backend/main.py` - 添加上 freeze_support
3. `backend/core/__init__.py` - 确保存在
4. `backend/utils/__init__.py` - 确保存在
5. `backend/models/__init__.py` - 确保存在

---

## 后续优化建议

1. **代码签名**：为 Windows 可执行文件添加代码签名，避免杀毒软件误报
2. **体积优化**：使用 UPX 压缩可执行文件
3. **增量构建**：配置 CI/CD 缓存以加速构建
4. **模型打包**：考虑将 AI 模型打包到安装包中，或提供首次启动下载
