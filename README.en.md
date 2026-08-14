# 🎵 SoundBot - AI 音效管理器 / AI Sound Effect Manager

[![License: GPL v3+](https://img.shields.io/badge/License-GPLv3%2B-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Version](https://img.shields.io/badge/version-v0.2.1--beta.3-orange.svg)](https://github.com/Huckrick/SoundBot/releases/tag/v0.2.1-beta.3)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Electron](https://img.shields.io/badge/electron-28.3.3-9feaf9.svg)](https://www.electronjs.org/)

[中文](README.md) · [Changelog](CHANGELOG.md) · [Releases](https://github.com/Huckrick/SoundBot/releases) · [GPL-3.0-or-later](LICENSE)

> Find the sound you want using natural language—a local-first, AI-powered desktop sound-effects manager.

Electron provides the desktop UI, while a local backend built with FastAPI, SQLite, PyAV, and Chroma is frozen and shipped with the application. SoundBot supports sound import, real waveforms, playback, tags, isolated projects, dual-index search, and an optional AI assistant.

***

## 📥 Download

**Latest test release:** [SoundBot v0.2.1-beta.3](https://github.com/Huckrick/SoundBot/releases/tag/v0.2.1-beta.3)

The current source version is **v0.2.1-beta.3 (prerelease)**. This release fixes file/folder imports and waveform availability caused by a sandboxed-preload crash in the installed Windows app, and bundles the pinned CLAP model, PyAV/FFmpeg audio runtime, and complete verification metadata in each installer.

| Resource | Target | Download |
| --- | --- | --- |
| macOS installer | macOS 14+ on Apple Silicon arm64 | [Download the DMG from the v0.2.1-beta.3 Release](https://github.com/Huckrick/SoundBot/releases/tag/v0.2.1-beta.3) |
| Windows installer | Windows 10/11 x64 | [Download the EXE from the v0.2.1-beta.3 Release](https://github.com/Huckrick/SoundBot/releases/tag/v0.2.1-beta.3) |
| CLAP repair/development asset | Shared by both platforms; must exactly match the application version | [Download the optional `models.zip` from the matching Release](https://github.com/Huckrick/SoundBot/releases) |

> This is a test release. Back up the SoundBot user-data directory before upgrading. The v0.2.1-beta.3 installer bundles a pinned CLAP revision, a per-file manifest, and the Apache-2.0 notice, so ordinary users do not need a separate `models.zip`. If model loading fails, files remain safely stored in SQLite and core management, waveforms, playback, tags, and keyword search remain available.

### Interface preview

![SoundBot main interface](Home1.png)

![SoundBot waveform and search interface](Home2.png)

***

## ✨ Features

| Capability | v0.2.1-beta.3 behavior |
| --- | --- |
| File import | A minimal IPC bridge lets the sandboxed preload invoke native file/folder pickers and submit absolute paths only; the backend writes SQLite first, then a cancellable, pollable persistent job creates the waveform and both vectors |
| Audio decoding | Pinned `av==18.0.0` (PyAV) and its wheel-bundled FFmpeg libraries provide one decoder without invoking a system `ffmpeg` command |
| Waveforms | The production preload bridge fetches exactly 2,000 finite, non-negative peaks in `[0,1]` per file on demand; source or algorithm changes automatically invalidate old peaks |
| Playback | Chromium-compatible formats use Electron Audio directly; incompatible containers are converted on demand to fingerprinted temporary WAV files under an LRU limit |
| Data and projects | SQLite v3 is the single source of truth for files, waveforms, and index state; files and indexes are isolated by `project_id` |
| Dual indexes | CLAP encodes the original sound; filename, logical folder, tags, UCS category, and description use a separate text-metadata index |
| Search | Audio CLAP 0.55, text metadata 0.30, and keyword/UCS 0.15, with automatic renormalization when a branch is unavailable |
| Index maintenance | Status, repair, and full rebuild operations; full rebuilds use a shadow collection and switch atomically only after validation |
| AI assistant | Local and OpenAI-compatible LLMs are supported; if AI is unavailable, the original query is used for local search |
| Privacy | No cloud telemetry; an external LLM/Embedding service receives text only after the user explicitly configures it |
| Startup diagnostics | The main window appears only after the sandboxed preload handshake and backend audio-runtime check pass; a consistently styled startup window reports failures and writes persistent diagnostics |

***

## 🚀 Installation and quick start

### Installation

1. Download the installer for your platform from the [v0.2.1-beta.3 Release](https://github.com/Huckrick/SoundBot/releases/tag/v0.2.1-beta.3).
2. On macOS, open the DMG and drag SoundBot to Applications. On Windows, run the EXE and complete the installer wizard.
3. The v0.2.1-beta.3 installer already contains the verified CLAP model, so semantic audio indexing works on first launch without separately downloading or placing `models.zip`.
4. External LLM and text-Embedding providers are optional and contact the network only after you configure one in Settings.

### Quick start

1. Create or select a project, then use Import Folder or Import Files to add your sound library.
2. Import jobs persist file records before generating waveforms and indexes; switching projects cannot redirect a running job.
3. Click a sound card to preview it and use the main waveform to select a region. Keyword search works without a model.
4. The installer bundles CLAP and enables semantic audio search. The index-status view can Repair missing items or perform a non-destructive Full rebuild.
5. If the AI assistant is unavailable, the query automatically falls back to local dual-index and keyword search.

***

## 🖥️ Supported platforms

The only official v0.2.1-beta.3 build targets are:

- Windows 10/11 x64;
- macOS 14 or later on Apple Silicon arm64.

This release does not provide Linux, macOS Intel/x64, or cross-OS builds. The PyInstaller backend contains native binaries, so Windows must be built on Windows x64 and macOS must be built on Apple Silicon macOS. The build script rejects a mismatched host or architecture before cleaning or packaging.

***

## 🎧 Audio formats, waveforms, and playback

The scanner, file picker, backend decoder, waveform generator, indexer, and playback fallback share a single audio capability table. These nine extensions are officially supported:

| Format | Extensions | Backend decoder | Default playback path |
| --- | --- | --- | --- |
| WAV | `.wav` | PyAV/FFmpeg | Original file |
| MP3 | `.mp3` | PyAV/FFmpeg | Original file |
| FLAC | `.flac` | PyAV/FFmpeg | Original file |
| AIFF | `.aiff`, `.aif` | PyAV/FFmpeg | On-demand WAV |
| Ogg Vorbis | `.ogg` | PyAV/FFmpeg | Original file |
| M4A | `.m4a` | PyAV/FFmpeg | On-demand WAV |
| AAC | `.aac` | PyAV/FFmpeg | On-demand WAV |
| WMA | `.wma` | PyAV/FFmpeg | On-demand WAV |

The frozen backend explicitly collects the PyAV extensions, `avcodec`, `avformat`, `avutil`, `swresample`, `swscale`, and third-party license notices. The Windows release gate reduces `PATH` to the system directory and decodes every format without a system FFmpeg installation.

The waveform contract is:

- `null` means not loaded; an empty array is not valid waveform data;
- a successful result is a non-empty numeric array containing finite values only, fixed at 2,000 points in `[0,1]`;
- the cache fingerprint contains file size, `mtime_ns`, and `waveform_version`, so changed files never continue displaying stale peaks;
- silence, short files, multichannel files, and long files use the same deterministic reducer; long audio uses a bounded temporary buffer instead of retaining the full decoded PCM in memory;
- the UI shows loading, failure reason, and retry states and never generates a fake random waveform; an older response cannot overwrite the state of the current selection;
- Canvas rendering respects `devicePixelRatio` and `ResizeObserver`; static waveform layers are cached offscreen while the playhead and selection are redrawn separately.

The playback WAV cache defaults to 128 files and 512 MiB. Its key includes the source fingerprint, and least-recently-used files are removed when the limit is reached.

***

## 💾 Database, migration, and artifact state

SQLite v3 is the single source of truth. `indexed_files_meta.json` no longer controls index decisions; Chroma contains rebuildable vector data only.

On the first upgrade from a legacy database:

1. SoundBot creates one immutable snapshot beside the database, named `soundmind.db.pre-v<old>-to-v3.bak`; the same migration never overwrites that snapshot;
2. a transaction retains projects, files, tags, waveforms, folder mappings, and temporary-directory settings;
3. each file receives a project-scoped UUID, a Windows/UNC-aware canonical path key, and a source fingerprint; POSIX symlink aliases are safely merged by physical path while preserving the richest UUID, tags, waveform, and artifact state; unsafe legacy project IDs are mapped to safe IDs;
4. `file_artifacts`, `index_manifests`, and `jobs` are created and SQLite `quick_check` runs;
5. if migration fails, the transaction rolls back, the original database and snapshot remain, and startup stops. The database is never cleared automatically.

Do not delete `soundmind.db` to repair an index. To roll a database back, fully quit SoundBot and back up the entire user-data directory first; do not copy an actively used SQLite file in isolation.

Each file has three separately tracked artifacts:

- `waveform`: the 2,000 peaks plus source and waveform-engine fingerprints;
- `audio_vector`: a CLAP vector generated from the original sound;
- `text_vector`: a vector generated from filename, logical folder, tags, UCS category, and description.

Artifact state is `pending`, `processing`, `ready`, `failed`, or `stale`, and failures retain a code and message. After an unexpected process stop, orphaned `processing` items return to resumable `pending` state on the next launch, while unfinished jobs are marked interrupted instead of being counted as indexed.

***

## 🧠 CLAP + text-metadata dual indexes

SoundBot does not claim that a general text embedding can replace an audio encoder:

- the audio index always uses the local `laion/larger_clap_general` CLAP model on the original sound. Processor window size determines deterministic windows, which are aggregated and normalized without random truncation;
- the text-metadata index uses CLAP's text encoder by default and can instead use LM Studio, Ollama, or an OpenAI-compatible Embeddings API;
- changing the text embedding provider, model, or dimension marks only the text index `stale` and does not damage the audio index;
- new collections always use the cosine metric and score with `similarity = 1 - distance`; legacy squared-L2 collections are never mixed with current results;
- each manifest records model ID, pinned revision, dimensions, preprocessing version, engine fingerprint, cosine metric, collection name, and revision. A fingerprint mismatch prevents old vectors from being reused.

Default hybrid-search weights are:

```text
final score = audio CLAP × 0.55 + text metadata × 0.30 + keyword/UCS × 0.15
```

If CLAP or a text service is unavailable, the remaining branches are proportionally renormalized. Each result exposes `audio_score`, `text_score`, `keyword_score`, and effective `score_weights` in `metadata`, making the ranking explainable. Cache keys contain the project, query, filters, index revision, model fingerprint, and pagination values.

### Repair missing items and full rebuild

Project index status shows ready, pending/stale, and failed counts for waveforms, the audio index, and the text index.

- **Repair missing items (`reconcile`)** compares SQLite with the active collection, recomputes pending/failed/stale items, and removes orphan vectors without clearing a working index.
- **Full rebuild (`rebuild`)** writes selected indexes into new shadow collections. SQLite switches the active collections in one transaction only after count, cosine metric, and manifest validation; a failed or cancelled rebuild keeps the previous collections active.

Import, repair, and rebuild jobs capture an immutable `project_id` when created. Switching the visible project while work is running cannot redirect SQLite, Chroma, search caches, or the AI searcher to another project.

***

## 🤖 LLM and text Embeddings

The following LLM entries are enabled in v0.2.1-beta.3:

- LM Studio;
- Ollama;
- OpenAI;
- Kimi (Moonshot);
- DeepSeek;
- SiliconFlow;
- custom OpenAI-compatible APIs.

Azure OpenAI, Google Gemini, Anthropic, and Kimi Coding are hidden in this release. Legacy configuration blocks are preserved for future migration but cannot remain selected as the active provider.

LLM calls use a shared asynchronous HTTP client with cross-chunk SSE buffering, cancellation, connect/response timeouts, and bounded retries. Normal chat does not perform a duplicate availability probe before every request; only the explicit Test Connection action probes the service. If an LLM fails, AI search sends the user's original query directly to the local dual-index/keyword search.

Text Embeddings can use:

- `default`: the bundled CLAP text encoder;
- `local`: an LM Studio or Ollama embeddings endpoint;
- `external`: OpenAI or another OpenAI-compatible embeddings endpoint.

External text Embeddings receive metadata text only. They neither replace the CLAP audio encoder nor upload original audio.

***

## 📦 Bundled model and verification

The v0.2.1-beta.3 Windows/macOS installers include the CLAP model required for semantic audio indexing. `config/model_bundle.json` is the single source for the model ID and immutable commit revision, and installed resources include a per-file SHA-256 manifest plus the full Apache-2.0 source and license notice:

```text
models/
├── model-manifest.json
├── CLAP_MODEL_NOTICE.txt
└── clap/
    ├── config.json
    ├── preprocessor_config.json
    └── ...
```

Ordinary desktop users only install the application and do not need `models.zip`. A separately published `models.zip` and `models.zip.sha256` are exact-version resources for offline repair, builds, and source development only; never mix model resources from another application version. Reinstall the same-version installer first if an installed bundled resource is damaged. In a source checkout, the resource manager can fetch the exact release and verifies the archive SHA-256, per-file SHA-256 values, pinned revision, extraction boundaries, and Zip Slip protections before atomically replacing a validated staging directory:

```bash
python scripts/download_manager.py download models --tag v0.2.1-beta.3
python scripts/download_manager.py check
```

A source checkout defaults to the repository's `models/` directory. A packaged desktop application defaults to its read-only `resources/models/` copy, not a user-data model directory. For controlled repair, development, or testing, `SOUNDBOT_MODELS_PATH` may point to an absolute directory with the layout above. This explicit value has highest priority and is authoritative: even a missing or corrupt target does not silently fall back to the bundled model. Remove an incorrect override after use. Do not use a mutable branch/tag as a release revision or an unknown bundle without a manifest, per-file checksums, and the license notice.

The backend loads CLAP only from a verified local directory and derives its engine fingerprint from the manifest revision and per-file SHA values; request paths never fall back to Hugging Face. Builds and releases generate and validate the model tree first, copy that same tree into the final application, and repeat per-file hash verification there. A model-load failure never clears data. When status polling detects changed resources and loads them successfully, durable reconcile jobs backfill `pending`, `failed`, or `stale` audio vectors and default CLAP text vectors for every project.

***

## 🔐 Secrets, privacy, and network boundaries

Electron encrypts API keys with OS-backed `safeStorage` and stores the encrypted blobs in its user-data directory. The renderer sends only `keep`, `set`, or `clear` intent:

- a stored key is never echoed; configuration reads expose only `has_api_key`;
- the Python backend receives the selected provider key in process memory only. `ai_config.json`, logs, exported configuration, and API responses do not persist or return keys or sensitive headers;
- plaintext keys in a legacy `ai_config.json` are migrated before backend startup. Existing safeStorage values win. The migration uses a temporary file and atomic replacement; any failure restores the secure-store snapshot and preserves the original file;
- if OS secure storage is unavailable, saving a key fails explicitly instead of falling back to disguised plaintext.

SoundBot contains no cloud telemetry. Paths, tags, waveforms, CLAP audio vectors, and Chroma data remain local by default. When an external LLM is selected, chat content, search context, or candidate metadata may be sent to that provider. When an external text Embedding provider is selected, metadata text used for that index is sent to the provider. Provider pricing, retention, and compliance policies are outside SoundBot's control.

***

## 🗂️ User data and logs

Default user-data directories are:

| Platform | Directory |
| --- | --- |
| macOS | `~/Library/Application Support/SoundBot/` |
| Windows | `%APPDATA%\SoundBot\` (falling back to the user's Roaming directory when `APPDATA` is absent) |

Important contents include:

```text
SoundBot/
├── db/soundmind.db                         # SQLite v3 source of truth
├── db/soundmind.db.pre-v*-to-v3.bak       # one-time migration snapshot, if needed
├── chroma_projects/<project_id>/           # project-isolated Chroma collections
├── logs/soundmind_YYYYMMDD.log             # backend log
├── temp/                                   # temporary clips and on-demand playback WAV files
├── ai_config.json                          # non-secret AI metadata configuration
├── user_config.json                       # local user settings such as the global temp directory
└── secure_secrets.json                     # safeStorage ciphertext, not plaintext keys
```

The Electron main process separately persists startup, preload, IPC, and frozen-backend output in the OS application-log directory. `soundbot-main.log` rotates to `.1` above 5 MiB, and common API-key and Bearer-token forms are redacted before writing. The diagnostics action opens the actual directory through the main process, so users do not need to guess an installation path.

Tests and portable diagnostics can override the data directory with `SOUNDBOT_USER_DATA_DIR`. Installed models live in application resources and are not part of the user-data tree above; `SOUNDBOT_MODELS_PATH` is a separate authoritative model-directory override. Do not edit SQLite, Chroma, or secure-storage files while the application is running.

### Diagnostic sequence

If a file exists but has no waveform or search result:

1. inspect project index status to identify whether `waveform`, `audio_vector`, or `text_vector` is pending/stale/failed;
2. run Repair missing items first, then inspect job stage, progress, and last error;
3. use Full rebuild only when the metric, model, or dimension changed, or reconciliation cannot recover the index;
4. inspect the backend's `logs/soundmind_YYYYMMDD.log` and use the Electron diagnostics action to open `soundbot-main.log`; logs are for local diagnosis and should still be checked for paths and private content before sharing;
5. call `/api/v1/health`, `/api/v1/runtime/capabilities`, and `/api/v1/model/status` to verify backend identity, required PyAV/FFmpeg decoding, and optional CLAP loading separately;
6. on Windows, verify that security software did not quarantine `soundbot-backend.exe`. A system FFmpeg installation is not required.

This repository does not accept diagnostic files, audio samples, databases, API keys, or private paths. Do not submit such material through any public or private GitHub channel.

***

## 🛠️ Development environment

Recommended tools:

- Python 3.12;
- Node.js 20;
- a native host matching the target: Windows x64 or macOS 14+ on Apple Silicon.

```bash
python -m venv .venv
# macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
npm ci
```

`backend/requirements.in` lists application-owned direct dependencies, `backend/requirements.txt` is the resolved runtime lock including Chroma's required transitive packages, and build tools such as PyInstaller live separately in `backend/requirements-build.txt`.

Development mode still requires a frozen backend for the current host:

```bash
python scripts/build.py --skip-electron
npm start
```

***

## 🏗️ Native builds

The unified build script verifies release metadata, installs/checks dependencies, freezes the backend, checks PyAV/FFmpeg/licenses, validates the pinned CLAP revision and Apache-2.0 notice, builds Electron, and repeats native-backend and per-file model SHA-256 verification inside the final package.

Before an official build, generate and fully verify the local model tree from `config/model_bundle.json`. This command accepts only the immutable revision in that configuration:

```bash
python scripts/download_models.py
```

Apple Silicon macOS:

```bash
python scripts/build.py --platform macos
```

Windows x64:

```powershell
python scripts/build.py --platform windows
```

`python scripts/build.py` builds for the current supported host. `--skip-backend` and `--skip-electron` exist only for debugging with known intermediate artifacts; a release must not skip either stage. Output is written to `dist-electron/`.

This repository does not fabricate a Windows package on macOS or a macOS package on Windows. Windows results are established by the native Windows CI gate; this document does not claim that a Windows build was completed on the current development machine.

### Release gates

`.github/workflows/build.yml` creates a prerelease only after all of these checks pass:

- versions in `package.json`, `package-lock.json`, `backend/config.py`, the tag, and bilingual changelog are synchronized;
- one version-controlled model configuration pins the immutable commit revision; the build first generates and verifies its per-file manifest, Apache-2.0 notice, and `models.zip.sha256`, then embeds the same model tree in both platform applications;
- macOS arm64 and Windows x64 are frozen and packaged on their respective native runners;
- the frozen bundle contains the correct native backend, PyAV extensions, wheel-bundled FFmpeg libraries, and third-party license notices;
- both final applications repeat model-ID, revision, license-notice, and manifest per-file hash checks; publishing is rejected if any of the five Release assets reaches 2 GiB;
- Windows runs with a clean `PATH` and no system FFmpeg across WAV, MP3, FLAC, AIFF, AIF, OGG, M4A, AAC, and WMA, including paths with spaces, Chinese characters, `%`, `_`, `#`, `+`, and parentheses;
- the same Windows smoke test checks exact 2,000-point waveforms, SQLite artifacts, CLAP, cosine Chroma collections, hybrid component scores, and the WMA-to-WAV playback fallback;
- the backend inside `win-unpacked` starts again and passes a real waveform smoke test; CI then silently installs the actual NSIS package to a path containing spaces and Chinese characters, launches the installed Electron application, lets the production preload/IPC chain start its own frozen backend, proves the real file and folder native pickers appear, and verifies WAV/WMA imports, SQLite listing, three exact 2,000-point waveforms, WMA playback transcoding, bundled CLAP loading, dual indexes, and semantic search;
- the macOS DMG, packaged arm64 backend, and `app.asar` resource set pass integrity checks, and the pinned CLAP model inside application resources loads successfully in offline mode;
- any failed functional smoke test prevents the Release job from running.

The Release workflow accepts only a pushed annotated `v*` version tag and has no manual-dispatch entry point. The tag must already exist and point to a commit reachable from the default branch. Every job is bound to that same tag commit, and the complete source and renderer contracts run again before packaging. The workflow first creates a draft Release, uploads the model archive and checksum, DMG, EXE, and unified `SHA256SUMS.txt`, compares remote names, sizes, SHA-256 digests, and upload states, and produces provenance attestations before publishing. It refuses to overwrite any existing Release or draft for that tag, so reruns cannot mix old and new assets. The immutable v0.2.0 remains a historical prerelease; the current v0.2.1-beta.3 and later SemVer-suffixed versions use the prerelease channel, while stable versions use the full-release channel.

Minimal release order: run `python scripts/bump_version.py --version X.Y.Z --write`, replace the changelog placeholder, and commit; wait for `Validate / Source contracts` on main to pass; then run `git tag -a vX.Y.Z -m "SoundBot vX.Y.Z"` and `git push origin vX.Y.Z`. Do not use a lightweight tag.

***

## 🧪 Tests

Python unit and integration tests:

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python -m unittest discover -s tests/build -p 'test_*.py' -v
```

Electron/renderer contracts and syntax:

```bash
node --check main.js
node --check preload.js
node --check assets/i18n.js
node tests/frontend/check_renderer_contract.js
npx --no-install electron tests/frontend/check_electron_preload.js
```

Release metadata and the PyInstaller environment:

```bash
python tests/build/verify_release_metadata.py --expected-version 0.2.1-beta.3
python scripts/bump_version.py --version 0.2.1          # preview only
python scripts/bump_version.py --version 0.2.1 --write  # atomically sync all version sources
python scripts/test_pyinstaller.py
python scripts/test_pyinstaller.py --build
```

CI runs the frozen-backend full-format/CLAP/Chroma test through `tests/build/check_frozen_audio_matrix.py` with a controlled port and temporary user-data directory. It requires a running frozen backend and a verified CLAP model and is not intended as an ordinary source-level unit test.

***

## 🔌 Public API summary

The backend listens on `127.0.0.1` only. Electron selects a free local port if the default is occupied. Extensions should obtain the runtime address through preload/runtime config instead of hard-coding a port.

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/health` | Backend version, device, model state, and required `audio_decoder_available`; status is `degraded` when decoding is unavailable |
| `GET /api/v1/runtime/capabilities` | Path-free PyAV/FFmpeg runtime details, supported extensions, and optional semantic-search state |
| `GET /api/v1/model/status` | CLAP preload status and availability |
| `GET /api/v1/files?project_id=&limit=&cursor=` | Cursor-paginated metadata; default 200, maximum 500, no peaks |
| `GET /api/v1/files/{file_id}/waveform?project_id=` | One file's waveform |
| `POST /api/v1/waveforms/batch` | Up to 100 waveforms with per-item success/error results |
| `GET /api/v1/files/{file_id}/playback-source?project_id=` | Original path or a fingerprinted temporary WAV |
| `POST /api/v1/projects/{project_id}/imports` | Submit exactly one `folder_path` or up to 1,000 `file_paths` and receive a persistent job ID |
| `GET /api/v1/jobs/{job_id}` | Read job state, stage, total, processed count, and last error |
| `DELETE /api/v1/jobs/{job_id}` | Request cancellation for a running job |
| `GET /api/v1/projects/{project_id}/index/status` | Artifact counts and both active manifests |
| `POST /api/v1/projects/{project_id}/index/reconcile` | Non-destructive repair for missing/failed/stale items |
| `POST /api/v1/projects/{project_id}/index/rebuild` | Full rebuild through shadow collections |
| `POST /api/v1/search` | Hybrid search with explicit project, filters, and pagination |
| `GET/POST /api/v1/ai/config` | Read redacted metadata or update in-process configuration |
| `POST /api/v1/ai/chat` | AI search/chat with an explicit `project_id` |

Folder imports must also use the project-explicit route; since v0.2.0, the old `POST /api/v1/import/async` has remained only as a current-project compatibility adapter and is retained for one compatibility cycle. The compatibility path `GET /api/waveform?path=...` likewise remains for one release cycle; new callers should use the file-UUID route. Structured errors use:

```json
{
  "code": "file_not_found",
  "message": "File does not exist",
  "retryable": false,
  "details": {}
}
```

***

## ⚠️ Known limitations

- v0.2.1-beta.3 is a prerelease; packages do not yet promise production-grade code signing, notarization, or automatic updates.
- Only Windows x64 and macOS arm64 are supported. Linux and Intel Macs are outside the build, test, and support matrix.
- The CLAP model is large, and first-time indexing or CPU inference may be slow. A single model worker serializes inference to avoid resource contention.
- When a model or external service is missing, affected vectors remain pending/failed; management, decoding, waveforms, playback, and keyword search remain usable.
- Chromium's native container support differs by platform. AIFF/AIF, M4A, AAC, and WMA use temporary WAV files and therefore consume additional disk space.
- Availability, rate limits, pricing, model behavior, and data policy for external LLM/Embedding services are outside SoundBot's control.
- Opening `index.html` directly provides a UI preview only; real imports, safeStorage, and the local protocol require Electron.

***

## 📁 Repository layout

```text
SoundBot/
├── index.html                   # Electron renderer UI
├── assets/renderer/             # API, state, audio, waveform, search, project, and settings modules
├── main.js                      # Main process, backend lifecycle, protocol, safeStorage, and IPC
├── preload.js                   # Minimal contextBridge surface
├── backend/                     # FastAPI, SQLite, PyAV, CLAP, Chroma, and LLM code
├── scripts/build.py             # Native-host integrated build
├── tests/                       # Backend, integration, and security tests
└── tests/build/                 # Frozen-runtime and release gates
```

***

## 📝 About this project

- Developer: **Nagisa_Huckrick (胡杨)**
- Project focus: local-first sound-asset management, waveform preview, semantic search, and an optional AI assistant
- Development approach: the author leads product concepts, interaction design, and testing, with implementation assisted by AI coding tools

> Project quality is defined by reproducible automated builds, frozen-runtime tests, and verified release assets; a “fixed” claim that has not passed the release gates is not treated as a release result.

## 📄 License

SoundBot is licensed under the [GNU General Public License v3.0 or later](LICENSE).

```text
Copyright (C) 2026 Nagisa_Huckrick (胡杨)
```

## 🙏 Acknowledgments

### AI development collaborators

The maintainer remains responsible for SoundBot's product direction, technical decisions, code acceptance, and final releases. The following AI tools assisted with research, implementation, review, or testing at different stages. Inclusion indicates development use only; it does not imply vendor endorsement, co-authorship, or maintenance responsibility:

- [OpenAI Codex](https://openai.com/codex/): repository auditing, implementation, testing, cross-platform builds, and release workflow assistance
- [Kimi](https://www.kimi.com/) by Moonshot AI: research, solution planning, and development assistance
- [Claude](https://www.anthropic.com/claude) by Anthropic: code analysis, review, and development assistance
- [Trae](https://www.trae.ai/) and [Cursor](https://www.cursor.com/): AI coding environments and early implementation assistance

### Core technology and engineering toolchain

- [Electron](https://www.electronjs.org/) (Chromium + Node.js), [Tailwind CSS](https://tailwindcss.com/), and [Lucide](https://lucide.dev/): desktop runtime, interface styling, and icons
- [Python](https://www.python.org/), [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/), [Pydantic](https://docs.pydantic.dev/), and [HTTPX](https://www.python-httpx.org/): the typed local API and asynchronous service layer
- [SQLite](https://www.sqlite.org/) and [Chroma](https://www.trychroma.com/): local metadata, job state, and project-isolated vector indexes
- [PyTorch](https://pytorch.org/), [Hugging Face Transformers](https://huggingface.co/docs/transformers/), and [LAION CLAP](https://huggingface.co/laion/larger_clap_general): audio/text embeddings and semantic retrieval
- [NumPy](https://numpy.org/), [PyAV](https://pyav.org/), and [FFmpeg](https://ffmpeg.org/): audio decoding, resampling, waveforms, and playback fallback
- [Mutagen](https://mutagen.readthedocs.io/), [TinyTag](https://github.com/tinytag/tinytag), and [jieba](https://github.com/fxsjy/jieba): audio metadata and Chinese keyword processing
- [PyInstaller](https://pyinstaller.org/), [electron-builder](https://www.electron.build/), and [GitHub Actions](https://github.com/features/actions): native backend freezing, desktop installers, and reproducible release gates

### Taxonomy and reference data

- The [Universal Category System](https://universalcategorysystem.com/) (UCS) community: the sound-effect classification and naming system
- Bilibili author [宇宙人和太空人](https://www.bilibili.com/read/cv23153650/): the attributed source of the bundled Chinese–English UCS sound-category reference; data rights remain with the original author and the UCS community

## 🔒 Repository policy

- Downloads: [GitHub Releases](https://github.com/Huckrick/SoundBot/releases)
- Release history: [CHANGELOG.md](CHANGELOG.md)
- Policy: [CONTRIBUTING.md](.github/CONTRIBUTING.md) · [SECURITY.md](.github/SECURITY.md)

This repository is a public source, audit, and release mirror. Issues, Pull Requests, Discussions, Projects, private vulnerability reports, and other external submission channels are disabled. Support requests, code, diagnostics, and personal data are not accepted. Forking and modification remain permitted under GPL-3.0-or-later, but forks have no write access to this repository.

***

<p align="center">
  Made with ❤️ by Nagisa_Huckrick (胡杨) with AI-assisted development<br>
  使用 AI 编程工具辅助制作
</p>
