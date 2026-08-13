# Changelog / 更新日志

All notable changes to SoundBot are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/). / 本文件记录 SoundBot 的重要变更，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased] / 未发布

### Notes / 说明

- No unreleased changes yet. / 暂无未发布变更。

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

- Hardened GitHub release automation by binding every job to an existing annotated tag commit, pinning official Actions to immutable SHAs, building both platforms through the same native-host script, separating model-free and CLAP-ready smoke gates, publishing only after draft asset verification, and adding checksums plus provenance attestations. / 加固 GitHub 发布自动化：所有作业绑定既有 annotated tag 的同一提交，官方 Actions 固定到不可变 SHA，双平台统一使用原生宿主构建脚本，拆分无模型与 CLAP 就绪冒烟门禁，仅在草稿资产核验后发布，并新增全资产校验和与 provenance 证明。
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

[Unreleased]: https://github.com/Huckrick/SoundBot/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Huckrick/SoundBot/compare/v0.1.4...v0.2.0
