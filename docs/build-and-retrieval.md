# 构建与发布 / Build and release

在目标系统上使用 Python 3.12 和 Node.js 24。macOS 在 Apple Silicon 上生成 DMG，Windows 在 x64 上生成 NSIS EXE。源码、锁文件和发布说明使用同一个版本号。运行环境的已验证范围见当次构建记录。

Use Python 3.12 and Node.js 24 on the target OS. Build the DMG on Apple Silicon and the NSIS installer on Windows x64. The source, lockfile, and release notes share one version.

```bash
python -m pip install --only-binary=av -r backend/requirements.txt -r backend/requirements-build.txt
npm ci
python scripts/build.py --skip-dependency-install
```

安装包输出到 `dist-electron/`。打包读取源码白名单和本次冻结后端。分类表可放在用户数据目录的 `ucs-keywords.xlsx`，或通过 `SOUNDBOT_UCS_PATH` 指定。音效库索引、备份和预览缓存由应用在用户数据目录创建。

Installers are written to `dist-electron/`. Packaging uses the source allowlist and the frozen backend built for the target platform. An optional keyword workbook can be placed at `ucs-keywords.xlsx` in the user data directory or configured with `SOUNDBOT_UCS_PATH`.

| 操作 / Action | 结果 / Result |
| --- | --- |
| `Validate` | 检查仓库数据、凭据、版本、代码和测试。 / Checks repository data, credentials, version metadata, and tests. |
| 手动运行 `Build Installers` / Run `Build Installers` manually | 构建选定提交并在 Actions 提供 macOS、Windows 产物。 / Builds the selected commit and makes both platform artifacts available in Actions. |
| 推送经批准的附注版本标签 / Push an approved annotated version tag | 仅当标签与源码版本一致且提交已进入默认分支时构建。两平台通过后创建 Release 草稿。 / Builds a matching tag on the default branch history and creates a release draft after both platforms pass. |
| 检查并发布草稿 / Review and publish the draft | 核对两端安装包、校验值、签名与实机验收结果，再在 Releases 确认发布。 / Review the installers, hashes, signatures, and platform results before publishing the draft. |

发行资产名称为 `SoundBot-<version>-arm64.dmg`、`SoundBot-Setup-<version>.exe` 和 `SHA256SUMS.txt`。从 Release 的版本页面复制这三个文件的链接。新版本发布后同步更新两个 README 的下载区。

The release assets are `SoundBot-<version>-arm64.dmg`, `SoundBot-Setup-<version>.exe`, and `SHA256SUMS.txt`. Use the asset links from the published version page and update the download sections in both README files.

构建验收覆盖冻结后端解码、安装包结构、实际桌面应用启动、导入、搜索和试听。对外分发前在最低支持系统上验收，记录宿主兼容性。当前本地预览签名与对外分发签名分开验收。正式包需要配置 macOS 签名和公证、Windows 签名，并在干净环境验证。

Package checks cover frozen audio decoding, installer contents, desktop startup, import, search, and playback. Before distribution, verify the minimum supported OS versions and host compatibility. Configure and verify macOS signing and notarization and Windows signing in a clean environment for a public installer.

## 后续检索扩展 / Optional search extensions

关键词检索以当前库为范围。向量索引可作为独立模块接入，按库、素材 UUID、内容指纹和模型版本管理派生数据。更新素材时仅重建受影响的索引。缺少模型或索引时，关键词检索照常工作。

本地助手沿用独立的对话接口。模型输出只表达检索意图，应用校验关键词与筛选条件后执行搜索。用分语种的查询集分别评估关键词、向量和混合检索，再决定默认策略。

Keyword search stays scoped to the current library. An optional vector index can track derived data by library, asset ID, source fingerprint, and model version. A local assistant can propose search intent, with the application validating keywords and filters before retrieval. Compare keyword, vector, and hybrid search on the same multilingual query set.
