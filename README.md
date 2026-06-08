# 🎵 SoundBot - AI 音效管理器 / AI Sound Effect Manager

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Version](https://img.shields.io/badge/version-0.1.3-orange.svg)](https://github.com/Huckrick/SoundBot)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Electron](https://img.shields.io/badge/electron-28.x-9feaf9.svg)](https://www.electronjs.org/)

> 用自然语言找到你想要的任何声音 - AI 驱动的智能音效管理器桌面版  
> Find any sound you want using natural language - AI-powered intelligent sound effect manager for desktop

***

## 📥 下载 / Download

**最新版本 / Latest Release**: [v0.1.3](https://github.com/Huckrick/SoundBot/releases/tag/v0.1.3)

### v0.1.3 修复版 / Stability Update

v0.1.3 重点修复 Windows/macOS 打包后的导入、工程隔离、磁盘空间显示和配置持久化问题。升级应用即可，AI 模型包无需重新下载，除非 release 中另行说明。

v0.1.3 focuses on packaged-app import, project isolation, disk space display, and configuration persistence fixes. Upgrade the app package directly. The AI model package does not need to be downloaded again unless the release notes explicitly say otherwise.

### 🆕 新架构：一体化安装包 / New Architecture: All-in-One Installer

从 v0.1.2 开始，SoundBot 采用 **PyInstaller 一体化架构**，应用包已包含后端可执行文件，无需单独配置 Python 环境：

Starting from v0.1.2, SoundBot adopts a **PyInstaller all-in-one architecture**. The app package already includes the backend executable, no separate Python environment setup needed:

### 应用安装包 / App Installers

| 平台 / Platform | 下载链接 / Download | 大小 / Size | 说明 / Description |
|----------------|--------------------|-------------|-------------------|
| **macOS (Universal)** | [📥 SoundBot-0.1.3.dmg](https://github.com/Huckrick/SoundBot/releases/download/v0.1.3/SoundBot-0.1.3.dmg) | ~300-500MB | 支持 Intel 和 Apple Silicon / Supports Intel & Apple Silicon |
| **Windows (x64)** | [📥 SoundBot-Setup-0.1.3.exe](https://github.com/Huckrick/SoundBot/releases/download/v0.1.3/SoundBot-Setup-0.1.3.exe) | ~300-500MB | Windows 10/11 64位 / Windows 10/11 64-bit |

### AI 模型包 / AI Model Package

> ⚠️ **首次安装必须下载 / Required for first-time installation**

| 资源 / Resource | 下载链接 / Download | 大小 / Size | 说明 / Description |
|----------------|--------------------|-------------|-------------------|
| **AI 模型 / Models** | [📦 models.zip](https://github.com/Huckrick/SoundBot/releases/download/v0.1.3/models.zip) | ~500MB-1GB | **所有平台通用 / All platforms** |

### 界面预览 / Screenshot

![SoundBot Home1](https://raw.githubusercontent.com/Huckrick/SoundBot/main/Home1.png)  
![SoundBot Home2](https://raw.githubusercontent.com/Huckrick/SoundBot/main/Home2.png)

***

## 🚀 安装指南 / Installation Guide

### 首次安装 / First-time Installation

#### macOS

1. **下载应用包** / Download app package
   - 下载 `SoundBot-0.1.3.dmg` / Download `SoundBot-0.1.3.dmg`

2. **安装应用** / Install app
   - 打开 `.dmg` 文件，将 SoundBot 拖到 Applications 文件夹 / Open `.dmg` and drag SoundBot to Applications folder
   - 如果提示"无法打开"，请前往 **系统设置 > 隐私与安全性** 允许打开 / If "cannot open" warning appears, go to **System Settings > Privacy & Security** to allow

3. **下载并放置 AI 模型** / Download and place AI models
   ```bash
   # 下载 models.zip 后解压到以下任一位置：
   # Download models.zip and extract to any of the following locations:
   
   # 方法 1：应用包内（推荐 / Recommended）
   # Method 1: Inside app bundle (Recommended)
   # 解压到 /Applications/SoundBot.app/Contents/Resources/models/
   # Extract to /Applications/SoundBot.app/Contents/Resources/models/
   
   # 方法 2：用户数据目录
   # Method 2: User data directory
   # 解压到 ~/Library/Application Support/SoundBot/models/
   # Extract to ~/Library/Application Support/SoundBot/models/
   
   # 方法 3：应用同级目录
   # Method 3: Same level as app
   # 解压到 /Applications/SoundBot.app 同级目录的 models/ 文件夹
   # Extract to models/ folder at the same level as /Applications/SoundBot.app
   ```

4. **启动应用** / Launch
   - 双击 SoundBot 图标启动 / Double-click SoundBot icon to launch
   - 应用会自动检测模型位置 / App will automatically detect model location

#### Windows

1. **下载应用包** / Download app package
   - 下载 `SoundBot-Setup-0.1.3.exe` / Download `SoundBot-Setup-0.1.3.exe`

2. **安装应用** / Install app
   - 运行 `.exe` 安装程序，按向导完成安装 / Run `.exe` installer and follow wizard
   - 默认安装路径 / Default install path：`C:\Users\<Username>\AppData\Local\Programs\SoundBot`

3. **下载并放置 AI 模型** / Download and place AI models
   - 下载 `models.zip` / Download `models.zip`
   - 解压到以下任一位置 / Extract to any of the following locations:
     - 安装目录 / Install directory：`C:\Users\<Username>\AppData\Local\Programs\SoundBot\models\`
     - 用户数据目录 / User data directory：`C:\Users\<Username>\AppData\Roaming\SoundBot\models\`
     - 便携模式 / Portable mode：与 `SoundBot.exe` 同级的 `models\` 文件夹 / `models\` folder at same level as `SoundBot.exe`

4. **启动应用** / Launch
   - 从开始菜单或桌面快捷方式启动 / Launch from Start Menu or desktop shortcut
   - 应用会自动检测模型位置 / App will automatically detect model location

### 模型目录结构 / Model Directory Structure

无论选择哪种放置方式，模型目录结构应为：
Regardless of which location you choose, the model directory structure should be:

```
models/
└── clap/
    ├── config.json
    ├── pytorch_model.bin
    └── preprocessor_config.json
```

### 更新版本 / Update Version

**应用更新** / App Update：
- 下载并安装新的应用包即可 / Just download and install the new app package
- 模型文件**不需要**重新下载（除非新版本明确说明需要更新模型）
- Model files **do not** need to be re-downloaded (unless new version explicitly states model update is required)

**模型更新** / Model Update：
- 如果新版本需要更新模型，会单独更新 `models.zip`
- If new version requires model update, `models.zip` will be updated separately
- 下载后覆盖解压到原位置即可 / Download and extract to original location to overwrite

***

## 🧾 更新日志 / Changelog

### v0.1.3

- 修复打包后导入音频文件、导入文件夹、工程临时目录选择的对话框调用问题。
- 修复 Windows 路径分隔符导致文件夹筛选、父目录识别和导入结果显示异常的问题。
- 修复启动时恢复上次工程只更新界面、不切换后端工程的问题。
- 修复 SQLite 文件记录以路径全局唯一导致不同工程互相覆盖的问题，并加入自动数据库迁移。
- 修复关键词搜索可能跨工程读取文件的问题。
- 修复工程专属临时目录未被磁盘空间检测和导出临时文件实际使用的问题。
- 修复 AI 配置在打包应用中写入资源目录的问题，现在会保存到用户数据目录。
- 修复 LM Studio、Ollama、本地 Embedding 服务地址被安全校验误拦截的问题。
- 修复立体声/多声道音频淡入淡出处理时的数组维度错误。
- 补齐主进程 IPC 处理器，减少渲染进程调用未注册通道导致的运行时错误。
- 加强工程名、文件夹名、文件名和 AI 消息的界面转义，避免特殊字符破坏界面。

***

## 📝 关于本项目 / About This Project

**开发环境 / Development Environment**：本项目完全在 macOS 环境下开发和测试  
This project was developed and tested entirely in a macOS environment.

**开发背景 / Development Background**：

- 开发者 / Developer：**Nagisa_Huckrick (胡杨)**
- 📧 联系邮箱 / Contact Email：**Nagisa_Huckrick@yeah.net**
- 🐙 GitHub：[@Huckrick](https://github.com/Huckrick)

**重要声明 / Important Statement**：

> ⚠️ **本人并非专业程序员，不具备编程背景。本项目全部代码均由 AI 编程工具（Trae、Cursor 等）辅助生成，本人主要负责产品构思、功能设计和测试验证。**  
> ⚠️ **I am not a professional programmer and have no programming background. All code in this project was generated with the assistance of AI programming tools (Trae, Cursor, etc.). I am primarily responsible for product conception, feature design, and testing verification.**

**灵感来源 / Inspiration**：
本项目的诞生深受 **[OpenClaw](https://github.com/miaoxworld/openclaw-manager)** 的启发。OpenClaw 作为业内领先的 AI 管理工具，展示了 AI 与工具软件结合的无限可能，让我看到了 AI 辅助音频管理的创新方向。感谢 OpenClaw 团队为 AI 应用生态做出的贡献！

This project was inspired by **[OpenClaw](https://github.com/miaoxworld/openclaw-manager)**. As a leading AI management tool, OpenClaw demonstrated the infinite possibilities of combining AI with utility software, showing me the innovative direction of AI-assisted audio management. Thanks to the OpenClaw team for their contributions to the AI application ecosystem!

**致谢 / Acknowledgments**：
特别感谢以下 AI 编程工具对本项目的支持 / Special thanks to the following AI programming tools for supporting this project：

- **[Trae](https://www.trae.ai/)** - 由字节跳动开发的 AI 编程工具 / AI programming tool by ByteDance
- **[Cursor](https://cursor.sh/)** - 基于 VS Code 的 AI 编程编辑器 / AI-powered code editor based on VS Code

***

## ✨ 功能特性 / Features

### 🎯 核心功能 / Core Features

| 功能 / Feature | 描述 / Description |
|---------------|-------------------|
| 🔍 **语义搜索 / Semantic Search** | 输入"雨声"、"爆炸声"等自然语言，AI 理解你的意图 / Type "rain sound", "explosion" in natural language, AI understands your intent |
| 🎵 **音频预览 / Audio Preview** | 支持选区截取并拖拽到 DAW 中使用 / Support selecting regions and dragging to DAW |
| 🤖 **AI 对话 / AI Chat** | 与 AI 讨论音效需求，获取推荐 / Chat with AI about sound needs and get recommendations |
| 📁 **批量导入 / Batch Import** | 支持整个文件夹批量索引 / Support batch indexing of entire folders |
| 🏷️ **智能标签 / Smart Tags** | 自动生成 UCS 标准分类标签 / Auto-generate UCS standard classification tags |

### 🚀 技术亮点 / Technical Highlights

- **🧠 CLAP 模型**：使用 LAION 的 CLAP 模型进行音频-文本嵌入 / Using LAION's CLAP model for audio-text embedding
- **⚡ 高性能**：基于 FastAPI 的异步后端 / FastAPI-based async backend
- **🔒 本地优先**：所有数据本地存储，保护隐私 / All data stored locally, privacy protected
- **🎨 现代化 UI**：基于 Tailwind CSS 的响应式设计 / Modern responsive design with Tailwind CSS
- **📦 一体化**：PyInstaller 打包，无需 Python 环境 / PyInstaller packaging, no Python environment needed

***

## 🛠️ 开发环境搭建 / Development Setup

### 前提条件 / Prerequisites

- Python 3.12+
- Node.js 18+
- Git

### 本地开发 / Local Development

```bash
# 克隆仓库 / Clone repository
git clone https://github.com/Huckrick/SoundBot.git
cd SoundBot

# 安装 Node 依赖 / Install Node dependencies
npm install

# 安装 Python 依赖 / Install Python dependencies
cd backend
pip install -r requirements.txt
cd ..

# 下载 AI 模型 / Download AI models
python scripts/download_models.py

# 启动开发服务器 / Start development server
npm run dev
```

### 构建应用 / Build Application

```bash
# 构建当前平台 / Build for current platform
python scripts/build.py

# 构建 macOS / Build for macOS
python scripts/build.py --platform macos

# 构建 Windows / Build for Windows
python scripts/build.py --platform windows

# 构建所有平台 / Build for all platforms
python scripts/build.py --platform all
```

***

## 📝 使用说明 / Usage Guide

### 1. 导入音效库 / Import Sound Library

1. 点击"选择文件夹"按钮 / Click "Select Folder" button
2. 选择包含音效文件的文件夹 / Select folder containing audio files
3. 等待 AI 索引完成（首次可能需要几分钟）/ Wait for AI indexing to complete (may take a few minutes first time)

### 2. 语义搜索 / Semantic Search

1. 在搜索框输入自然语言描述，如：
   Type natural language descriptions in search box, such as:
   - "雨声" / "rain sound"
   - "爆炸声" / "explosion"
   - "科幻武器" / "sci-fi weapon"
   - "恐怖氛围" / "horror atmosphere"
2. 查看搜索结果，点击播放预览 / View search results, click to play preview
3. 拖拽到 DAW 中使用 / Drag to DAW for use

### 3. AI 对话 / AI Chat

1. 点击右下角的 AI 对话按钮 / Click AI chat button at bottom right
2. 描述你需要的音效，如：
   Describe the sounds you need, such as:
   - "我需要一些适合恐怖游戏的音效" / "I need some sound effects suitable for horror games"
   - "推荐一些科幻风格的 UI 音效" / "Recommend some sci-fi style UI sounds"
3. AI 会推荐相关音效并解释原因 / AI will recommend relevant sounds and explain why

### 4. 音频预览 / Audio Preview

- 点击音效卡片播放预览 / Click sound card to play preview
- 使用波形图选择特定区域 / Use waveform to select specific region
- 拖拽选区到 DAW 时间线 / Drag selection to DAW timeline

***

## ⚠️ 注意事项 / Important Notes

### 存储空间 / Storage Space

- **应用包**：约 300-500MB（已包含后端）
- **App Package**: ~300-500MB (backend included)
- **AI 模型**：约 500MB-1GB（需单独下载）
- **AI Models**: ~500MB-1GB (separate download required)
- **数据库**：根据音效库大小增长
- **Database**: Grows with sound library size
- **建议**：确保至少有 2GB 可用磁盘空间
- **Recommendation**: Ensure at least 2GB free disk space

### 常见问题 / FAQ

**Q: 启动时提示"找不到模型文件"？**
**Q: "Model files not found" error on startup?**

A: 请下载 `models.zip` 并解压到应用安装目录或用户数据目录的 `models/` 文件夹下。  
A: Please download `models.zip` and extract to `models/` folder in app install directory or user data directory.

**Q: 支持哪些音频格式？**
**Q: What audio formats are supported?**

A: 支持 WAV、MP3、FLAC、OGG、M4A 等常见格式。  
A: Supports common formats including WAV, MP3, FLAC, OGG, M4A.

**Q: 可以离线使用吗？**
**Q: Can it be used offline?**

A: 可以！除了 AI 对话功能需要联网外，其他功能（搜索、预览、导入）均可离线使用。  
A: Yes! Except for AI chat which requires internet, other features (search, preview, import) work offline.

**Q: 如何更新到新版本？**
**Q: How to update to new version?**

A: 直接下载新的安装包覆盖安装即可，模型文件和数据会自动保留。  
A: Simply download and install the new package to overwrite. Model files and data will be preserved automatically.

***

## 🤝 贡献指南 / Contributing

欢迎提交 Issue 和 Pull Request！  
Issues and Pull Requests are welcome!

### 提交 Issue / Submit Issue

- 🐛 **Bug 报告 / Bug Report**：描述问题、复现步骤、期望行为 / Describe issue, reproduction steps, expected behavior
- ✨ **功能建议 / Feature Request**：描述功能、使用场景、期望效果 / Describe feature, use case, expected effect
- 💬 **一般讨论 / General Discussion**：使用 GitHub Discussions / Use GitHub Discussions

### 提交 PR / Submit PR

1. Fork 本仓库 / Fork this repository
2. 创建特性分支 / Create feature branch：`git checkout -b feature/AmazingFeature`
3. 提交更改 / Commit changes：`git commit -m 'Add some AmazingFeature'`
4. 推送分支 / Push branch：`git push origin feature/AmazingFeature`
5. 创建 Pull Request / Create Pull Request

***

## 📄 许可证 / License

本项目采用 / This project is licensed under [GNU General Public License v3.0](LICENSE)

```
Copyright (C) 2026 Nagisa_Huckrick (胡杨)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
```

***

## 🙏 致谢 / Acknowledgments

- [LAION](https://laion.ai/) - 提供 CLAP 预训练模型 / Providing CLAP pre-trained model
- [ChromaDB](https://www.trychroma.com/) - 向量数据库 / Vector database
- [FastAPI](https://fastapi.tiangolo.com/) - 高性能 Web 框架 / High-performance web framework
- [Electron](https://www.electronjs.org/) - 跨平台桌面应用框架 / Cross-platform desktop framework
- [PyInstaller](https://pyinstaller.org/) - Python 应用打包工具 / Python application packager
- [WaveSurfer.js](https://wavesurfer-js.org/) - 音频波形可视化 / Audio waveform visualization
- [Trae](https://www.trae.ai/) & [Cursor](https://cursor.sh/) - AI 编程工具 / AI programming tools

***

## 📞 联系我们 / Contact Us

- 📧 邮箱 / Email：**Nagisa_Huckrick@yeah.net**
- 🐛 Issue：[GitHub Issues](https://github.com/Huckrick/SoundBot/issues)
- 💬 Discussions：[GitHub Discussions](https://github.com/Huckrick/SoundBot/discussions)

***

<p align="center">
  Made with ❤️ by Nagisa_Huckrick (胡杨) using AI tools<br>
  使用 AI 工具由 Nagisa_Huckrick (胡杨) 制作
</p>
