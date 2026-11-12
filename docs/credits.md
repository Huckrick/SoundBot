# SoundBot 致谢与许可 / Credits and licenses

## 作者 / Authorship

SoundBot 由 **Huckrick** 发起、开发和维护。© 2026 Huckrick 与 SoundBot 贡献者。源码采用 GPL-3.0-or-later 许可。 [项目源码](https://github.com/Huckrick/SoundBot) · [许可证](https://github.com/Huckrick/SoundBot/blob/main/LICENSE)

**Huckrick** created, develops, and maintains SoundBot. © 2026 Huckrick and SoundBot contributors. The source code is licensed under GPL-3.0-or-later. [Source repository](https://github.com/Huckrick/SoundBot) · [License](https://github.com/Huckrick/SoundBot/blob/main/LICENSE)

## MiSans

SoundBot 的启动窗口和主界面使用原版 **MiSans Global** 可变字体。简体中文与日语使用 MiSans，繁体中文使用 MiSans TC，英语、巴西葡萄牙语与西班牙语使用 MiSans Latin。三套字体嵌入应用并离线加载。素材名称中的罕见字形由系统补齐。 [官方介绍](https://hyperos.mi.com/font/en/about/)

The splash screen and workspace use the original **MiSans Global** variable fonts. SoundBot uses MiSans for Simplified Chinese and Japanese, MiSans TC for Traditional Chinese, and MiSans Latin for English, Brazilian Portuguese, and Spanish. The three fonts are embedded in the application and load offline. The system supplies rare glyphs that may occur in asset names. [Official information](https://hyperos.mi.com/font/en/about/)

MiSans Global 由小米主导，联合 Monotype 和汉仪制作。字体保留原版权声明，适用小米的 MiSans 字体知识产权许可协议，许可原文随安装包提供。小米允许免费商用及在软件中嵌入，并对修改和单独分发设有限制。SoundBot 源码的 GPL 许可与字体许可各自适用。 [官方许可协议](https://hyperos.mi.com/font/en/download/) · [官方常见问题](https://hyperos.mi.com/font/faq)

Xiaomi led MiSans Global and produced it with Monotype and Hanyi. The embedded fonts retain their original copyright notices and are governed by Xiaomi’s MiSans Font Intellectual Property License Agreement, which ships with the app. Xiaomi permits free commercial use and embedding in software under terms that restrict modification and standalone distribution. SoundBot’s GPL license covers its source code. [Official license agreement](https://hyperos.mi.com/font/en/download/) · [Official FAQ](https://hyperos.mi.com/font/en/faq/)

## 组件与引用 / Components and references

| 组件 / Component | 用途 / Role | 来源 / Source |
| --- | --- | --- |
| Electron, Chromium, Node.js | 桌面窗口、界面渲染及运行环境 / Desktop windows, rendering and runtime | [Electron](https://github.com/electron/electron) · [Chromium](https://www.chromium.org/) · [Node.js](https://github.com/nodejs/node) |
| Python, FastAPI, Uvicorn | 本机后端运行时与 API / Local backend runtime and API | [Python](https://www.python.org/) · [FastAPI](https://github.com/fastapi/fastapi) · [Uvicorn](https://github.com/encode/uvicorn) |
| Pydantic, HTTPX, websockets | 后端数据校验、HTTP 和 WebSocket 通信 / Validation and HTTP and WebSocket communication | [Pydantic](https://github.com/pydantic/pydantic) · [HTTPX](https://github.com/encode/httpx) · [websockets](https://github.com/python-websockets/websockets) |
| SQLite FTS5, jieba | 本地关键词索引和中文分词 / Local full-text indexing and Chinese word segmentation | [SQLite FTS5](https://www.sqlite.org/fts5.html) · [jieba](https://github.com/fxsjy/jieba) |
| PyAV, FFmpeg, NumPy | 音频解码、渲染、分析与信号处理 / Audio decoding, rendering, analysis and signal processing | [PyAV](https://github.com/PyAV-Org/PyAV) · [FFmpeg](https://ffmpeg.org/legal.html) · [NumPy](https://github.com/numpy/numpy) |
| Mutagen, Pillow | 音频标签和嵌入封面 / Audio tags and embedded artwork | [Mutagen](https://github.com/quodlibet/mutagen) · [Pillow](https://github.com/python-pillow/Pillow) |
| Pedalboard | 加载和处理用户选择的 VST3 效果器 / Hosting user-selected VST3 effects | [Pedalboard](https://github.com/spotify/pedalboard) |
| libebur128 | 实时响度表改编了 K 加权滤波系数计算 / K-weighting filter coefficient calculation adapted for live loudness metering | [Source](https://github.com/jiixyj/libebur128) · [MIT license, © 2011 Jan Kokemüller](https://github.com/jiixyj/libebur128/blob/master/COPYING) |
| Universal Category System (UCS) | 音效分类字段参考 / Reference for sound effect category fields | [Official UCS reference](https://universalcategorysystem.com/) |

发行包在资源目录的 `licenses` 文件夹内提供本页、SoundBot 许可证、音频组件通知及 Electron、Chromium、FFmpeg 的许可文件。后端的上游许可随对应的依赖发行元数据提供。FFmpeg 的许可取决于各平台打包的二进制及其构建选项。用户安装的 VST3 插件遵循各自许可。 [音频组件许可与对应源码](https://github.com/Huckrick/SoundBot/blob/main/tests/build/licenses/THIRD_PARTY_AUDIO_NOTICES.txt) · [后端依赖锁定表](https://github.com/Huckrick/SoundBot/blob/main/backend/requirements.txt)

The `licenses` folder in the installed resources contains this document, the SoundBot license, audio component notices, and license files for Electron, Chromium, and FFmpeg. Backend dependencies carry their upstream notices in their distribution metadata. FFmpeg terms depend on the packaged platform binaries and build options. User-installed VST3 plug-ins have their own licenses. [Audio notices and corresponding source](https://github.com/Huckrick/SoundBot/blob/main/tests/build/licenses/THIRD_PARTY_AUDIO_NOTICES.txt) · [Backend dependency lock](https://github.com/Huckrick/SoundBot/blob/main/backend/requirements.txt)

## 中间件 / Middleware

工作台参考 Wwise WAAPI、FMOD Studio 和 CRI Atom Craft 的官方文档。Wwise 使用 WAAPI 连接当前工程，FMOD Studio 和 CRI Atom Craft 通过素材目录交付。用户自行安装宿主，产品名称和商标归各权利人所有。 [工作台支持范围](https://github.com/Huckrick/SoundBot/blob/main/docs/middleware.md)

The workspace uses official documentation for Wwise WAAPI, FMOD Studio, and CRI Atom Craft. Wwise connects to the current project through WAAPI. FMOD Studio and CRI Atom Craft receive files through their asset folders. Users install the host applications separately. Product names and trademarks belong to their respective owners. [Wwise Authoring API](https://www.audiokinetic.com/en/library/edge/?source=SDK&id=waapi.html) · [FMOD Studio asset management](https://www.fmod.com/docs/2.04/studio/managing-assets.html) · [CRI Atom Craft materials](https://game.criware.jp/manual/adx2_tool_en/latest/criatom_tools_atomcraft_material_create.html)

## 社区 / Community

感谢参与测试和反馈的用户，也感谢维护上游组件、标准与文档的开发者。感谢 DiamondsF 提供启动和导入问题的反馈，感谢 glitch-vova 提供 FLAC 音效显示空白的复现和调查。 [Issue #4](https://github.com/Huckrick/SoundBot/issues/4) · [Issue #5](https://github.com/Huckrick/SoundBot/issues/5)

Thanks to the users who tested SoundBot and shared feedback, and to the developers maintaining its upstream components, standards, and documentation. Thanks to DiamondsF for reporting startup and import problems, and to glitch-vova for reproducing and investigating the blank FLAC waveform issue. [Issue #4](https://github.com/Huckrick/SoundBot/issues/4) · [Issue #5](https://github.com/Huckrick/SoundBot/issues/5)
