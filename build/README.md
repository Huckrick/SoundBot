# SoundBot 构建资源 / Build resources

[中文主文档](../README.md) · [English documentation](../README.en.md) · [更新日志 / Changelog](../CHANGELOG.md)

本目录只保存 Electron 安装包所需的图标和 macOS entitlement。当前正式目标是 **Windows x64** 与 **macOS 14+ arm64**；不支持 Linux、macOS Intel 或跨系统原生打包。

This directory contains only the icons and macOS entitlement required by Electron packaging. The current official targets are **Windows x64** and **macOS 14+ arm64**; Linux, Intel macOS, and cross-OS native packaging are unsupported.

## 目录内容 / Contents

| 文件 / File | 用途 / Purpose |
| --- | --- |
| `icon.ico` | Windows NSIS 安装包与应用图标 / Windows NSIS installer and application icon |
| `icon.icns` | macOS 应用与 DMG 图标 / macOS application and DMG icon |
| `icon.png` | Electron 通用回退图标 / Generic Electron fallback icon |
| `icon_16x16.png` … `icon_1024x1024.png` | 可复现的多尺寸 PNG 资源 / Reproducible multi-size PNG assets |
| `entitlements.mac.plist` | macOS hardened runtime entitlement |

PyAV/FFmpeg 的第三方说明不放在此目录；冻结构建从 `tests/build/licenses/THIRD_PARTY_AUDIO_NOTICES.txt` 和 PyAV distribution metadata 收集许可证，并由 `tests/build/verify_frozen_bundle.py` 检查。

PyAV/FFmpeg third-party notices do not live here. The frozen build collects `tests/build/licenses/THIRD_PARTY_AUDIO_NOTICES.txt` plus the PyAV distribution metadata, and `tests/build/verify_frozen_bundle.py` verifies them.

CLAP 的来源、固定 revision 与 Apache-2.0 许可说明由 `config/model_bundle.json` 和 `tests/build/licenses/CLAP_MODEL_NOTICE.txt` 管理；生成后的 `model-manifest.json` 与说明文件会和模型一起进入应用的 `resources/models/`，而不是复制到本目录。

The CLAP source, pinned revision, and Apache-2.0 notice are controlled by `config/model_bundle.json` and `tests/build/licenses/CLAP_MODEL_NOTICE.txt`. The generated `model-manifest.json` and notice are packaged with the model under application `resources/models/`, not copied into this directory.

## 生成图标 / Regenerating icons

图标以仓库根目录的 `SoundBot.png` 为设计源文件，但该开发源图不会进入应用包。脚本需要 Pillow，生成 `.icns` 还需要 macOS 的 `iconutil`。使用仓库脚本生成所有派生尺寸：

The repository-root `SoundBot.png` is the design source, but that development asset is excluded from packages. The script requires Pillow, and `.icns` generation additionally requires macOS `iconutil`. Regenerate all derived sizes with the repository script:

```bash
python scripts/generate_icons.py
```

生成后请检查透明边缘、缩放清晰度、`icon.ico` 和 `icon.icns`，并确认没有意外修改设计源文件。不要使用在线转换器处理发布图标，因为它会让构建难以复现。

After generation, inspect transparent edges, scaling quality, `icon.ico`, and `icon.icns`, and confirm that the design source was not changed accidentally. Avoid online converters for release assets because they make the build non-reproducible.

## 原生构建 / Native builds

先根据唯一模型配置生成并校验待打包资源；该步骤会固定模型 ID/revision、生成逐文件 manifest，并复制受版本控制的 Apache-2.0 说明：

Generate and verify the resource tree from the single model configuration before packaging. This pins the model ID/revision, creates the per-file manifest, and copies the version-controlled Apache-2.0 notice:

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

`scripts/build.py` 会先验证宿主、架构、版本与双语 changelog，再验证固定 CLAP 配置、逐文件 manifest 与许可说明，冻结后端、检查 PyAV/FFmpeg/许可证、打包 Electron，并在最终应用中重新验证后端和内置模型。输出位于 `dist-electron/`；构建脚本会拒绝达到 2 GiB 的安装产物，发布汇总门禁还会对模型包、两个校验文件、DMG 与 EXE 全部执行同一上限。

`scripts/build.py` verifies the host, architecture, version, bilingual changelog, pinned CLAP configuration, per-file manifest, and license notice before freezing the backend and checking PyAV/FFmpeg/licenses. It then packages Electron and re-verifies both the backend and bundled model inside the final application. Output is written to `dist-electron/`; the build script rejects an installer artifact at 2 GiB, and the release-assembly gate applies the same limit to the model archive, both checksum files, DMG, and EXE.

构建脚本不会在 macOS 生成 Windows 包，也不会在 Windows 生成 macOS 包。发布矩阵使用 `.github/workflows/build.yml` 的原生 runner。macOS CI 会从最终应用资源目录在完全离线模式加载固定 CLAP。Windows CI 会在干净 `PATH`、无系统 FFmpeg 下验证全部 9 个扩展名、CLAP、Chroma、混合搜索和 `win-unpacked`；随后把真实 NSIS 静默安装到包含空格与中文的目录，启动已安装的 Electron，经生产 sandbox preload/IPC 启动冻结后端，并证明文件/文件夹两个原生选择器真实弹出，验证 WAV/WMA 导入、SQLite、三份固定 2,000 点波形、WMA 播放转码、内置模型加载、双索引与语义搜索。这些是发布门禁，不代表当前开发机已经完成 Windows 构建。

The script never generates a Windows package on macOS or a macOS package on Windows. The release matrix uses native runners from `.github/workflows/build.yml`. macOS CI loads the pinned CLAP model from final application resources in fully offline mode. With a clean `PATH` and no system FFmpeg, Windows CI validates all nine extensions, CLAP, Chroma, hybrid search, and `win-unpacked`; it then silently installs the actual NSIS package under a path containing spaces and Chinese characters, launches the installed Electron application, starts the frozen backend through the production sandbox preload/IPC chain, proves both native file/folder pickers appear, and verifies WAV/WMA imports, SQLite, three exact 2,000-point waveforms, WMA playback transcoding, bundled-model loading, dual indexes, and semantic search. These are release gates, not a claim that the current development machine completed a Windows build.

## 资源验证 / Asset verification

```bash
python -m unittest discover -s tests/build -p 'test_*.py' -v
python tests/build/verify_release_metadata.py
python scripts/test_pyinstaller.py
```

冻结目录可进一步检查原生架构和音频运行时：

A frozen directory can also be checked for native architecture and audio runtime:

```bash
# macOS arm64 example
python tests/build/verify_frozen_bundle.py \
  --bundle dist/backend/soundbot-backend \
  --platform macos \
  --arch arm64
```

打包结果不得包含 `.DS_Store`、根目录 `SoundBot.png`、WaveSurfer、未经 manifest 校验的源码模型副本或错误平台的后端；必须包含经过逐文件哈希校验的 `resources/models/` 与许可说明。任一构建/功能 smoke test 失败都应阻止发布。代码签名和 macOS 公证只有在维护者配置真实签名凭据后才成立；本流程不会生成或宣称使用自签名证书。

Packages must not contain `.DS_Store`, the root `SoundBot.png`, WaveSurfer, an unverified source-model copy, or a foreign-platform backend. They must contain the manifest-verified `resources/models/` tree and its license notice. Any failed build or functional smoke test must block publication. Code signing and macOS notarization apply only after the maintainer configures genuine signing credentials; this workflow neither creates nor claims a self-signed identity.

Copyright © 2026 Nagisa_Huckrick（胡杨）。项目使用 [GNU GPL v3 或更高版本](../LICENSE)。 / Licensed under the [GNU GPL v3 or later](../LICENSE).
