# 🎵 SoundBot - AI 音效管理器 / AI Sound Effect Manager

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Version](https://img.shields.io/badge/version-0.1.1--alpha-orange.svg)](https://github.com/Huckrick/SoundBot)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Electron](https://img.shields.io/badge/electron-28.x-9feaf9.svg)](https://www.electronjs.org/)

> 用自然语言找到你想要的任何声音 - AI 驱动的智能音效管理器桌面版  
> Find any sound you want using natural language - AI-powered intelligent sound effect manager for desktop

***

## 📥 下载 / Download

**最新版本 / Latest Release**: [v0.1.1-alpha](https://github.com/Huckrick/SoundBot/releases/tag/v0.1.1-alpha)

### 🆕 新架构：分离式发布 / New Architecture: Split Release

从 v0.1.1-alpha 开始，SoundBot 采用**分离式架构**，将应用包、AI 模型和 Python 环境分开发布：

Starting from v0.1.1-alpha, SoundBot adopts a **split architecture**, separating the app package, AI models, and Python environment:

### 应用包 / App Packages

| 平台 / Platform | 下载链接 / Download | 大小 / Size |
|----------------|--------------------|-------------|
| **macOS (Intel)** | [📥 SoundBot-0.1.1-alpha.dmg](https://github.com/Huckrick/SoundBot/releases/download/v0.1.1-alpha/SoundBot-0.1.1-alpha.dmg) | ~100MB |
| **macOS (Apple Silicon)** | [📥 SoundBot-0.1.1-alpha-arm64.dmg](https://github.com/Huckrick/SoundBot/releases/download/v0.1.1-alpha/SoundBot-0.1.1-alpha-arm64.dmg) | ~100MB |
| **Windows** | [📥 SoundBot-Setup-0.1.1-alpha.exe](https://github.com/Huckrick/SoundBot/releases/download/v0.1.1-alpha/SoundBot-Setup-0.1.1-alpha.exe) | ~100MB |

### 资源文件 / Resources

> ⚠️ **首次安装必须下载 / Required for first-time installation**

| 资源 / Resource | 下载链接 / Download | 大小 / Size | 说明 / Description |
|----------------|--------------------|-------------|-------------------|
| **AI 模型 / Models** | [📦 models.zip](https://github.com/Huckrick/SoundBot/releases/download/v0.1.1-alpha/models.zip) | ~500MB-1GB | **所有平台通用 / All platforms** |
| **macOS 环境** | [📦 venv-macos.zip](https://github.com/Huckrick/SoundBot/releases/download/v0.1.1-alpha/venv-macos.zip) | ~1-2GB | macOS Python 虚拟环境 |
| **Windows 环境** | [📦 venv-windows.zip](https://github.com/Huckrick/SoundBot/releases/download/v0.1.1-alpha/venv-windows.zip) | ~1-2GB | Windows Python 虚拟环境 |

### 界面预览 / Screenshot

![SoundBot Home1](https://raw.githubusercontent.com/Huckrick/SoundBot/main/Home1.png)  
![SoundBot Home2](https://raw.githubusercontent.com/Huckrick/SoundBot/main/Home2.png)

***

## 🚀 安装指南 / Installation Guide

### 首次安装 / First-time Installation

#### macOS

1. **下载应用包** / Download app package
   ```bash
   # 下载 SoundBot-x.x.x.dmg（根据你的架构选择 Intel 或 Apple Silicon 版本）
   ```

2. **安装应用** / Install app
   - 打开 `.dmg` 文件，将 SoundBot 拖到 Applications 文件夹

3. **下载资源文件** / Download resources
   - 下载 `models.zip`（所有平台通用）
   - 下载 `venv-macos.zip`

4. **解压资源文件** / Extract resources
   ```bash
   # 方法 1：解压到应用包内（推荐）
   cd /Applications/SoundBot.app/Contents/MacOS
   unzip ~/Downloads/models.zip
   unzip ~/Downloads/venv-macos.zip
   
   # 方法 2：解压到应用同级目录（便于管理）
   cd /Applications
   unzip ~/Downloads/models.zip
   unzip ~/Downloads/venv-macos.zip
   ```

5. **启动应用** / Launch
   - 双击 SoundBot 图标启动
   - 如果提示缺少资源，应用会自动检测并引导你下载

#### Windows

1. **下载应用包** / Download app package
   - 下载 `SoundBot Setup x.x.x.exe`

2. **安装应用** / Install app
   - 运行 `.exe` 安装程序，按向导完成安装
   - 默认安装路径：`C:\Users\<用户名>\AppData\Local\Programs\SoundBot`

3. **下载资源文件** / Download resources
   - 下载 `models.zip`（所有平台通用）
   - 下载 `venv-windows.zip`

4. **解压资源文件** / Extract resources
   - 将 `models.zip` 解压到 SoundBot 安装目录，得到 `models` 文件夹
   - 将 `venv-windows.zip` 解压到 SoundBot 安装目录，得到 `backend\venv` 文件夹
   - 最终目录结构：
     ```
     SoundBot/
     ├── SoundBot.exe
     ├── backend/
     │   ├── main.py
     │   ├── core/
     │   └── venv/          # 从 venv-windows.zip 解压
     ├── models/            # 从 models.zip 解压
     │   └── clap/
     └── ...
     ```

5. **启动应用** / Launch
   - 双击 SoundBot 图标启动

### 更新版本 / Update Version

**应用更新** / App Update：
- 只需下载并安装新的应用包（~100MB）
- 模型和 Python 环境**不需要**重新下载

**模型更新** / Model Update：
- 如果新版本需要更新模型，会单独发布新的 `models.zip`
- 下载后覆盖解压即可

**环境更新** / Environment Update：
- 如果依赖有变化，会单独发布新的 `venv-*.zip`
- 下载后覆盖解压即可

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

# 方式 1：自动下载资源 / Method 1: Auto-download resources
npm run setup

# 方式 2：手动设置 / Method 2: Manual setup
cd backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cd ..
python scripts/download_models.py

# 启动开发服务器 / Start development server
npm run dev
```

### 开发命令 / Development Commands

```bash
# 检查资源状态
python scripts/download_manager.py check

# 下载模型
npm run download:models

# 下载当前平台的 venv
npm run download:venv

# 下载所有资源
npm run download:all

# 自动设置环境
npm run setup
```

***

## 📝 使用说明 / Usage Guide

### 1. 导入音效库 / Import Sound Library

1. 点击"选择文件夹"按钮
2. 选择包含音效文件的文件夹
3. 等待 AI 索引完成（首次可能需要几分钟）

### 2. 语义搜索 / Semantic Search

1. 在搜索框输入自然语言描述，如：
   - "雨声" / "rain sound"
   - "爆炸声" / "explosion"
   - "科幻武器" / "sci-fi weapon"
   - "恐怖氛围" / "horror atmosphere"
2. 查看搜索结果，点击播放预览
3. 拖拽到 DAW 中使用

### 3. AI 对话 / AI Chat

1. 点击右下角的 AI 对话按钮
2. 描述你需要的音效，如：
   - "我需要一些适合恐怖游戏的音效"
   - "推荐一些科幻风格的 UI 音效"
3. AI 会推荐相关音效并解释原因

### 4. 音频预览 / Audio Preview

- 点击音效卡片播放预览
- 使用波形图选择特定区域
- 拖拽选区到 DAW 时间线

***

## ⚠️ 注意事项 / Important Notes

### 存储空间 / Storage Space

- **应用包**：约 100MB
- **AI 模型**：约 500MB-1GB
- **Python 环境**：约 1-2GB
- **数据库**：根据音效库大小增长
- **建议**：确保有足够的磁盘空间

### 资源下载配置 / Resource Download Config

如果需要从其他仓库下载资源，可以创建 `download_config.json`：

```json
{
  "github_repo": "your-username/SoundBot"
}
```

### 清理建议 / Cleanup Recommendations

- 清理前请确保不影响 DAW 工程中的引用
- 建议在 DAW 中先备份工程
- 可以使用"重置数据库"功能重新开始

***

## 🤝 贡献指南 / Contributing

欢迎提交 Issue 和 Pull Request！

### 提交 Issue / Submit Issue

- 🐛 **Bug 报告**：描述问题、复现步骤、期望行为
- ✨ **功能建议**：描述功能、使用场景、期望效果
- 💬 **一般讨论**：使用 GitHub Discussions

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
