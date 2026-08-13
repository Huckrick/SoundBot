# SoundBot 构建与资源脚本

本目录只保留当前仍在使用的构建与资源脚本。应用安装包必须在目标系统的原生宿主上构建：Windows x64 使用 Windows runner，macOS arm64 使用 Apple Silicon runner。

## 常用命令

```bash
# 当前宿主的 PyInstaller 后端与 Electron 安装包
python scripts/build.py

# 仅清理构建输出（跨平台）
python scripts/build.py --clean

# 静态检查 PyInstaller 环境；加 --build 执行真实冻结构建
python scripts/test_pyinstaller.py
python scripts/test_pyinstaller.py --build

# 下载开发用 CLAP 模型
python scripts/download_models.py

# 验证开发/冻结环境的模型路径解析
python scripts/verify_model_path.py

# 预览版本同步（默认不写文件）；确认后原子更新全部版本来源
python scripts/bump_version.py --version 0.2.1
python scripts/bump_version.py --version 0.2.1 --write
```

版本工具只接受不带 `v` 前缀的 SemVer（如 `0.2.1` 或 `0.3.0-rc.1`），且拒绝降级或重复版本。它会先在内存中校验 `package.json`、`package-lock.json`、`backend/config.py`、双语 README、手动 Release 模板与 `CHANGELOG.md` 全部一致，再执行原子替换；若目标版本已存在更新日志区段则拒绝覆盖。

`download_manager.py` 是源码仓库中的手动模型安装工具，当前不打入 Electron 安装包，也没有被桌面端自动调用。它会按当前应用版本请求精确的 GitHub Release tag，要求同时提供 `models.zip` 与 `models.zip.sha256`，并在原子安装前验证根级 `model-manifest.json`、固定模型 revision、逐文件 SHA-256 和压缩包路径安全。

完整环境要求、模型布局、发布门禁与故障诊断见仓库根目录的 `README.md` / `README.en.md`。
