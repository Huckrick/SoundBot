# SoundBot

**Version / 版本:** 1.0.7<br>
**Status / 状态:** Stable / 稳定版<br>

## 功能 / Features

- 来源目录、增量导入、暂停续跑与本地关键词检索。 / Source folders, incremental imports, resumable jobs, and local keyword search.
- 源文件试听、按需波形、音效库试听主效果链与 DAW 选区交付。 / Source playback, on-demand waveforms, a library master effects chain, and DAW selection delivery.
- 中间件多轨编辑和独立轨道效果链，以及 Wwise 源 WAV 写回与备份恢复。 / Middleware multitrack editing with independent track effects, plus Wwise source WAV writeback and recovery.

## 安装与迁移 / Install and migrate

| 平台 / Platform | 文件 / Asset |
| --- | --- |
| macOS 14+ arm64 | DMG |
| Windows 10/11 x64 | NSIS EXE |

发布资产附带 `SHA256SUMS.txt`。升级时应用先备份 SQLite，再迁移到 v4。 / Releases include `SHA256SUMS.txt`. Upgrades back up SQLite before migrating to v4.

## 验收 / Verification

记录两平台的安装、音频格式、试听和宿主验收结果。确认 SHA-256、签名状态、对应源码及第三方许可，再由维护者确认是否发布草稿。 / Record installer, audio format, playback, and host results. Check SHA-256, signing status, corresponding source, and third-party notices before the maintainer publishes the draft.

[用户指南 / User guide](../README.md) · [发行审查 / Release review](../docs/release-review.md) · [GNU GPL v3 or later](../LICENSE)
