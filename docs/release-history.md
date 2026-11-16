# Historical releases / 历史发行 / 過去のリリース

SoundBot’s previous release notes, source tags, and asset checksums. The download page shows the current installer. / 此处保存以往发行说明、源码标签和产物校验值。当前安装包以最新稳定版下载页为准。 / 旧版のリリースノートとソースタグ、配布ファイルのチェックサムを記録しています。

[最新稳定版 / Latest stable release / 最新安定版](https://github.com/Huckrick/SoundBot/releases/latest) · [变更记录 / Changelog](../CHANGELOG.md)

Asset names are listed for identification and verification of copies already downloaded. / 产物信息供辨认和校验已下载的历史文件。

## v1.0.10

Originally published 2026-11-16 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.10) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/fa337dedce4dba4f636fdd87deea5ed108bed4c6)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 184 | `bd31add3fe900539fe3403ee9322195266c2adf77333d06eeec256cff79929e6` |
| `SoundBot-1.0.10-arm64.dmg` | 209996046 | `4b84e0477a31850994755e681c41234137081ceef56b7627073848c70c2444d5` |
| `SoundBot-Setup-1.0.10.exe` | 210942819 | `a298533fc2e9413513635b1e177a1e2aff53bec38d493e707f23708d80d292f2` |

<details>
<summary>Original release notes / 原发行说明</summary>

## [1.0.10] - 2026-11-16

### Added / 新增

- Open the folder context menu to remove cataloged sounds from subfolders and folders made up of individually imported files. Existing audio files stay on disk. / 子目录和由单独导入素材组成的目录均可通过右键菜单从当前音效库移除，原音频保留在磁盘。
- Fix WAV delivery on Windows by syncing the rendered file through a writable handle. The waveform editor also offers a native drag control for whole sounds and selections. / 使用可写句柄同步成品文件，修复 Windows 的 WAV 交付。波形编辑器增加整段和选区的原生拖出入口。
- Point the repository's main download link to the latest stable release. / 将仓库首页的下载入口指向最新稳定发行。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 标准 CI 产物供维护者验收，未配置发布者签名凭据。 / Standard CI produces review installers without publisher signing credentials.
- 源码提交 / Source commit: fa337dedce4dba4f636fdd87deea5ed108bed4c6. 构建流程提交 / Workflow commit: fa337dedce4dba4f636fdd87deea5ed108bed4c6.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.10) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43825871981) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.10/docs/release-review.md)

</details>

## v1.0.9

Originally published 2026-11-15 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.9) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/df555011a0fbb743a6b1b163d826e805d2ed9786)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 182 | `83c8eb2977b224b7a290bf5a68c41d9e5d137d351391c2e821aeed74a5fa9f9e` |
| `SoundBot-1.0.9-arm64.dmg` | 209994275 | `36ea75104c8a4519cb2cfb328113b141a3f5946616dd1af3145c18ea4b9827a7` |
| `SoundBot-Setup-1.0.9.exe` | 210941926 | `f1f3eb50c48a639632e2d2f4cf299248ec0c9125534b9bbb6e9ca9d968804126` |

<details>
<summary>Original release notes / 原发行说明</summary>

## [1.0.9] - 2026-11-15

### Added / 新增

- Browse filename categories such as footsteps, doors, and weather in Tags and the middleware library. Classification runs locally on existing and newly imported sounds and preserves manual tags and UCS metadata. / 在标签页和工作台音效库按文件名筛选脚步、门、天气等类别。现有和新导入的素材均在本机分类，保留手动标签与 UCS 元数据。
- Reorder, sort, and collapse tag sections. Create folders for custom tags and move tags between folders. Deleting a folder returns its tags to the main tag list. / 标签页支持调整分类顺序、分类内排序及折叠。可新建标签夹和移动标签，删除标签夹后标签回到主列表。

### Fixed / 修复

- Drag whole sounds and edited selections directly into the timeline. DAWs receive a prepared WAV from the chosen media directory. Returning an outgoing drag to SoundBot cancels it. / 整段音效或裁剪选区可直接拖入时间线。拖到 DAW 时从所选媒体目录交付已准备的 WAV。拖出后放回 SoundBot 会取消当次拖放。
- Keep the library filters synchronized across views. The middleware library uses one toolbar with filters on the left, search in the center, and expand and collapse controls on the right. Use the mouse wheel to browse the sound cards horizontally. / 主库和工作台音效库同步筛选。工作台底部工具栏保持一行，筛选靠左、搜索居中、展开和收起按钮靠右。可用鼠标滚轮左右浏览素材卡片。
- Reuse in-flight waveform decoding and bound thumbnail work to the visible area. / 合并正在进行的同源波形解码，缩略图任务限定在可见区域附近。
- Decode fallback playback in blocks to keep memory usage bounded for long sounds. / 需要转码试听时分块解码，控制长音频的内存占用。

### Upgrade / 升级

- The library upgrades to database schema v7 and saves a pre-upgrade snapshot. Keep this snapshot if you need to open the library in an earlier release. / 音效库会升级到数据库架构 v7，并在升级前保存快照。若要使用旧版 SoundBot 打开音效库，请保留并恢复该快照。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 标准 CI 产物供维护者验收，未配置发布者签名凭据。 / Standard CI produces review installers without publisher signing credentials.
- 源码提交 / Source commit: df555011a0fbb743a6b1b163d826e805d2ed9786. 构建流程提交 / Workflow commit: df555011a0fbb743a6b1b163d826e805d2ed9786.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.9) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43714457218) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.9/docs/release-review.md)

</details>

## v1.0.8

Originally published 2026-11-14 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.8) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/657527aeb5bfe4e858716968c5cb8326f963976c)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 182 | `c307efe987f42f22c0ebc5d707978a51b30c251d03c47e74856eec5a2fea8d8d` |
| `SoundBot-1.0.8-arm64.dmg` | 210015539 | `1d1e2de75e4c0883f138899bb4fc55379db50e3f6f4fbb7b4fabb79cb7b97059` |
| `SoundBot-Setup-1.0.8.exe` | 210908281 | `44174a29719e9152c4bda29119426bbeeca3177b59513b5d361b60835aaa9884` |

<details>
<summary>Original release notes / 原发行说明</summary>

## [1.0.8] - 2026-11-14

### Fixed / 修复

- Remove an imported source folder from its context menu. Original files, individually imported sounds, and sounds still linked to another source stay in place. / 已导入的来源文件夹可通过右键菜单移除。磁盘原文件、单独导入的素材和仍关联其他来源的素材会保留。
- Keep library creation and file and folder import available after removing the last library. Starting an import from the empty state prompts for a new library name. / 移除最后一个音效库后，仍可新建库并导入文件或文件夹。空库状态下导入时可直接命名新库。
- Read playable WAV files with legacy metadata encodings during import, waveform generation, and preview. Failed imports can be retried from the task history. / 兼容带旧编码元数据的可播放 WAV，修复入库、波形和试听读取，已有失败项可从任务记录重试。
- Back up the local SQLite database before upgrading it to preserve the relationship between source folders and individual imports. / 升级前备份本地 SQLite 数据库，迁移后继续区分来源文件夹和单独导入的素材。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 标准 CI 产物供维护者验收，未配置发布者签名凭据。 / Standard CI produces review installers without publisher signing credentials.
- 源码提交 / Source commit: 657527aeb5bfe4e858716968c5cb8326f963976c. 构建流程提交 / Workflow commit: 657527aeb5bfe4e858716968c5cb8326f963976c.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.8) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43610373859) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.8/docs/release-review.md)

</details>

## v1.0.7

Originally published 2026-11-13 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.7) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/d4fc8cec8b223275b86a2e7c79af5a3bdc65186b)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 182 | `721cbfa620f074e740817e51b497c3ac7905df70bfc40c4182014ac6a1f241c4` |
| `SoundBot-1.0.7-arm64.dmg` | 210016107 | `01533518b8936fc6c93ef38593e0add73eeed533872a1aeb36435beb15450b77` |
| `SoundBot-Setup-1.0.7.exe` | 210905520 | `b4b842fda9098dd8df93aeb86fb61f9d789a349190ece167ab3ff3ad02c234f0` |

<details>
<summary>Original release notes / 原发行说明</summary>

## [1.0.7] - 2026-11-13

### Fixed / 修复

- Load a selected VST3 and open its editor in one worker on macOS and Windows. Show loading progress until the editor is ready. / macOS 与 Windows 使用同一个工作进程完成 VST3 加载和首次打开编辑器，并显示加载提示。
- Place Windows plug-in editors beside the main window with a draggable title bar, minimize and close controls, and resizing where the native editor allows it. / Windows 插件编辑器在主窗口附近打开，可拖动标题栏、最小化和关闭，原生编辑器支持时可调整尺寸。
- Preserve audible reverb and VST3 tails in audition and WAV delivery. The library and middleware workspace show the waveform from rendered playback. / 试听与 WAV 交付保留混响及 VST3 可听尾音，音效库和中间件工作台显示实际渲染的播放波形。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 标准 CI 产物供维护者验收，未配置发布者签名凭据。 / Standard CI produces review installers without publisher signing credentials.
- 源码提交 / Source commit: d4fc8cec8b223275b86a2e7c79af5a3bdc65186b. 构建流程提交 / Workflow commit: d4fc8cec8b223275b86a2e7c79af5a3bdc65186b.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.7) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43409070133) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.7/docs/release-review.md)

### VST3 与尾音验收 / VST3 and effect-tail verification

- Windows 安装态用 AGain 验证了原生小窗归属、最小化后从效果链恢复，以及打开编辑器时的试听处理。拖边缩放取决于插件原生界面，标题栏配色和圆角也受 Windows 版本支持影响。 / The installed Windows app was tested with AGain for window ownership, minimize and restore, and audio processing while its editor was open. Edge resizing depends on the plug-in, and title-bar colors and rounded corners depend on Windows support.
- macOS 本机用 Dehumaniser Simple Monsters 验证了加载和试听，用 Valhalla Supermassive 验证了第三方尾音的播放与波形。效果尾音按实际渲染输出保留，单次混音最多延长 30 秒。 / Dehumaniser Simple Monsters was tested locally on macOS for loading and audition. Valhalla Supermassive was used to verify third-party tails in playback and the rendered waveform. SoundBot retains audible output for up to 30 seconds beyond the source mix.

</details>

## v1.0.6

Originally published 2026-11-13 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.6) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/50403a54551a3263786ce9a85091af73ce82fcb2)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 182 | `7043008586176fe7da54230f32a1b315874c674e444593ab021eabd4591307f6` |
| `SoundBot-1.0.6-arm64.dmg` | 209970405 | `e9b67ee11f3159b42f21a48624984e1dc191d448602d945ba42c4763f7a7fea4` |
| `SoundBot-Setup-1.0.6.exe` | 210895628 | `4552e2633d28a09f574abba17e38e152c020c7181e7b2b0c85cdc2669bbfa5cc` |

<details>
<summary>Original release notes / 原发行说明</summary>

## [1.0.6] - 2026-11-13

### Changed / 变更

- Updated the SoundBot mark across the application, installers, README, and native drag indicator. / 更新 SoundBot 标识，同步应用、安装包、README 与原生拖拽图标。

### Fixed / 修复

- Start isolated VST3 workers without a Windows console. Keep native editor windows attached to SoundBot and render audio while the editor is open. / Windows 的 VST3 进程在后台启动。原生插件小窗随 SoundBot 主窗口管理，打开时可继续试听。
- Reserve Space for playback and text entry. / 空格键用于播放和暂停，文本输入区照常输入空格。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 标准 CI 产物供维护者验收，未配置发布者签名凭据。 / Standard CI produces review installers without publisher signing credentials.
- 源码提交 / Source commit: 50403a54551a3263786ce9a85091af73ce82fcb2. 构建流程提交 / Workflow commit: 50403a54551a3263786ce9a85091af73ce82fcb2.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.6) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43401297503) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.6/docs/release-review.md)

### VST3 验证 / VST3 verification

- macOS 本机使用 Dehumaniser Simple Monsters 验证了原生编辑器、试听和带效果的 WAV 导出。 / Dehumaniser Simple Monsters was tested locally on macOS for native editing, playback, and WAV export with effects.
- Windows 安装态测试使用 AGain，确认插件小窗归属 SoundBot，打开时可继续试听，且输出确实应用了增益。窗口缩放取决于插件编辑器。 / The Windows installer was tested with AGain. Its editor opens in a SoundBot-owned window and playback continues with gain applied. Resizing depends on the plug-in editor.

</details>

## v1.0.5

Originally published 2026-11-13 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.5) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/cc7d71dfb0f39fe753b780384c245b010ba89600)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 182 | `1ffa5fe4d9258e04c6cec6431ff731fbd887ef61a54bd1270ea3f88ba614d801` |
| `SoundBot-1.0.5-arm64.dmg` | 209956983 | `c11de2408cda0f795a528a1d7fe87a312e940fc6096c66da57a156b55df4f3d8` |
| `SoundBot-Setup-1.0.5.exe` | 210884081 | `cf973f2883300aa5e53415ef10dd11c76d321afb941d96208303e573e1415d45` |

<details>
<summary>Original release notes / 原发行说明</summary>

### 宿主、插件与签名 / Hosts, plug-ins and signing

- macOS 本机验证了 VST3 原生窗口打开时的试听、单声道适配、故障旁通和 WAV 导出。Windows 已通过 NSIS 安装、导入和试听测试。Windows 第三方插件及窗口缩放仍待实机验证。插件窗口尺寸取决于插件自身支持。 / On macOS, we checked audition with a native VST3 editor open, mono adaptation, fallback on processing errors, and WAV export. Windows passed NSIS installation, import, and audition tests. Windows third-party plug-ins and window scaling still need desktop verification. Editor sizing depends on the plug-in.
- 使用模拟 WAAPI 工程验证了独立轨道效果与成功写回后的复位。 / Track effects and their reset after successful writeback were checked with a simulated WAAPI project.
- macOS 应用使用临时签名，未经 Apple 公证。Windows 安装包无发布者 Authenticode 签名。 / The macOS app is ad hoc signed and unnotarized. The Windows installer has no publisher Authenticode signature.

### 源码与许可 / Source and licenses

SoundBot 采用 GPL-3.0-or-later。安装包附项目和第三方许可，包括内嵌 MiSans 的字体许可。 / SoundBot is licensed under GPL-3.0-or-later. Installers include the project and third-party notices, including the license for the embedded MiSans fonts.

- [SoundBot 源码归档 / Source archive](https://codeload.github.com/Huckrick/SoundBot/tar.gz/refs/tags/v1.0.5) · [构建说明 / Build instructions](https://github.com/Huckrick/SoundBot/blob/v1.0.5/docs/build-and-retrieval.md)
- [PyAV 18.0.0](https://codeload.github.com/PyAV-Org/PyAV/tar.gz/refs/tags/v18.0.0) · [FFmpeg 8.1.2](https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz) · [Wheel build recipes and patches](https://codeload.github.com/PyAV-Org/pyav-ffmpeg/tar.gz/refs/tags/8.1.2-1)
- [Pedalboard 0.9.22](https://codeload.github.com/spotify/pedalboard/tar.gz/refs/tags/v0.9.22) · [Mutagen 1.47.0](https://codeload.github.com/quodlibet/mutagen/tar.gz/refs/tags/release-1.47.0) · [Electron 45.3.1](https://codeload.github.com/electron/electron/tar.gz/refs/tags/v45.3.1) · [完整致谢与许可 / Credits and licenses](https://github.com/Huckrick/SoundBot/blob/v1.0.5/docs/credits.md)## [1.0.5] - 2026-11-13

### Changed / 变更

- Use one library audition effects chain across assets and render it into selection exports. / 音效库跨素材共用试听效果链，并将效果渲染到选区导出文件。
- Keep independent middleware track effects and reset them after successful delivery. / 中间件各轨独立保存效果设置，交付成功后复位。
- Show SoundBot confirmations inside the app and apply the app theme to native VST3 window chrome. / SoundBot 确认提示使用应用内弹窗，VST3 原生窗口外框随应用主题显示。

### Fixed / 修复

- Adapt mono audio for stereo-only VST3 effects and continue audition with a warning when a plug-in cannot process audio. Delivery stops on a plug-in failure. / 为仅支持立体声的 VST3 适配单声道素材。插件处理失败时提示并继续试听，交付遇到该错误则停止。
- Pass native editor state updates to audition while playback continues. / 播放时同步 VST3 原生编辑器的状态更新。
- Allow native source playback from SoundBot's local audio service. / 允许播放器从 SoundBot 本地音频服务直接试听源文件。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 标准 CI 产物供维护者验收，未配置发布者签名凭据。 / Standard CI produces review installers without publisher signing credentials.
- 源码提交 / Source commit: cc7d71dfb0f39fe753b780384c245b010ba89600. 构建流程提交 / Workflow commit: cc7d71dfb0f39fe753b780384c245b010ba89600.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.5) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43391326350) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.5/docs/release-review.md)

</details>

## v1.0.4

Originally published 2026-11-13 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.4) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/bdd985fc9d83c862a57290f747e7b331f8cd8424)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 182 | `baf5a4115ec936f9f758c767606680f8541659258db689a40539cbcf05397f49` |
| `SoundBot-1.0.4-arm64.dmg` | 209949712 | `05f16a1b8a370dcce09744f76458b6ba943db41556712aa091eefcca61c446de` |
| `SoundBot-Setup-1.0.4.exe` | 210871224 | `c8167b1918b0114b492f604a812e9a345f4e8b6a0e27ce8f85b6ad003f06d0e6` |

<details>
<summary>Original release notes / 原发行说明</summary>

## [1.0.4] - 2026-11-13

### Changed / 变更

- Browse and search the full Wwise project tree with viewport-based rendering. / Wwise 工程树支持完整浏览和搜索，界面只渲染可见行。
- Edit fades directly on clips, set individual crossfade shapes, and draw stereo pan automation on the waveform. / 在剪辑块上直接调整淡化，为单个交叉区设置曲线，在波形上编辑立体声声像。
- Open native VST3 editors, retain their state for audition and delivery, and reorder, bypass, or remove effects in the rack. / 打开 VST3 原生界面，保留插件状态用于试听和交付，并在效果链排序、旁通或移除。

### Fixed / 修复

- Reuse compact waveform previews when browsing and refreshing library results. / 浏览和刷新音效库时复用轻量波形预览。
- Report non-finite VST3 output as a render error. / VST3 返回非有限采样时报告渲染错误。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 源码提交 / Source commit: bdd985fc9d83c862a57290f747e7b331f8cd8424. 构建流程提交 / Workflow commit: bdd985fc9d83c862a57290f747e7b331f8cd8424.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.4) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43379810046) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.4/docs/release-review.md)

### 宿主、插件与签名 / Hosts, plug-ins and signing

- 使用包含 900 多条音频源的模拟 WAAPI 工程验证了树形浏览、搜索及选中音频。真实 Wwise 工程仍需在目标环境验收。 / Tree navigation, search, and source selection were verified with a simulated WAAPI project containing more than 900 sources. Verify compatibility with your Wwise project in its target environment.
- 在 macOS 上验证了已安装 VST3 原生窗口的打开和关闭，以及两套插件状态产生不同渲染结果。关闭插件窗口后，新设置用于下一次试听和交付。Windows 的第三方插件窗口尚待实机验证。 / The native VST3 editor lifecycle and renders with two different plug-in states were checked on macOS. Close the editor to apply its settings to the next audition and render. Windows third-party editor compatibility is still to be verified on a desktop installation.
- macOS 应用使用临时签名，未经 Apple 公证。Windows 安装包没有发布者 Authenticode 签名。下载后可核对同一发行条目中的 SHA-256。 / The macOS app is ad hoc signed and unnotarized. The Windows installer has no publisher Authenticode signature. Verify the download against the SHA-256 file in this release.

### 源码与许可 / Source and licenses

SoundBot 采用 GPL-3.0-or-later。安装包附应用及第三方许可声明，包括内嵌 MiSans 的字体许可。 / SoundBot is licensed under GPL-3.0-or-later. Installers include the project and third-party notices, including the license for the embedded MiSans fonts.

- [SoundBot v1.0.4 源码归档 / Source archive](https://codeload.github.com/Huckrick/SoundBot/tar.gz/refs/tags/v1.0.4) · [构建说明 / Build instructions](https://github.com/Huckrick/SoundBot/blob/v1.0.4/docs/build-and-retrieval.md)
- [PyAV 18.0.0](https://codeload.github.com/PyAV-Org/PyAV/tar.gz/54a4395bb4cdd9cdd53ff6216c50b69f6475c13d) · [FFmpeg 8.1.2](https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz) · [Wheel build recipes and patches](https://codeload.github.com/PyAV-Org/pyav-ffmpeg/tar.gz/a71bf9279f7a4659154b68ba6783e89be460bcd5)
- [Pedalboard 0.9.22](https://codeload.github.com/spotify/pedalboard/tar.gz/refs/tags/v0.9.22) · [Mutagen 1.47.0](https://codeload.github.com/quodlibet/mutagen/tar.gz/refs/tags/release-1.47.0) · [Electron 45.3.1](https://codeload.github.com/electron/electron/tar.gz/refs/tags/v45.3.1) · [完整致谢与许可 / Credits and licenses](https://github.com/Huckrick/SoundBot/blob/v1.0.4/docs/credits.md)

</details>

## v1.0.3

Originally published 2026-11-13 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.3) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/8f976ee181c6fa3a9c19a5ecc8c58826915fbf17)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 182 | `23624470614f47516e449e6f2ee038857cc8b04d6aea3fe803c857b10079f693` |
| `SoundBot-1.0.3-arm64.dmg` | 209913137 | `f22bafebf6ac210e19f276ed6ebd82bf1b0a2268fdcfc3d9d249778f0fcccb4e` |
| `SoundBot-Setup-1.0.3.exe` | 210862970 | `07ced5292c77db038ebd3dc45f117f594397117ebfe22b1b87df36440666696e` |

<details>
<summary>Original release notes / 原发行说明</summary>

## [1.0.3] - 2026-11-13

### Changed / 变更

- Zoom the library waveform with the mouse wheel and drag a thumbnail to deliver the whole sound. Keep rendered thumbnails in a bounded cache as the list scrolls. / 波形支持滚轮缩放，从列表缩略波形拖出整条素材。列表滚动时复用有限数量的已渲染缩略图。
- Show Wwise object colors and type icons along the source hierarchy. Keep the browser's branch state and add indentation guides. / 在 Wwise 音频源层级中显示对象颜色和类型图标，保留展开状态并加入层级辅助线。
- Add five fade and crossfade shapes, editable stereo clip pan automation, and a dedicated clip editing bar below the timeline. / 新增五种淡化和交叉淡化曲线、可编辑的立体声剪辑声像曲线，以及时间线下方的剪辑编辑栏。
- Discover installed VST3 plug-ins in the standard folders. SoundBot asks for confirmation when loading a selected plug-in. / 自动发现标准安装目录中的 VST3 插件，选定后确认加载。

- Embedded the original MiSans, MiSans TC, and MiSans Latin variable fonts for all six interface languages. Expanded Settings → About with authorship, font licensing, component references, and community thanks. The installer includes the full credits and Xiaomi’s font agreement. / 六种界面语言均使用内嵌的原版 MiSans、MiSans TC 或 MiSans Latin 可变字体。设置 → 关于补全作者、字体许可、组件引用和社区致谢，安装包随附完整致谢及小米字体协议原文。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 标准 CI 产物供维护者验收，未配置发布者签名凭据。 / Standard CI produces review installers without publisher signing credentials.
- 源码提交 / Source commit: 8f976ee181c6fa3a9c19a5ecc8c58826915fbf17. 构建流程提交 / Workflow commit: 8f976ee181c6fa3a9c19a5ecc8c58826915fbf17.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.3) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43373067083) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.3/docs/release-review.md)

### 宿主与 VST3 / Host and VST3 compatibility

- Wwise 工程树、颜色与写回流程使用模拟 WAAPI 工程验证。实际 Wwise 工程及 DAW 原生拖出需在目标环境确认。 / Wwise tree navigation, object colors, and writeback were tested with a simulated WAAPI project. Check native DAW drag-out and Wwise compatibility in your target environment.
- macOS 本机验证了标准目录自动发现及 ValhallaSupermassive 的立体声渲染。ValhallaFreqEcho 在本次测试环境中可加载，但返回非有限采样，可能试听无声。遇到此情况可旁通该插件。 / On macOS, discovery and stereo rendering were verified with ValhallaSupermassive. ValhallaFreqEcho loaded but returned non-finite samples in this test setup and may produce silence. Bypass it if you encounter this behavior.
- macOS 应用使用临时签名，未经 Apple 公证。Windows 安装包没有发布者 Authenticode 签名。可用本页的 SHA-256 和构建证明核对下载来源。 / The macOS app is ad hoc signed and unnotarized. The Windows installer has no publisher Authenticode signature. Use the SHA-256 hashes and build attestations to verify the downloads.

### 源码与许可 / Source and licenses

SoundBot 采用 GPL-3.0-or-later。安装包包含应用及第三方许可声明，内嵌 MiSans 随包提供其字体许可。 / SoundBot is licensed under GPL-3.0-or-later. The installers contain project and third-party notices, including the license for the embedded MiSans fonts.

- [SoundBot v1.0.3 源码归档 / Source archive](https://codeload.github.com/Huckrick/SoundBot/tar.gz/refs/tags/v1.0.3) · [构建说明 / Build instructions](https://github.com/Huckrick/SoundBot/blob/v1.0.3/docs/build-and-retrieval.md) · [本次构建流程 / Workflow used](https://github.com/Huckrick/SoundBot/blob/v1.0.3/.github/workflows/build.yml)
- [PyAV 18.0.0 source](https://codeload.github.com/PyAV-Org/PyAV/tar.gz/54a4395bb4cdd9cdd53ff6216c50b69f6475c13d) · [FFmpeg 8.1.2 source](https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz) · [Wheel build recipes and patches](https://codeload.github.com/PyAV-Org/pyav-ffmpeg/tar.gz/a71bf9279f7a4659154b68ba6783e89be460bcd5)
- [Pedalboard 0.9.22 source](https://codeload.github.com/spotify/pedalboard/tar.gz/refs/tags/v0.9.22) · [Mutagen 1.47.0 source](https://codeload.github.com/quodlibet/mutagen/tar.gz/refs/tags/release-1.47.0) · [Electron 45.3.1 source](https://codeload.github.com/electron/electron/tar.gz/refs/tags/v45.3.1) · [完整致谢与许可 / Credits and licenses](https://github.com/Huckrick/SoundBot/blob/v1.0.3/docs/credits.md)

</details>

## v1.0.2

Originally published 2026-11-12 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.2) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/f2f1f67f6d9e685c9ff9f957ea6aee63fc9939d2)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 182 | `b5a8e546751fbc578a14a82c7f29fd151fe9d4618e8b01d27676ee2a804bc55f` |
| `SoundBot-1.0.2-arm64.dmg` | 188232996 | `3c46318dc03b772b8089a2dd66e4faaa1e413f0cf2bbbfe7d7580950f214615d` |
| `SoundBot-Setup-1.0.2.exe` | 191485623 | `d02cfd83bc648d70131db03ffc8d58ae0508e8023543d5ca79a06ca52c20fb37` |

<details>
<summary>Original release notes / 原发行说明</summary>

## MiSans 与致谢更新 / MiSans and credits update

新构建的 **设置 → 关于** 补全了 Huckrick 的作者署名、组件与标准来源和社区致谢。六种界面语言均使用内嵌的原版 MiSans 系列字体，随包提供小米字体许可原文。 [完整致谢](https://github.com/Huckrick/SoundBot/blob/1430074878ea63207d37b53281a92201928e3e77/docs/credits.md)

The updated **Settings → About** credits Huckrick, upstream projects and standards, and the community. All six interface languages use embedded original MiSans fonts. The installer includes Xiaomi’s font agreement. [Full credits](https://github.com/Huckrick/SoundBot/blob/1430074878ea63207d37b53281a92201928e3e77/docs/credits.md)

- **1.0.2 MiSans 预览 / 1.0.2 MiSans preview** — [macOS Apple Silicon 和 Windows x64 / macOS Apple Silicon and Windows x64](https://github.com/Huckrick/SoundBot/actions/runs/43210447438#artifacts)。登录 GitHub 后从 Artifacts 下载，产物保留 7 天。 / Sign in to GitHub to download from Artifacts. Downloads are available for seven days.
- **正式发行 / Stable release** — 下方 Assets 来自已锁定的 `v1.0.2` 标签，对应原始发行。 / The Assets below are the original installers from the locked `v1.0.2` tag.

## [1.0.2] - 2026-11-12

### Added / 新增

- Added timeline zoom, draggable playhead, clip edge trimming, cross-track clip movement, and automatic crossfades where adjacent clips overlap on one track. Preview and delivery use the same mix. / 时间线支持缩放、拖动播放头、剪辑边缘裁剪、跨轨移动以及同轨相邻剪辑重叠时自动交叉淡化，试听与交付使用同一混音。
- Drag a full sound from the library list into the middleware timeline or use its DAW drag handle to deliver the full WAV. / 可从音效库列表拖动整条素材到中间件时间线，或用该行的 DAW 拖拽手柄交付整条 WAV。

### Fixed / 修复

- Allow removing any library, including the default or last library, while keeping the original audio files. Active imports show progress in the lower-right corner, and finished jobs remain available in Job history. / 可移除任意音效库，包括默认库和最后一个库，原始音频文件保留。右下角显示进行中的导入进度，已结束的任务可在任务记录中查看。
- Keep EQ bypassed by default and show live signal and loudness during source playback as well as processed previews. / 均衡器默认旁通，原文件试听和效果试听均显示实时信号与响度。
- Preserve expanded middleware tree branches during refreshes and show Wwise sound colors when WAAPI provides them. / 刷新时保留中间件素材树的展开状态，并显示 WAAPI 提供的 Wwise 音效颜色。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 标准 CI 产物供维护者验收，未配置发布者签名凭据。 / Standard CI produces review installers without publisher signing credentials.
- 源码提交 / Source commit: f2f1f67f6d9e685c9ff9f957ea6aee63fc9939d2. 构建流程提交 / Workflow commit: f2f1f67f6d9e685c9ff9f957ea6aee63fc9939d2.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.2) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43192658880) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.2/docs/release-review.md)

</details>

## v1.0.1

Originally published 2026-11-12 · Stable / 稳定版 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v1.0.1) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/d0f9b5262eca71a361c504d671799b1f8076c153)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `SHA256SUMS.txt` | 182 | `790bf5ebe621c500c197d4e5ba6ef394b7326eb464807309dbcd60dd2093a1f6` |
| `SoundBot-1.0.1-arm64.dmg` | 188227665 | `7bfe55d44207e68ed3b64f4b3f309e1ad6e21e4bb417c46ad18a5107e757dd70` |
| `SoundBot-Setup-1.0.1.exe` | 191479197 | `3deeb787cc737936e326ff58c4e550504354a98eebd0521cd94cce938c4c3319` |

<details>
<summary>Original release notes / 原发行说明</summary>

## [1.0.1] - 2026-11-12

### Security / 安全

- Updated the networking dependencies and packaging toolchain to address reported advisories. The tag build checks both dependency locks. / 更新网络依赖和打包工具以修复已报告的告警，标签构建会检查两套依赖锁。
- Restricted the desktop scan WebSocket to the same per-launch credential as other local data routes, and denied browser permission requests from the desktop UI. / 扫描 WebSocket 与其他本地数据接口共用每次启动的访问凭据，桌面界面拒绝浏览器权限申请。
- Moved access to the system credential store to explicit assistant actions and removed the full disk access shortcut. / 只有主动使用助手时才访问系统安全存储，移除全磁盘访问入口。

### Changed / 变更

- Configured the Windows installer for the current user and removed unused macOS runtime entitlements. The app asks before loading a selected VST3 plug-in and identifies that action as local code execution. / Windows 安装器按当前用户配置，精简 macOS 运行时权限。加载所选 VST3 前说明插件会执行本机代码并请用户确认。
- Included project, runtime, and codec license notices in the installers and expanded bilingual download, support, privacy, and release verification guidance. / 安装包附带项目、运行时和编解码器许可证说明，补齐双语下载、支持、隐私和发行验收指南。

### 安装与验收 / Install and verification

- macOS 14+ Apple Silicon 使用 DMG，Windows 10/11 x64 使用 EXE。安装包对应的 SHA-256 在同一发行条目的校验清单中。 / Use the DMG for macOS 14+ on Apple Silicon and the EXE for Windows 10/11 x64. Compare your download with the checksum list in this release.
- 两平台均通过本机架构构建、安装包验证、音频格式及桌面启动和试听测试。宿主版本的实机结果另行记录。 / Both platforms passed native builds, package verification, audio format checks, and desktop startup and playback tests. Host compatibility results are recorded separately.
- 签名状态：macOS 使用临时签名，未经过 Apple 公证；Windows 未使用发布者 Authenticode 证书。请使用本发行条目的 SHA-256 和构建证明核对下载来源。 / Signing status: the macOS app is ad hoc signed and unnotarized. The Windows installer has no publisher Authenticode signature. Use this release’s SHA-256 hashes and build attestations to verify the downloads.
- 源码提交 / Source commit: d0f9b5262eca71a361c504d671799b1f8076c153. 构建流程提交 / Workflow commit: a50cf5a8c08d7da2f8cfe92bbb77eed7641481e2.

### 界面语言 / Interface languages

在 **设置 → 常规 → 语言** 切换简体中文、繁體中文、日本語、English、Português (Brasil) 和 Español。素材名称、源标签及第三方插件界面保留原文。 / In **Settings → General → Language**, choose Simplified Chinese, Traditional Chinese, Japanese, English, Brazilian Portuguese, or Spanish. Asset names, source tags, and third-party plug-in interfaces stay in their original language.

[对应源码 / Tagged source](https://github.com/Huckrick/SoundBot/tree/v1.0.1) · [构建记录 / Build run](https://github.com/Huckrick/SoundBot/actions/runs/43177171792) · [安装与支持 / Installation and support](https://github.com/Huckrick/SoundBot#readme) · [分发审查 / Distribution review](https://github.com/Huckrick/SoundBot/tree/v1.0.1/docs/release-review.md)

### 源码和许可 / Source and licenses

SoundBot 以 GPL-3.0-or-later 许可分发。安装包包含应用及第三方许可文件。对应源码和构建输入可从以下固定版本取得。 / SoundBot is distributed under GPL-3.0-or-later. Installers include the project and third-party license notices. Source and build inputs are available at these pinned versions.

- SoundBot v1.0.1 源码与构建流程 / Application source and build workflow. [源码归档 / Source archive](https://codeload.github.com/Huckrick/SoundBot/tar.gz/refs/tags/v1.0.1) · [本次构建流程 / Workflow used](https://github.com/Huckrick/SoundBot/blob/a50cf5a8c08d7da2f8cfe92bbb77eed7641481e2/.github/workflows/build.yml)
- PyAV 18.0.0 和 FFmpeg 8.1.2。配方归档包含平台构建脚本、编解码器源地址和补丁。 / PyAV 18.0.0 and FFmpeg 8.1.2. The pinned recipe archive contains platform build scripts, codec source URLs, and patches. [PyAV source](https://codeload.github.com/PyAV-Org/PyAV/tar.gz/54a4395bb4cdd9cdd53ff6216c50b69f6475c13d) · [FFmpeg source](https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz) · [Pinned recipe and patches](https://codeload.github.com/PyAV-Org/pyav-ffmpeg/tar.gz/a71bf9279f7a4659154b68ba6783e89be460bcd5)
- 其他运行时对应源码 / Other bundled runtime sources. [Pedalboard 0.9.22](https://codeload.github.com/spotify/pedalboard/tar.gz/refs/tags/v0.9.22) · [Mutagen 1.47.0](https://codeload.github.com/quodlibet/mutagen/tar.gz/refs/tags/release-1.47.0) · [Electron 45.3.1](https://codeload.github.com/electron/electron/tar.gz/refs/tags/v45.3.1)

</details>

## v0.2.0

Originally published 2026-08-13 · Prerelease / 预发布 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v0.2.0) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/2365b2c85274deb8db217c0a2314e005a4acfecb)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `models.zip` | 719473555 | `7296c9a2b4fc01f562e80c8fdd091e1235cfe773c3044e6655c530e4b2460c34` |
| `models.zip.sha256` | 77 | `08922d16a9e0b1eb71d4ff415a75593887fd4c8ea0e8a36214f15af6f11b2b71` |
| `SHA256SUMS.txt` | 259 | `6f106ff97181589a42a3fabae25891590a9e0fe593336bff75ddea46af6ca02f` |
| `SoundBot-0.2.0-arm64.dmg` | 312439519 | `e7145e60f2bc4b5a02a5a7f42bd6cd980fd151054af0de1573e6b2b8a70329a7` |
| `SoundBot-Setup-0.2.0.exe` | 281303104 | `fd0708643efbed1ac4ae6798b298d7cebc65c2ca3a2845e6f86c2691091a72fe` |

<details>
<summary>Original release notes / 原发行说明</summary>

> **Archived prerelease · 歴史的なプレリリース · 历史预发布**
>
> This page documents an early prerelease of SoundBot by Huckrick. For current Windows and macOS installers, use the latest stable release.
>
> このページは初期のプレリリースの記録です。 · 本页是早期预发布记录。请从最新稳定版页面下载 Windows 或 macOS 安装包。
>
> [Latest stable release · 最新安定版 · 最新稳定版](https://github.com/Huckrick/SoundBot/releases/latest)

## [0.2.0] - 2026-08-12 (Prerelease / 预发布)

### Added / 新增

- Added versioned SQLite schema v3 migration with one immutable pre-migration snapshot, transactional rollback, integrity checking, incremental v2 artifact preservation, and safe merging of POSIX physical-path aliases. / 新增版本化 SQLite v3 迁移，包含一次性不可变迁移前快照、事务回滚、完整性检查、v2 artifact 增量保留，以及 POSIX 物理路径别名的安全合并。
- Added project-scoped file UUIDs, Windows-aware canonical path keys, source fingerprints, and safe legacy project-ID mapping. / 新增工程内文件 UUID、Windows 感知的规范化路径键、源文件指纹与安全的旧工程 ID 映射。
- Added durable `file_artifacts`, `index_manifests`, and `jobs` tables to track waveform, audio-vector, and text-vector state as `pending`, `processing`, `ready`, `failed`, or `stale`, including engine fingerprints and last errors. / 新增持久化 `file_artifacts`、`index_manifests` 与 `jobs` 表，分别以 `pending`、`processing`、`ready`、`failed`、`stale` 跟踪波形、音频向量和文本向量，并记录引擎指纹与最后错误。
- Added a unified audio capability table for WAV, MP3, FLAC, AIFF, AIF, OGG, M4A, AAC, and WMA across the picker, scanner, decoder, waveform service, indexer, and documentation. / 新增统一音频能力表，在选择器、扫描器、解码器、波形服务、索引器与文档中共同支持 WAV、MP3、FLAC、AIFF、AIF、OGG、M4A、AAC 和 WMA。
- Added a pinned PyAV decoder with wheel-bundled FFmpeg libraries and explicit PyInstaller collection of native libraries and third-party license notices. / 新增固定版本 PyAV 解码器及 wheel 内置 FFmpeg 动态库，并由 PyInstaller 显式收集原生库与第三方许可证说明。
- Added deterministic, bounded-memory waveform generation that always returns exactly 2,000 finite non-negative peaks in `[0,1]`, invalidated by size, `mtime_ns`, and waveform version. / 新增确定性、有界内存的波形生成，固定返回 2,000 个位于 `[0,1]` 的有限非负峰值，并按文件大小、`mtime_ns` 与波形版本自动失效。
- Added explicit waveform loading, cancellation, failure-reason, and retry states, plus DPR-aware Canvas sizing, `ResizeObserver`, and offscreen static waveform layers. / 新增明确的波形加载、取消、失败原因与重试状态，以及高 DPI Canvas、`ResizeObserver` 和离屏静态波形图层。
- Added fingerprinted, size-limited LRU playback-WAV generation for containers Chromium cannot reliably play directly. / 新增带源文件指纹、容量限制和 LRU 清理的播放 WAV，为 Chromium 无法可靠直放的容器提供回退。
- Added separate cosine Chroma collections for CLAP audio embeddings and text-metadata embeddings generated from filenames, logical folders, tags, UCS categories, and descriptions. / 新增相互独立的 cosine Chroma collection，分别存放 CLAP 音频向量与由文件名、逻辑目录、标签、UCS 分类和描述生成的文本元数据向量。
- Added CLAP text embeddings by default and LM Studio, Ollama, or OpenAI-compatible alternatives for the text-metadata branch only. / 文本元数据分支新增默认 CLAP 文本编码器，并支持 LM Studio、Ollama 或 OpenAI-compatible 替代方案。
- Added project-level index status, non-destructive reconciliation, and verified shadow-collection rebuild APIs and UI controls. / 新增工程级索引状态、非破坏性修复以及经过验证的影子 collection 重建 API 与界面控制。
- Added atomic activation of verified shadow manifests so failed or cancelled rebuilds retain the previous searchable collections. / 新增已验证影子 manifest 的原子激活，重建失败或取消时继续保留原有可搜索 collection。
- Added cursor-paginated metadata-only file listing, per-file and batch waveform endpoints, project-explicit import jobs, persistent job status, and cancellation APIs. / 新增游标分页的纯元数据文件列表、单文件与批量波形接口、显式工程导入作业、持久化作业状态及取消 API。
- Added per-result `audio_score`, `text_score`, `keyword_score`, and effective branch weights for explainable hybrid ranking. / 新增每条结果的 `audio_score`、`text_score`、`keyword_score` 与实际分支权重，使混合排序可诊断。
- Added asynchronous LLM support for LM Studio, Ollama, OpenAI, Kimi, DeepSeek, SiliconFlow, and custom OpenAI-compatible endpoints, including streaming, cancellation, timeouts, and bounded retries. / 新增 LM Studio、Ollama、OpenAI、Kimi、DeepSeek、SiliconFlow 与自定义 OpenAI-compatible 端点的异步 LLM 支持，包含流式响应、取消、超时和有限重试。
- Added OS-backed Electron `safeStorage` credential persistence with explicit `keep`, `set`, and `clear` intent and in-memory-only backend hydration. / 新增基于操作系统的 Electron `safeStorage` 凭据持久化，通过明确的 `keep`、`set`、`clear` 意图操作，并仅在后端内存中注入密钥。
- Added atomic migration of legacy plaintext AI keys into secure storage, with secure-store precedence, rollback, and original-file preservation on failure. / 新增旧版明文 AI 密钥到安全存储的原子迁移，遵循安全存储值优先，并在失败时回滚且保留原文件。
- Added committed audio fixtures for every supported extension and Windows path cases containing spaces, Chinese characters, `%`, `_`, `#`, `+`, and parentheses. / 新增覆盖全部支持扩展名的音频样本，以及包含空格、中文、`%`、`_`、`#`、`+` 和括号的 Windows 路径样例。
- Added migration, artifact-state, path, waveform, cosine ranking, cache invalidation, project isolation, LLM/Embedding mock, secret-storage, renderer-contract, and frozen-runtime tests. / 新增迁移、artifact 状态、路径、波形、cosine 排序、缓存失效、工程隔离、LLM/Embedding 模拟、密钥存储、渲染层契约和冻结运行时测试。
- Added complete Chinese and English README mirrors and synchronized bilingual release notes. / 新增完整的中英文 README 镜像与同步的双语发布说明。

### Changed / 变更

- Hardened GitHub release automation by allowing only pushed annotated `v*` tags to trigger it, binding every job to that tag commit, pinning official Actions to immutable SHAs, building both platforms through the same native-host script, separating model-free and CLAP-ready smoke gates, publishing only after draft asset verification, and adding checksums plus provenance attestations. / 加固 GitHub 发布自动化：仅允许推送 annotated `v*` tag 触发，所有作业绑定该 tag 的同一提交，官方 Actions 固定到不可变 SHA，双平台统一使用原生宿主构建脚本，拆分无模型与 CLAP 就绪冒烟门禁，仅在草稿资产核验后发布，并新增全资产校验和与 provenance 证明。
- Added an atomic version-bump command, strict release-channel/tag validation, exact-version model downloads, complete model-manifest verification, and official npm registry locks to keep future version updates and uploads reproducible. / 新增原子版本升级命令、严格发布渠道/tag 校验、精确版本模型下载、完整模型 manifest 验证与官方 npm registry 锁定，使后续版本更新和上传可复现。
- SQLite is now the single source of truth; files are persisted before waveform or vector work begins, and a failed artifact remains retryable instead of being counted as indexed. / SQLite 现在是唯一真相源；文件先持久化再生成波形或向量，失败 artifact 保持可重试而不再计为已索引。
- Import, repair, rebuild, search, AI chat, and cache state now carry an explicit immutable `project_id` and index revision to prevent cross-project leakage during project switches. / 导入、修复、重建、搜索、AI 对话和缓存状态现在携带显式且不可变的 `project_id` 与索引 revision，防止切换工程期间串库。
- CLAP audio embedding now uses deterministic processor-sized windows, aggregation, and normalization instead of random truncation. / CLAP 音频向量改为按处理器窗口确定性分窗、聚合与归一化，不再随机截断。
- New indexes use cosine distance and `similarity = 1 - distance`; legacy squared-L2 collections are rebuilt instead of being interpreted with an incorrect formula. / 新索引使用 cosine 距离与 `similarity = 1 - distance`；旧平方 L2 collection 会重建，不再套用错误公式。
- Hybrid search now defaults to audio CLAP 0.55, text metadata 0.30, and keyword/UCS 0.15, proportionally renormalizing only the available branches. / 混合搜索默认权重调整为音频 CLAP 0.55、文本元数据 0.30、关键词/UCS 0.15，并只对可用分支按比例重新归一化。
- Search filters now use legal `$and` combinations, and cache identity includes project, query, filters, index revision, model fingerprint, and pagination. / 搜索过滤改为合法 `$and` 组合，缓存标识包含工程、查询、过滤、索引 revision、模型指纹和分页参数。
- Changing an Embedding provider, model, or dimension now invalidates only the text-metadata index and preserves the CLAP audio index. / 切换 Embedding provider、模型或维度时仅使文本元数据索引失效，并保留 CLAP 音频索引。
- Heavy audio decoding and scanning now run in a bounded worker pool, while CLAP inference is serialized through a single model worker so FastAPI's event loop remains responsive. / 重型音频解码和扫描改在有界 worker 池运行，CLAP 推理由单一模型 worker 串行调度，使 FastAPI 事件循环保持响应。
- The renderer is split into plain-JavaScript API, state, audio, waveform, search, project, and settings modules without introducing a new frontend framework. / 渲染层拆分为原生 JavaScript 的 API、状态、音频、波形、搜索、工程和设置模块，未引入新前端框架。
- Individual imports now send paths to backend jobs instead of converting complete audio files into renderer-side `number[]` payloads. / 单文件导入改为向后端作业提交路径，不再把完整音频转换为渲染层 `number[]` 载荷。
- Model absence is now a degraded capability rather than a reason to clear data; base file management, playback, waveform, and keyword search remain available while vectors stay pending. / 模型缺失现在只表示能力降级而不是清理数据；基础文件管理、播放、波形和关键词搜索继续可用，相关向量保持 pending。
- A newly installed or replaced local CLAP package is now detected without restarting the backend; preload retries only after the package changes, then durable reconcile jobs automatically backfill recoverable vector artifacts for every project. / 现在无需重启后端即可检测新安装或替换的本地 CLAP 包；只有模型包发生变化才会重试预加载，成功后会为每个工程自动创建持久化 reconcile 作业并补算可恢复的向量 artifact。
- Model releases now use an immutable CLAP commit revision, per-file SHA-256 manifest, deterministic archive metadata, archive SHA-256, staged validation, and atomic replacement. / 模型发布改用不可变 CLAP commit revision、逐文件 SHA-256 manifest、确定性压缩包元数据、压缩包 SHA-256、暂存校验和原子替换。
- The official packaging matrix is limited to native Windows x64 and macOS arm64; cross-OS builds, Linux, and Intel macOS fail before mutation. / 正式打包矩阵收敛为原生 Windows x64 与 macOS arm64；跨系统构建、Linux 和 Intel macOS 会在修改构建目录前失败。
- The audio runtime is pinned to PyAV 18.0.0 with FFmpeg 8.1.2 binary wheels; the official macOS arm64 package therefore requires macOS 14 or later, while Windows keeps the complete `av.libs` closure. / 音频运行时固定为内置 FFmpeg 8.1.2 的 PyAV 18.0.0 二进制 wheel；因此正式 macOS arm64 包要求 macOS 14 或更高版本，Windows 则完整保留 `av.libs` 依赖闭包。
- Release notes are generated from the matching changelog version only after package, lockfile, backend, tag, and bilingual entry synchronization checks pass. / 发布说明改为在 package、lockfile、后端、tag 与双语条目版本同步检查通过后，从对应 changelog 版本自动生成。

### Fixed / 修复

- Fixed GitHub Release upload-name normalization by publishing the Windows installer as the stable `SoundBot-Setup-<version>.exe` asset, keeping the local filename, checksum manifest, provenance subject, and remote inventory identical. / 修复 GitHub Release 上传时对安装包名称的自动规范化问题，Windows 安装包现统一发布为稳定的 `SoundBot-Setup-<版本>.exe`，确保本地文件名、校验清单、provenance 对象与远端资产清单完全一致。
- Fixed empty arrays being accepted as valid waveform data, the primary renderer condition behind blank waveform areas in packaged Windows builds. / 修复空数组被当作有效波形数据的问题，该条件是 Windows 打包版波形区域空白的主要渲染层原因。
- Fixed packaged Windows decoding for compressed containers by removing the dependency on a system FFmpeg executable and collecting the complete PyAV runtime. / 通过移除系统 FFmpeg 可执行文件依赖并收集完整 PyAV 运行时，修复 Windows 打包版压缩容器解码。
- Fixed waveform cache reuse after a source file changed by validating both source and waveform-engine fingerprints before serving stored peaks. / 通过在返回缓存峰值前同时验证源文件与波形引擎指纹，修复源文件变化后继续复用旧波形的问题。
- Fixed IPC failures being converted into successful-looking empty payloads; errors now reject renderer promises with structured context. / 修复 IPC 失败被转换成看似成功的空载荷；错误现在携带结构化上下文并正确拒绝渲染层 Promise。
- Fixed import failures being permanently skipped on later runs and fixed failed files being included in indexed counts. / 修复导入失败后被后续运行永久跳过，以及失败文件被计入已索引数量的问题。
- Fixed SQLite/Chroma drift during add, update, delete, move, and tag changes through upsert, reconciliation, revision bumps, and cache invalidation. / 通过 upsert、对账、revision 递增和缓存失效，修复新增、更新、删除、移动与标签变更时的 SQLite/Chroma 状态失步。
- Fixed active collections accepting vectors from a changed model/provider and fixed search returning Chroma rows whose SQLite artifact is pending, failed, or stale; mixed or unknown engine fingerprints now require a verified shadow rebuild. / 修复 active collection 接受已变化模型/provider 的向量，以及搜索返回 SQLite artifact 为 pending、failed 或 stale 的 Chroma 记录；混合或未知引擎指纹现在必须经过验证的影子重建。
- Fixed supported but corrupt audio files disappearing during import: their stable file records and structured artifact failures now remain in SQLite for diagnosis and retry. / 修复格式受支持但内容损坏的音频在导入时消失；其稳定文件记录和结构化 artifact 失败状态现在保留在 SQLite 中，便于诊断与重试。
- Fixed project switches reusing indexers, searchers, AI services, or cache entries from another project or collection revision. / 修复切换工程后复用其他工程或 collection revision 的索引器、搜索器、AI 服务或缓存项。
- Fixed Windows drive-letter, slash, case, `%`/`_` wildcard, duplicate URL-decoding, and unsafe index-directory handling. / 修复 Windows 盘符、斜杠、大小写、`%`/`_` 通配、重复 URL 解码与不安全索引目录处理。
- Fixed tag keyword matching when SQLite stores tags as JSON arrays and fixed invalid Chroma filter combinations. / 修复 SQLite 以 JSON 数组保存标签时的关键词匹配，以及无效的 Chroma 过滤组合。
- Fixed SSE events split across transport chunks, duplicate user messages, missing assistant history, and stale AI searchers after a project switch. / 修复 SSE 事件跨传输 chunk 拆分、用户消息重复、assistant 历史缺失，以及工程切换后 AI 搜索器未重置的问题。
- Fixed synchronous provider calls blocking the FastAPI event loop and removed duplicate availability probes before normal LLM calls. / 修复同步 provider 调用阻塞 FastAPI 事件循环，并移除正常 LLM 调用前的重复可用性探测。
- Fixed high-DPI Canvas blur, interaction-coordinate drift, and unnecessary full waveform redraws while the playhead moves. / 修复高 DPI Canvas 模糊、交互坐标偏差，以及播放头移动时不必要的整幅波形重绘。
- Fixed backend startup races by treating the frozen process's `BOUND_PORT` handshake as authoritative and by applying a finite, identity-checked health timeout even when another HTTP service occupies the originally probed port. / 通过以后端冻结进程的 `BOUND_PORT` 握手为端口权威，并在原探测端口被其他 HTTP 服务占用时仍执行有限且带身份校验的健康超时，修复后端启动竞态。
- Fixed Windows project-index deletion by explicitly closing Chroma's shared client before removing its directory and performing the filesystem work outside the FastAPI event loop. / 通过在删除目录前显式关闭 Chroma 共享 client，并把文件系统操作移出 FastAPI 事件循环，修复 Windows 工程索引目录无法删除的问题。
- Fixed interrupted artifact and job states remaining permanently stuck after a process restart. / 修复进程重启后 artifact 与作业状态永久卡在处理中状态的问题。
- Fixed folder imports creating duplicate UUIDs and search results when macOS exposed the same file through `/tmp` and `/private/tmp`; scanner roots, SQLite keys, Chroma path keys, and imported-folder mappings now share physical-path identity without changing Windows drive or UNC semantics. / 修复 macOS 通过 `/tmp` 与 `/private/tmp` 暴露同一文件时，文件夹导入产生重复 UUID 和搜索结果的问题；扫描根目录、SQLite 路径键、Chroma 路径键与导入目录映射现在统一使用物理路径身份，同时保持 Windows 盘符与 UNC 语义。

### Removed / 移除

- Removed `indexed_files_meta.json` as an index-state authority and removed destructive collection resets from the normal repair path. / 移除 `indexed_files_meta.json` 的索引状态权威角色，并移除正常修复路径中的破坏性 collection reset。
- Removed generated fake waveform data, WaveSurfer scripts and regions code, and duplicate waveform rendering paths. / 移除生成的假波形数据、WaveSurfer 脚本与 regions 代码，以及重复波形渲染链路。
- Removed renderer and backend paths that converted a complete audio file into `number[]`, used backend PortAudio playback, or depended on an unused playback WebSocket/cache API. / 移除将完整音频转换为 `number[]`、使用后端 PortAudio 播放或依赖未使用播放 WebSocket/缓存 API 的前后端链路。
- Removed obsolete reindex/reset utilities, duplicate build helpers, personal hard-coded paths, stale AI/build-fix documents, and unused dependencies. / 移除过时的重索引/重置工具、重复构建辅助脚本、个人硬编码路径、失效的 AI/构建修复文档与无用依赖。
- Hid Azure OpenAI, Google Gemini, Anthropic, and Kimi Coding entries until their adapters are validated, while preserving legacy configuration for migration. / 在对应适配器验证前隐藏 Azure OpenAI、Google Gemini、Anthropic 与 Kimi Coding 入口，同时保留旧配置供未来迁移。

### Security / 安全

- API keys are encrypted by OS-backed `safeStorage`, never returned to the renderer, never persisted by the Python backend, and never included in logs or exported configuration. / API 密钥由操作系统支持的 `safeStorage` 加密，不回传渲染层、不由 Python 后端持久化，也不会进入日志或导出配置。
- Secret saves are transactional across secure storage and backend metadata; failed saves restore the previous encrypted store, while connection tests never persist temporary credentials. / 密钥保存横跨安全存储与后端元数据并具有事务语义；保存失败会恢复先前密文，连接测试不会持久化临时凭据。
- Project and collection paths are normalized and constrained to their configured root before creation or deletion, preventing unsafe IDs from escaping the project index directory. / 工程和 collection 路径在创建或删除前会规范化并限制在配置根目录内，防止不安全 ID 逃逸工程索引目录。
- Model extraction rejects absolute paths, traversal, symlinks, Zip Slip, missing checksums, mutable revisions, malformed manifests, and per-file hash mismatches before atomic installation. / 模型解压会在原子安装前拒绝绝对路径、目录穿越、符号链接、Zip Slip、缺失校验、可变 revision、异常 manifest 和逐文件哈希不匹配。
- Packaging verifies the native executable architecture, collects PyAV/FFmpeg licenses, and excludes `.DS_Store`, the development `SoundBot.png`, WaveSurfer, models, caches, and source-only backend files. / 打包会验证原生可执行文件架构、收集 PyAV/FFmpeg 许可证，并排除 `.DS_Store`、开发用 `SoundBot.png`、WaveSurfer、模型、缓存和仅源码后端文件。
- The Windows release gate starts the frozen backend with a clean `PATH` and no system FFmpeg, then requires the complete format matrix, exact waveforms, CLAP, Chroma, hybrid search, `win-unpacked`, and NSIS installer integrity checks to pass before publishing. / Windows 发布门禁会在干净 `PATH`、无系统 FFmpeg 下启动冻结后端，并要求全格式矩阵、精确波形、CLAP、Chroma、混合搜索、`win-unpacked` 与 NSIS 安装包完整性检查全部通过后才允许发布。
- CI builds Windows x64 and macOS arm64 only on native runners and blocks release creation if any functional smoke test, model check, metadata check, package check, or installer check fails. / CI 仅在原生 runner 构建 Windows x64 与 macOS arm64，并在任一功能冒烟、模型、元数据、应用包或安装包检查失败时阻止创建 Release。

</details>

## v0.1.3

Originally published 2026-06-08 · Prerelease / 预发布 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v0.1.3) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/33abfe647307a09f997b06f5b2288615746ae5f5)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `models.zip` | 719034379 | `846dcb9fe39e8d9e9e5a8fa92531e6d583a88eaf8eecffca828df80072a5101e` |
| `SoundBot-0.1.3-arm64.dmg` | 399523755 | `8c622362e143dc42b3c60f43d81d1006fa2a3078cca94dd56ffa230848b39340` |
| `SoundBot.Setup.0.1.3.exe` | 358647008 | `4c692ff5e53f3378dd4203820c80b751d0991efdfa88997cb7b8c887520c65e7` |

<details>
<summary>Original release notes / 原发行说明</summary>

> **Archived prerelease · 歴史的なプレリリース · 历史预发布**
>
> This page documents an early prerelease of SoundBot by Huckrick. For current Windows and macOS installers, use the latest stable release.
>
> このページは初期のプレリリースの記録です。 · 本页是早期预发布记录。请从最新稳定版页面下载 Windows 或 macOS 安装包。
>
> [Latest stable release · 最新安定版 · 最新稳定版](https://github.com/Huckrick/SoundBot/releases/latest)

修复打包后导入音频文件、导入文件夹、工程临时目录选择失败的问题。
修复 Windows 路径导致文件夹筛选、父目录识别异常的问题。
修复启动时只恢复工程标题、后端未真正切换工程的问题。
修复不同工程导入同一路径音频时互相覆盖的问题，并加入数据库自动迁移。
修复关键词搜索跨工程读取文件的问题。
修复工程专属临时目录没有真正参与磁盘容量显示和临时导出的问题。
修复 AI 配置打包后可能写入应用资源目录的问题，改为用户数据目录。
修复 LM Studio、Ollama、本地 Embedding 地址被误判为不安全的问题。
修复立体声/多声道淡入淡出处理错误。
补齐部分主进程 IPC 处理器，减少运行时 “no handler” 错误。
README、package.json、后端版本号、GitHub Actions 默认 tag 已更新到 v0.1.3。

</details>

## v0.1.2

Originally published 2026-04-18 · Prerelease / 预发布 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v0.1.2) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/11db59ddeaaaf0c055ce8406f924dfe1d11d428f)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `models.zip` | 719034323 | `82778929632d8075419885299f24d836c0dbd0ff4bf96eae8cba7430ace8d607` |
| `SoundBot-0.1.2-arm64.dmg` | 399525370 | `cbfe1572c3dfb2968db50d91310c3220879ddf7348ae8f48eb1450431509f2e8` |
| `SoundBot.Setup.0.1.2.exe` | 358642022 | `7675d019141c60d0edd375d27ec2d9ee3e70b60737680377d7138af1bc511f46` |

<details>
<summary>Original release notes / 原发行说明</summary>

> **Archived prerelease · 歴史的なプレリリース · 历史预发布**
>
> This page documents an early prerelease of SoundBot by Huckrick. For current Windows and macOS installers, use the latest stable release.
>
> このページは初期のプレリリースの記録です。 · 本页是早期预发布记录。请从最新稳定版页面下载 Windows 或 macOS 安装包。
>
> [Latest stable release · 最新安定版 · 最新稳定版](https://github.com/Huckrick/SoundBot/releases/latest)

</details>

## v0.1.1

Originally published 2026-03-22 · Prerelease / 预发布 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/v0.1.1) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/acefb244c54d71775122a5562c49a11cc90008d2)

| Original asset / 原产物 | Bytes / 字节 | Recorded SHA-256 / 已记录的校验值 |
| --- | ---: | --- |
| `models.zip` | 719034324 | `e0535af64095220e85c3d3afbf32b89f52865830d51b6b85ab15d8abe600c55f` |
| `SoundBot-0.1.1-alpha-arm64.dmg` | 94192429 | `402cbd71d603422beda84c2a03fc744706d16c32d76d2f73e1ec13af7cf36ce1` |
| `SoundBot-0.1.1-alpha.dmg` | 99309426 | `391cd13386459558fb8749a22ba1eead5de04d0decce33f0dfbb9b7c4f4518c1` |
| `SoundBot.Setup.0.1.1-alpha.exe` | 76531778 | `589ec76d681f968f1c5fdc1598dd2a786a6c7f2f86f53989c1900a2be47a149a` |
| `venv-macos.zip` | 380652377 | `cc917429ed9cd032391dfc31f9b3577814ef57bac385ba9c9ceb36bd219bbfb8` |
| `venv-windows.zip` | 425718154 | `bd2f9bdc8c234df8bac946da40242921365788aae1c6280cb4f7d26c4bc61f13` |

<details>
<summary>Original release notes / 原发行说明</summary>

> **Archived prerelease · 歴史的なプレリリース · 历史预发布**
>
> This page documents an early prerelease of SoundBot by Huckrick. For current Windows and macOS installers, use the latest stable release.
>
> このページは初期のプレリリースの記録です。 · 本页是早期预发布记录。请从最新稳定版页面下载 Windows 或 macOS 安装包。
>
> [Latest stable release · 最新安定版 · 最新稳定版](https://github.com/Huckrick/SoundBot/releases/latest)

## 🎉 SoundBot v0.1.1-alpha Release Notes
### 🆕 新架构：分离式发布 / New Architecture: Split Release
中文 ：
从 v0.1.1-alpha 开始，SoundBot 采用全新的 分离式架构 ，将应用包、AI 模型和 Python 环境分开发布。这使得应用包体积从原来的 2-3GB 大幅减小到约 100MB，同时保持完整功能。

English :
Starting from v0.1.1-alpha, SoundBot adopts a brand new split architecture , separating the app package, AI models, and Python environment. This reduces the app package size from 2-3GB to approximately 100MB while maintaining full functionality.

### 📦 发布文件说明 / Release Files
文件名 / Filename 大小 / Size 说明 / Description SoundBot-0.1.1-alpha.dmg ~100MB macOS 应用包 (Intel) SoundBot-0.1.1-alpha-arm64.dmg ~100MB macOS 应用包 (Apple Silicon) SoundBot Setup 0.1.1-alpha.exe ~100MB Windows 安装程序 models.zip ~500MB-1GB AI 模型 - 所有平台通用 venv-macos.zip ~1-2GB macOS Python 虚拟环境 venv-windows.zip ~1-2GB Windows Python 虚拟环境

### 🚀 安装指南 / Installation Guide
中文 ：

1. 下载对应平台的应用包
2. 下载 models.zip （所有平台通用）
3. 下载对应平台的 venv-*.zip
4. 将资源文件解压到应用目录
5. 启动应用
English :

1. Download the app package for your platform
2. Download models.zip (universal for all platforms)
3. Download the venv-*.zip for your platform
4. Extract resource files to the app directory
5. Launch the application
详细步骤请查看 README.md

### ✨ 主要更新 / Key Changes
中文 ：

- ✅ 全新架构 ：应用包、模型、环境分离，大幅减小体积
- ✅ 自动检测 ：启动时自动检查资源，缺失时提示下载
- ✅ 灵活更新 ：代码更新无需重新下载模型和环境
- ✅ 跨平台共享 ：AI 模型所有平台通用，只需下载一次
- ✅ 下载管理器 ：新增 download_manager.py 脚本，支持自动下载资源
English :

- ✅ New Architecture : Separated app, models, and environment, significantly reduced size
- ✅ Auto Detection : Automatically checks resources on startup, prompts to download if missing
- ✅ Flexible Updates : Code updates don't require re-downloading models and environment
- ✅ Cross-platform Sharing : AI models are universal across platforms, download once
- ✅ Download Manager : Added download_manager.py script for automatic resource downloading
### 🔧 技术改进 / Technical Improvements
中文 ：

- 重构 GitHub Actions 工作流，支持并行构建多个组件
- 优化资源路径检测，优先使用外部资源
- 新增资源管理 API，前端可检查资源状态
- 改进错误提示，资源缺失时引导用户下载
English :

- Refactored GitHub Actions workflow to support parallel building of multiple components
- Optimized resource path detection, prioritizing external resources
- Added resource management API for frontend resource status checking
- Improved error messages, guiding users to download when resources are missing
### ⚠️ 注意事项 / Important Notes
中文 ：

- 首次安装必须下载 models.zip 和对应平台的 venv-*.zip
- 资源文件可以放在应用包内或同级目录
- 更新应用时只需下载新的应用包，资源文件无需重新下载
English :

- First-time installation requires downloading models.zip and the venv-*.zip for your platform
- Resource files can be placed inside the app package or in the same directory
- When updating the app, only download the new app package, resource files don't need to be re-downloaded
### 🙏 致谢 / Acknowledgments
中文 ：
感谢所有测试者和贡献者的支持！这个版本的重构让 SoundBot 更轻量、更易维护。

English :
Thanks to all testers and contributors for your support! This version's refactoring makes SoundBot lighter and more maintainable.

完整文档请查看 / Full documentation : README.md

</details>

## 0.1.0

Originally published 2026-03-22 · Prerelease / 预发布 · [Source tag / 源码标签](https://github.com/Huckrick/SoundBot/tree/0.1.0) · [Commit / 提交](https://github.com/Huckrick/SoundBot/commit/2a2351f0331c77b2a8c4f469de20f04518b4804e)

<details>
<summary>Original release notes / 原发行说明</summary>

> **Archived prerelease · 歴史的なプレリリース · 历史预发布**
>
> This page documents an early prerelease of SoundBot by Huckrick. For current Windows and macOS installers, use the latest stable release.
>
> このページは初期のプレリリースの記録です。 · 本页是早期预发布记录。请从最新稳定版页面下载 Windows 或 macOS 安装包。
>
> [Latest stable release · 最新安定版 · 最新稳定版](https://github.com/Huckrick/SoundBot/releases/latest)

🎵 SoundBot v0.1.0 Alpha 发布
✨ 新特性
AI 自然语言搜索音效
语义向量搜索（CLAP + ChromaDB）
支持多 LLM 提供商
波形可视化预览
中英文界面
📦 下载
macOS: SoundBot-0.1.0.dmg
Windows: SoundBot Setup 0.1.0.exe
⚠️ 注意事项
首次启动需要加载 AI 模型，请耐心等待 10-30 秒。

开发者: Nagisa_Huckrick (胡杨)
许可证: GPL-3.0

</details>

## Other historical source tags / 其他历史源码标签

The following tags had no published release entry in the archive snapshot. / 下列标签在归档时没有公开发行条目。

- [`v1.0.0`](https://github.com/Huckrick/SoundBot/tree/v1.0.0)
- [`v0.2.1-beta.3`](https://github.com/Huckrick/SoundBot/tree/v0.2.1-beta.3)
- [`v0.2.1-beta.2`](https://github.com/Huckrick/SoundBot/tree/v0.2.1-beta.2)
- [`v0.2.1-beta.1`](https://github.com/Huckrick/SoundBot/tree/v0.2.1-beta.1)
- [`v0.1.4`](https://github.com/Huckrick/SoundBot/tree/v0.1.4)
