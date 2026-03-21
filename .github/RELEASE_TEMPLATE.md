# SoundBot v0.1.0-alpha Release Notes

## 🎉 首次发布 / First Release

**SoundBot** - AI-powered sound effect manager with semantic search

**SoundBot** - AI 驱动的音效管理器，支持语义搜索

---

## 📥 下载 / Download

### macOS
- **Apple Silicon (M1/M2/M3)**: `SoundBot-0.1.0-alpha-arm64.dmg`
- **Intel Mac**: `SoundBot-0.1.0-alpha-x64.dmg`

> 下载后双击 DMG 文件，将 SoundBot 拖到 Applications 文件夹
> After downloading, double-click the DMG and drag SoundBot to Applications

### Windows
- **64-bit**: `SoundBot Setup 0.1.0-alpha.exe`

> 下载后运行安装程序，按提示完成安装
> Run the installer and follow the prompts

### Linux
- **AppImage**: `SoundBot-0.1.0-alpha.AppImage`

> 下载后赋予执行权限并运行
> Download, make executable with `chmod +x`, and run

---

## ✨ 主要功能 / Key Features

### 🎯 语义搜索 / Semantic Search
- **自然语言搜索** - 用描述性词语搜索音效（如"雨声"、"爆炸"、"科幻武器"）
- **Natural Language Search** - Search sounds using descriptive words (e.g., "rain", "explosion", "sci-fi weapon")

### 🤖 AI 驱动 / AI Powered
- **CLAP 模型** - 使用先进的音频-文本嵌入模型理解音频内容
- **CLAP Model** - Advanced audio-text embedding model for understanding audio content

### 🎵 音频播放 / Audio Playback
- **波形可视化** - 实时显示音频波形
- **Waveform Visualization** - Real-time audio waveform display
- **区域选择** - 选择并循环播放特定片段
- **Region Selection** - Select and loop specific segments

### 📁 项目管理 / Project Management
- **多项目管理** - 创建和管理多个音效库项目
- **Multi-Project Management** - Create and manage multiple sound libraries

### 🤖 AI 对话助手 / AI Chat Assistant
- **多 LLM 支持** - 支持 OpenAI、Azure、Gemini、Kimi、Claude、DeepSeek、SiliconFlow 等
- **Multi-LLM Support** - Support OpenAI, Azure, Gemini, Kimi, Claude, DeepSeek, SiliconFlow, etc.
- **智能音效推荐** - 根据场景描述推荐合适的音效
- **Smart Sound Recommendation** - Recommend suitable sounds based on scene descriptions
- **音效知识问答** - 解答音效制作、UCS 分类等问题
- **Sound Knowledge Q&A** - Answer sound production, UCS categorization questions

### 🔧 高级功能 / Advanced Features
- **音频处理** - 淡入淡出、片段导出
- **Audio Processing** - Fade in/out, clip export
- **LRU 缓存** - 智能缓存加速重复播放
- **LRU Cache** - Smart caching for faster replay
- **批量导入** - 支持文件夹批量导入和实时监控
- **Batch Import** - Folder import with real-time monitoring

---

## 🚀 快速开始 / Quick Start

### 1. 安装 / Install
下载对应系统的安装包并安装  
Download and install the package for your system

### 2. 首次启动 / First Launch
应用会自动启动后端服务和加载 AI 模型（约需 10-30 秒）  
The app will auto-start backend and load AI model (takes 10-30 seconds)

### 3. 配置 AI 助手（可选）/ Configure AI Assistant (Optional)
点击"设置" → "AI 配置"添加你的 LLM API 密钥  
Click "Settings" → "AI Config" to add your LLM API key

**支持的 LLM 服务 / Supported LLM Services:**
- OpenAI (GPT-4, GPT-3.5)
- Azure OpenAI
- Google Gemini
- Moonshot Kimi
- Anthropic Claude
- DeepSeek
- SiliconFlow
- 自定义 API / Custom API

### 4. 导入音效 / Import Sounds
点击"导入" → "导入文件夹"选择你的音效文件夹  
Click "Import" → "Import Folder" to select your sound folder

### 5. 开始搜索 / Start Searching
在搜索框输入描述（如"雨声"、"科幻"）查找音效  
Type descriptions (e.g., "rain", "sci-fi") in the search box

### 6. 使用 AI 助手 / Use AI Assistant
点击右下角的 AI 助手图标，描述你需要的音效场景  
Click the AI assistant icon at bottom right, describe your sound scene

---

## 🛠️ 系统要求 / System Requirements

### macOS
- macOS 11.0 (Big Sur) 或更高版本
- Apple Silicon 或 Intel 处理器
- 4GB+ RAM（推荐 8GB）
- 2GB 可用磁盘空间

### Windows
- Windows 10/11 64-bit
- 4GB+ RAM（推荐 8GB）
- 2GB 可用磁盘空间

### Linux
- Ubuntu 20.04+ / Debian 11+ / Fedora 35+
- 4GB+ RAM（推荐 8GB）
- 2GB 可用磁盘空间

---

## 📝 已知问题 / Known Issues

- 首次启动需要加载 AI 模型，可能需要 10-30 秒
- First launch requires loading AI model, may take 10-30 seconds
- 大型音效库导入可能需要较长时间
- Large sound libraries may take time to import

---

## 🔒 许可证 / License

本项目采用 **GNU General Public License v3.0 (GPL-3.0)**  
This project is licensed under **GNU General Public License v3.0 (GPL-3.0)**

- ✅ 自由使用、修改、分发
- ✅ Free to use, modify, distribute
- ⚠️ 修改后的版本必须开源
- ⚠️ Modified versions must be open source
- ❌ 不能闭源销售
- ❌ Cannot be sold as closed-source

详见 [LICENSE](../LICENSE) 文件  
See [LICENSE](../LICENSE) file for details

---

## 👨‍💻 开发者 / Developer

**Nagisa_Huckrick (胡杨)**  
📧 Email: Nagisa_Huckrick@yeah.net  
🐙 GitHub: [@Nagisa_Huckrick](https://github.com/Nagisa_Huckrick)

### 致谢 / Acknowledgments
- 本项目开发过程中使用了 Trae 和 Cursor 等 AI 编程工具
- This project was developed using AI programming tools including Trae and Cursor

---

## 🤝 贡献 / Contributing

欢迎提交 Issue 和 Pull Request！  
Issues and Pull Requests are welcome!

1. Fork 本仓库 / Fork this repository
2. 创建特性分支 / Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. 提交更改 / Commit changes (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 / Push to branch (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request / Open a Pull Request

---

## 📊 版本历史 / Version History

### v0.1.0-alpha (2026-03-22)
- 🎉 首次公开发布
- 🎉 First public release
- ✨ 完整的语义搜索功能
- ✨ Full semantic search functionality
- ✨ AI 对话助手集成
- ✨ AI chat assistant integration
- ✨ 多项目管理支持
- ✨ Multi-project management support
- ✨ 音频波形可视化
- ✨ Audio waveform visualization

---

## 💬 反馈 / Feedback

遇到问题？请提交 Issue：  
Having issues? Please submit an Issue:

👉 [GitHub Issues](https://github.com/Nagisa_Huckrick/SoundBot/issues)

---

**感谢使用 SoundBot！**  
**Thank you for using SoundBot!**

🎵 让音效管理更智能 / Make sound management smarter 🎵
