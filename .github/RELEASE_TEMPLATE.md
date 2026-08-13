# SoundBot v0.2.0 — Release notes and verification / 发布说明与验证

> Release publishing starts only when an existing annotated `v*` tag is pushed, and its notes are generated automatically from the matching section of `CHANGELOG.md`. This file is a bilingual human-verification and incident-recovery reference, not an alternate manual publishing path; do not mark a gate as passed without its CI evidence. / 只有推送既有的 annotated `v*` tag 才会启动发布，发布说明会从 `CHANGELOG.md` 对应版本自动生成。本文件仅是双语人工核验与故障恢复参考，不是备用的手动发布入口；没有 CI 证据时不要把门禁标为已通过。

**Status / 状态:** Prerelease / 预发布<br>
**Version / 版本:** 0.2.0<br>
**License / 许可:** GNU GPL v3

## Downloads / 下载

| Target / 目标 | Release asset / 发布文件 | Support / 支持状态 |
| --- | --- | --- |
| Windows 10/11 x64 | `SoundBot Setup 0.2.0*.exe` | Official target / 正式目标 |
| macOS 14+ Apple Silicon arm64 | `SoundBot-0.2.0*.dmg` | Official target / 正式目标 |
| Optional CLAP model / 可选 CLAP 模型 | `models.zip` + `models.zip.sha256` | Required only for semantic audio indexing / 仅语义音频索引需要 |
| Release integrity / 发布完整性 | `models.zip.sha256` + `SHA256SUMS.txt` + GitHub attestations | The checksum files verify the model archive, DMG, and EXE; attestations cover all five assets / 校验文件验证模型包、DMG 与 EXE，证明覆盖全部五个资产 |

Linux and Intel macOS are not built, tested, or supported in v0.2.0. Do not publish an artifact for those targets or relabel a foreign native build. / v0.2.0 不构建、不测试也不支持 Linux 和 Intel macOS。不得为这些目标发布文件，也不得把其他平台的原生构建改名冒充。

## Highlights / 重点变化

- A pinned PyAV runtime and its wheel-bundled FFmpeg libraries decode WAV, MP3, FLAC, AIFF, AIF, OGG, M4A, AAC, and WMA without a system FFmpeg installation. / 固定版本 PyAV 与 wheel 内置 FFmpeg 动态库统一解码 WAV、MP3、FLAC、AIFF、AIF、OGG、M4A、AAC 和 WMA，无需安装系统 FFmpeg。
- Real waveforms now contain exactly 2,000 finite peaks in `[0,1]`; `null` means not loaded, and loading, failure, cancellation, and retry are explicit. / 真实波形固定包含 2,000 个位于 `[0,1]` 的有限峰值；`null` 表示未加载，并明确展示加载、失败、取消与重试状态。
- Chromium-incompatible containers use a fingerprinted, LRU-limited WAV generated on demand while compatible formats keep direct Electron Audio playback. / Chromium 不兼容容器按需生成带指纹、受 LRU 限制的 WAV；兼容格式继续由 Electron Audio 直放。
- SQLite v3 is the source of truth for files and artifact state, with a one-time migration snapshot, physical-path alias merging, and no automatic database reset. / SQLite v3 成为文件与 artifact 状态真相源，迁移前只生成一次快照，合并物理路径别名，并禁止自动重置数据库。
- CLAP audio and text metadata use separate cosine indexes. Hybrid search weights audio 0.55, text 0.30, and keyword/UCS 0.15, renormalizing unavailable branches. / CLAP 音频与文本元数据使用独立 cosine 索引；混合搜索权重为音频 0.55、文本 0.30、关键词/UCS 0.15，不可用分支会重新归一化。
- Repair is non-destructive, and full rebuilds use validated shadow collections with atomic activation so a failed rebuild retains the previous search index. / 修复操作非破坏性，完整重建使用经过验证的影子 collection 并原子激活，因此重建失败仍保留旧搜索索引。
- Import, index maintenance, search, caches, and AI chat are project-scoped, and long-running operations expose persistent job status and cancellation. / 导入、索引维护、搜索、缓存和 AI 对话均按工程隔离，长任务提供持久化作业状态与取消能力。
- LM Studio, Ollama, OpenAI, Kimi, DeepSeek, SiliconFlow, and custom OpenAI-compatible LLMs are enabled through a shared asynchronous client. / LM Studio、Ollama、OpenAI、Kimi、DeepSeek、SiliconFlow 和自定义 OpenAI-compatible LLM 通过共享异步客户端启用。
- API keys use Electron `safeStorage`, never return to the renderer, and are hydrated into the Python backend in memory only; legacy plaintext migration is atomic and recoverable. / API 密钥使用 Electron `safeStorage`，不回传渲染层，仅注入 Python 后端内存；旧明文迁移具有原子性与可恢复性。

See the synchronized [Chinese README](https://github.com/Huckrick/SoundBot/blob/v0.2.0/README.md), [English README](https://github.com/Huckrick/SoundBot/blob/v0.2.0/README.en.md), and [changelog](https://github.com/Huckrick/SoundBot/blob/v0.2.0/CHANGELOG.md) for architecture, APIs, data locations, diagnostics, privacy boundaries, and known limitations. / 架构、API、数据位置、诊断、隐私边界和已知限制请参阅同步的[中文 README](https://github.com/Huckrick/SoundBot/blob/v0.2.0/README.md)、[英文 README](https://github.com/Huckrick/SoundBot/blob/v0.2.0/README.en.md)与[更新日志](https://github.com/Huckrick/SoundBot/blob/v0.2.0/CHANGELOG.md)。

## Before upgrading / 升级前

Quit SoundBot and back up the complete user-data directory if the library is important. On first launch, SQLite v3 creates `soundmind.db.pre-v<old>-to-v3.bak` beside the database, migrates in a transaction, and stops without clearing data if migration fails. Do not delete `soundmind.db` or Chroma directories to “repair” an index; use Repair missing items or Full rebuild. / 如果音效库数据重要，请先完全退出 SoundBot 并备份整个用户数据目录。首次启动时，SQLite v3 会在数据库旁创建 `soundmind.db.pre-v<旧版本>-to-v3.bak`，在事务中迁移，失败时停止且不会清数据。不要删除 `soundmind.db` 或 Chroma 目录来“修复”索引，应使用“修复缺失项”或“完整重建”。

Legacy squared-L2 vectors are not reused as cosine vectors. Existing metadata remains available while affected vectors are rebuilt through shadow collections. / 旧平方 L2 向量不会冒充 cosine 向量复用；受影响向量在影子 collection 重建期间，现有元数据仍然可用。

## Installation / 安装

### Windows x64

Download the NSIS `.exe`, run it, and select an installation directory. If Windows security software quarantines `soundbot-backend.exe`, restore the file only after verifying that the installer came from this release. A system FFmpeg install is not required. / 下载并运行 NSIS `.exe`，按提示选择安装目录。如果 Windows 安全软件隔离 `soundbot-backend.exe`，请先确认安装包来自本 Release 再恢复。无需安装系统 FFmpeg。

### macOS arm64

On macOS 14 or later, open the `.dmg` and drag SoundBot to Applications. This release does not promise production signing or notarization unless explicitly stated; follow macOS security prompts only after verifying the download source. Intel Macs are unsupported. / 在 macOS 14 或更高版本中打开 `.dmg` 并把 SoundBot 拖到 Applications。除非明确说明，本版本不承诺生产级签名或公证；确认下载来源后再按 macOS 安全提示操作。不支持 Intel Mac。

### Optional CLAP model / 可选 CLAP 模型

Core library, decode, waveform, playback, and keyword search work without the model. For semantic audio search, download both model assets, verify `models.zip` against `models.zip.sha256`, and install this structure in SoundBot's user-data directory (or point `SOUNDBOT_MODELS_PATH` to it): / 没有模型仍可使用基础音效库、解码、波形、播放和关键词搜索。需要语义音频搜索时，请同时下载模型包与校验文件，以 `models.zip.sha256` 验证 `models.zip`，并把下列结构安装到 SoundBot 用户数据目录（或用 `SOUNDBOT_MODELS_PATH` 指向它）：

```text
models/
├── model-manifest.json
└── clap/
    └── ...
```

The model manifest records an immutable revision and per-file SHA-256 hashes. The resource manager rejects traversal, Zip Slip, symlinks, malformed manifests, and hash mismatches before atomic replacement. / 模型 manifest 记录不可变 revision 与逐文件 SHA-256；资源管理器会在原子替换前拒绝目录穿越、Zip Slip、符号链接、异常 manifest 与哈希不匹配。

## Privacy / 隐私

SoundBot has no cloud telemetry. Audio, waveforms, CLAP audio vectors, tags, and Chroma data stay local by default. Selecting an external LLM may send chat/search context or candidate metadata to that provider; selecting an external text Embedding endpoint sends metadata text but never uses it as an audio encoder. Provider pricing, retention, and compliance policies apply. / SoundBot 不含云端遥测。音频、波形、CLAP 音频向量、标签和 Chroma 数据默认留在本机。选择外部 LLM 后，聊天/搜索上下文或候选元数据可能发给 provider；选择外部文本 Embedding 后会发送元数据文本，但不会用它代替音频编码器。对应 provider 的费用、留存与合规政策仍然适用。

Configuration reads expose only `has_api_key`. If OS secure storage is unavailable, SoundBot refuses to save a key instead of falling back to plaintext. / 配置读取只暴露 `has_api_key`；操作系统安全存储不可用时，SoundBot 会拒绝保存密钥，不会降级为明文。

## Required release evidence / 必需发布证据

Leave every item unchecked in the template. The native CI job is the evidence source. There is no manual-dispatch release path: recovery must fix the source and push a valid annotated tag through the same workflow, never bypass failed gates with a manual asset upload. Release creation must depend on all jobs and must not run after any failed smoke test. / 模板中的每项默认保持未勾选，原生 CI 作业才是证据来源。发布不存在手动触发入口：故障恢复必须修复源码并通过同一工作流推送有效 annotated tag，绝不能以手工上传资产绕过失败门禁。Release 创建必须依赖全部作业，任一 smoke test 失败后都不得运行。

- [ ] Version `0.2.0` matches `package.json`, `package-lock.json`, `backend/config.py`, tag `v0.2.0`, both READMEs, and the bilingual changelog. / 版本 `0.2.0` 与 `package.json`、`package-lock.json`、`backend/config.py`、tag `v0.2.0`、两份 README 和双语 changelog 一致。
- [ ] The CLAP asset was downloaded from the pinned immutable revision, its per-file manifest passed, and `models.zip.sha256` matches. / CLAP 资源来自固定不可变 revision，逐文件 manifest 通过，且 `models.zip.sha256` 匹配。
- [ ] The draft Release contains exactly five assets: `models.zip`, `models.zip.sha256`, one DMG, one EXE, and `SHA256SUMS.txt`. Remote names, sizes, GitHub-provided SHA-256 digests, and `uploaded` states match the local files; both checksum files verify, and GitHub provenance attestations cover all five assets before publication. / 草稿 Release 必须恰好包含五个资产：`models.zip`、`models.zip.sha256`、一个 DMG、一个 EXE 与 `SHA256SUMS.txt`。远端名称、大小、GitHub 提供的 SHA-256 digest 和 `uploaded` 状态必须与本地文件一致；两个校验文件均通过，且公开前 GitHub provenance 证明覆盖全部五个资产。
- [ ] The macOS arm64 frozen backend starts, contains the PyAV/FFmpeg runtime and notices, the DMG passes integrity verification, and the packaged backend is arm64. / macOS arm64 冻结后端可启动，包含 PyAV/FFmpeg 运行时与许可证，DMG 通过完整性验证，且应用内后端为 arm64。
- [ ] The Windows x64 frozen backend starts with a clean `PATH` and no system FFmpeg, then decodes WAV, MP3, FLAC, AIFF, AIF, OGG, M4A, AAC, and WMA from special-character paths into exact 2,000-point waveforms. / Windows x64 冻结后端在干净 `PATH`、无系统 FFmpeg 下启动，并从特殊字符路径解码 WAV、MP3、FLAC、AIFF、AIF、OGG、M4A、AAC、WMA，生成精确 2,000 点波形。
- [ ] The same Windows matrix reaches ready waveform/audio/text artifacts, activates cosine CLAP and Chroma manifests, returns hybrid component scores, and produces a valid WMA playback WAV. / 同一 Windows 矩阵使波形/音频/文本 artifact 达到 ready，激活 cosine CLAP 与 Chroma manifest，返回混合搜索分项得分，并生成有效 WMA 播放 WAV。
- [ ] The backend inside `win-unpacked` starts and passes a real waveform test, and the NSIS installer passes archive-integrity validation. / `win-unpacked` 内后端可启动并通过真实波形测试，NSIS 安装包通过归档完整性验证。
- [ ] `app.asar` contains required Electron and renderer modules and excludes WaveSurfer, `.DS_Store`, root `SoundBot.png`, models, and source-only files. / `app.asar` 包含必要 Electron 与渲染模块，并排除 WaveSurfer、`.DS_Store`、根目录 `SoundBot.png`、模型和仅源码文件。
- [ ] Release notes were generated from `CHANGELOG.md` only after every native, functional, model, metadata, package, and installer gate passed. / 全部原生、功能、模型、元数据、应用包与安装包门禁通过后，发布说明才从 `CHANGELOG.md` 生成。

This checklist describes CI requirements; it does not claim that a Windows package was built on the maintainer's current machine. / 本清单描述 CI 要求，不表示维护者当前机器已经构建 Windows 包。

## Known limitations / 已知限制

- This release does not promise production-grade signing, notarization, or automatic updates unless explicitly stated. / 除非明确说明，本版本不承诺生产级签名、公证或自动更新。
- CLAP is a large model, and initial indexing or CPU inference can take time. / CLAP 模型体积较大，首次索引或 CPU 推理需要时间。
- Missing models or unavailable external services leave affected artifacts pending/failed but do not remove files or tags. / 模型缺失或外部服务不可用时，相关 artifact 保持 pending/failed，但不会删除文件或标签。
- AIFF/AIF, M4A, AAC, and WMA playback may consume temporary disk space because Electron uses an on-demand WAV fallback. / AIFF/AIF、M4A、AAC 和 WMA 播放可能占用临时磁盘空间，因为 Electron 使用按需 WAV 回退。
- External LLM/Embedding availability, rates, costs, and data policies are controlled by their providers. / 外部 LLM/Embedding 的可用性、限流、费用和数据政策由对应 provider 控制。

## Feedback and license / 反馈与许可

Issues and Pull Requests are disabled for this repository. After removing API keys, private paths, and database content, send test-release feedback to **Nagisa_Huckrick@yeah.net**. / 本仓库未开放 Issues 与 Pull Requests；请先移除 API 密钥、私人路径和数据库内容，再将测试版反馈发送到 **Nagisa_Huckrick@yeah.net**。

Copyright © 2026 Nagisa_Huckrick (胡杨), Nagisa_Huckrick@yeah.net. SoundBot is licensed under GNU GPL v3. / SoundBot 使用 GNU GPL v3 许可。
