# SoundBot

**Version / 版本:** 1.0.0<br>
**Status / 状态:** Stable / 稳定版<br>

## 功能 / Features

- 来源目录、增量导入、暂停续跑与本地关键词检索。 / Source folders, incremental imports, resumable jobs, and local keyword search.
- 源文件试听、按需波形、选区效果器与 DAW 工程媒体交付。 / Source playback, on-demand waveforms, clip effects, and DAW media delivery.
- Wwise 多轨编辑、效果链、源 WAV 写回与备份恢复。 / Wwise multitrack editing, effects, source WAV writeback, and recovery.

## 安装与迁移 / Install and migrate

| 平台 / Platform | 文件 / Asset |
| --- | --- |
| macOS 14+ arm64 | DMG |
| Windows 10/11 x64 | NSIS EXE |

发布资产附带 `SHA256SUMS.txt`。升级时应用先备份 SQLite，再迁移到 v4。 / Releases include `SHA256SUMS.txt`. Upgrades back up SQLite before migrating to v4.

## 验收 / Verification

记录两平台的原生包、冻结音频矩阵、实际 Electron 试听、宿主工程及 VST3 兼容性结果。使用真实实测值更新发行说明。 / Record native package, frozen audio matrix, Electron audition, host project, and VST3 compatibility results. Use measured results in release notes.

[用户指南](../README.md) · [English guide](../README.en.md) · [GNU GPL v3 or later](../LICENSE)
