# Security policy / 安全政策

## Supported versions / 支持版本

Security fixes are prepared for the current 1.0.x line. Check the published Releases page for the latest supported installer and its validation record. The 0.2.x prereleases are archival builds.

安全修复面向当前 1.0.x 系列。请在发行页查看最新已发布安装包和验证记录。0.2.x 预发行包已归档。 [Releases / 发行页](https://github.com/Huckrick/SoundBot/releases)

## Reporting and credentials / 报告与凭据

Use Issues or Discussions for general feedback. Keep vulnerability details, credentials, raw logs, audio, database exports, and personal paths out of public posts. Private vulnerability reporting is currently unavailable. Published advisories appear under Security on GitHub. [Repository policy](CONTRIBUTING.md)

一般反馈可通过 Issue 或讨论区交流。请勿在公开帖子中披露漏洞细节、凭据、原始日志、音频、数据库或私人路径。当前未开通私密漏洞报告。公开安全公告在 GitHub 的 Security 页面查看。 [仓库政策](CONTRIBUTING.md) · [Advisories / 安全公告](https://github.com/Huckrick/SoundBot/security/advisories)

If a credential was exposed, revoke or rotate it with its issuer. Removing it in a later commit leaves older Git objects intact.

如果凭据已泄露，请在签发服务处吊销或轮换。后续提交删除凭据并不会清除旧 Git 对象。

## Release checks / 发行检查

The release build checks source-tree hygiene, dependency advisories, installed files, native playback, and asset digests. Repository access and release settings are reviewed separately from application permissions. Signing status and the verified source and license inventory are part of release review.

发行构建检查源码树、依赖告警、安装文件、原生试听和产物校验值。仓库权限与应用权限分别核对。签名状态及对应源码和许可证清单纳入发行审查。 [Release review / 发行审查](../docs/release-review.md)
