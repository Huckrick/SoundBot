# SoundBot 致谢与许可 / Credits and licenses

## 作者 / Authorship

SoundBot 由 **Huckrick** 发起、开发和维护。© 2026 Huckrick 与 SoundBot 贡献者。源码采用 GPL-3.0-or-later 许可。 [项目源码](https://github.com/Huckrick/SoundBot) · [许可证](https://github.com/Huckrick/SoundBot/blob/main/LICENSE)

**Huckrick** created, develops, and maintains SoundBot. © 2026 Huckrick and SoundBot contributors. The source code is licensed under GPL-3.0-or-later. [Source repository](https://github.com/Huckrick/SoundBot) · [License](https://github.com/Huckrick/SoundBot/blob/main/LICENSE)

## MiSans

简体中文主界面优先使用系统已安装的 **MiSans VF** 或 **MiSans**。MiSans 由小米提供。MiSans Global 由小米联合 Monotype 和汉仪开发。安装包不附带字体文件，运行时也不从网络获取字体。未安装 MiSans 时依次使用苹方、微软雅黑或系统默认字体。MiSans 的使用遵循小米发布的字体知识产权许可协议。 [官方介绍](https://hyperos.mi.com/font/en/about/) · [官方许可协议](https://hyperos.mi.com/font-download/MiSans%E5%AD%97%E4%BD%93%E7%9F%A5%E8%AF%86%E4%BA%A7%E8%AE%B8%E5%8F%AF%E5%8D%8F%E8%AE%AE.pdf)

The Simplified Chinese workspace prefers **MiSans VF** or **MiSans** if installed on your system. MiSans is provided by Xiaomi. Xiaomi developed MiSans Global with Monotype and Hanyi. The installer ships without these fonts, and the app uses only local fonts. When MiSans is unavailable, it falls back to PingFang SC, Microsoft YaHei, or the system default. Xiaomi's MiSans Font Intellectual Property License Agreement governs the font. [Official information](https://hyperos.mi.com/font/en/about/) · [Official license agreement](https://hyperos.mi.com/font-download/MiSans%E5%AD%97%E4%BD%93%E7%9F%A5%E8%AF%86%E4%BA%A7%E8%AE%B8%E5%8F%AF%E5%8D%8F%E8%AE%AE.pdf)

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
