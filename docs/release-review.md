# SoundBot release review / 发行审查

## User permissions and data / 用户权限与数据

| Boundary | Expected behavior |
| --- | --- |
| Installation / 安装 | Windows uses the current-user installer with elevation disabled. macOS uses a disk image. Choose an applications folder writable by your account. / Windows 使用当前用户安装，关闭提权选项。macOS 从镜像拖出应用，选择当前用户可写的位置。 |
| File access / 文件访问 | Read selected files and source directories. Write only to app data or an explicit delivery target. A macOS Files and Folders prompt can apply to a protected folder the user selects. / 读取用户选中的文件和来源目录。写入应用数据或用户明确选择的交付位置。选中受保护目录时，macOS 可能提示授权该目录。 |
| System credentials / 系统凭据 | Only explicit assistant actions can access the OS credential store. SoundBot asks for a service API key in the assistant settings. System passwords belong only in verified system dialogs. / 只有主动操作助手时才访问系统凭据存储。应用只在助手设置中接收服务 API 密钥。系统密码只应输入经过核实的系统对话框。 |
| Native code / 本机代码 | Loading a user-selected VST3 requires confirmation. That code runs with the user's rights. The macOS build keeps JIT and the library-validation exception used by the native plug-in host. / 加载用户选择的 VST3 前需要确认。插件以当前用户权限运行。macOS 构建保留 JIT 和本机插件宿主所需的动态库校验例外。 |
| Network / 网络 | The desktop API listens on loopback and protects data routes with a per-launch credential. Core search and playback use local data. Only a user-configured assistant makes a service request when used. / 桌面 API 监听本机回环地址，数据接口由本次启动凭据保护。基础搜索和播放使用本机数据。使用已配置的助手时才向对应服务发请求。 |

Default data directories are `~/Library/Application Support/SoundBot` on macOS and `%APPDATA%\SoundBot` on Windows. Back up the library database and its backups before changing versions. Original audio, delivered DAW media, and Wwise source backups should be kept according to their project requirements.

macOS 默认数据目录为 `~/Library/Application Support/SoundBot`，Windows 为 `%APPDATA%\SoundBot`。更换版本前备份音效库数据库及其备份。音频原件、交付给 DAW 的媒体和 Wwise 源文件备份应按各自工程的要求留存。

## Advisory triage / 依赖告警

The v1.0.1 runtime lock updates AnyIO, certifi, IDNA, and Starlette. ChromaDB and the legacy model stack are absent from the runtime lock and frozen package. Old alerts should be reassessed against the current default branch as GitHub refreshes its dependency graph. The npm audit checks the full build lock, including the packaging tools. Its remaining moderate alerts come from the build-time minimatch dependency chain. Installer inputs are the reviewed source and fixed build configuration.

v1.0.1 的运行时锁更新 AnyIO、certifi、IDNA 和 Starlette。旧模型和 ChromaDB 不在运行时锁或冻结安装包中。GitHub 刷新依赖图后，按当前默认分支复核历史告警。npm 审计覆盖完整构建锁。剩余中危告警来自构建时的 minimatch 依赖链，安装包使用已审查的源码及固定构建配置。

CodeQL also reports paths used for imports, delivery, and VST3 loading. Review those separately from dependency advisories. Check that each file operation is bound to the selected source, delivery target, or managed record before marking a finding resolved. / CodeQL 也标记了导入、交付和加载 VST3 的文件路径。与依赖告警分开复核。逐项核对文件操作是否绑定用户选定的来源、交付目标或受管理记录，再确认是否解决。 [Code scanning / 代码扫描](https://github.com/Huckrick/SoundBot/security/code-scanning)

## Distribution evidence / 分发证据

- **Tagged source / 对应源码。** Check that the version, annotated tag, build run, and generated release notes all identify the same commit. Link the source archive and build instructions next to the installer downloads. / 核对版本号、附注标签、构建记录和发行说明均对应同一提交。在安装包下载区同时提供源码归档和构建说明。
- **Third-party inventory / 第三方清单。** The installers carry SoundBot's GPL license, Electron and Chromium notices, the audio notices and FFmpeg license texts. The frozen backend carries the platform's Python wheel metadata and accompanying licenses. PyAV 18.0.0 uses FFmpeg 8.1.2 from the pinned wheel recipe. Confirm the exact native libraries, auxiliary codec notices, and corresponding source availability on each platform before public binary distribution. / 安装包附带 SoundBot 的 GPL 许可证、Electron 和 Chromium 声明、音频声明与 FFmpeg 许可证。冻结后端附带目标平台的 Python wheel 元数据和随附许可证。PyAV 18.0.0 使用固定配方中的 FFmpeg 8.1.2。公开分发前逐平台核对实际原生库、附属编解码器声明及对应源码的可取得性。 [Audio notices / 音频声明](../tests/build/licenses/THIRD_PARTY_AUDIO_NOTICES.txt) · [FFmpeg distribution guidance](https://ffmpeg.org/legal.html)
- **Authenticity / 来源与签名。** Compare each installer against `SHA256SUMS.txt` and the GitHub artifact attestation. Record Developer ID and notarization status on macOS and Authenticode status on Windows. The standard CI build currently makes review installers without publisher signing credentials. / 比对安装包的 SHA-256 与 GitHub 产物证明。记录 macOS 的 Developer ID 签名及公证状态，以及 Windows 的 Authenticode 签名状态。标准 CI 当前生成待审安装包，未配置发布者签名凭据。
- **Installation and privacy / 安装与隐私。** Test as a standard account on a clean machine. Confirm startup, migration, import, search, playback, and DAW or host delivery where claimed. Review the committed files and package contents for personal paths, credentials, test media, caches, and databases. / 在干净系统以标准账户验证安装。核对启动、迁移、导入、搜索、试听和发行说明中声称已验证的 DAW 或宿主交付。检查提交和包内容中的私人路径、凭据、测试素材、缓存及数据库。
- **Approval / 发布确认。** The tag workflow creates a draft. Enter the observed platform results, remaining advisory and signing status, and source links in the draft before the maintainer publishes it. / 标签工作流创建草稿。维护者发布前，在草稿中填写两平台实测结果、剩余告警及签名状态，并补齐源码链接。

[构建与发布 / Build and release](build-and-retrieval.md)
