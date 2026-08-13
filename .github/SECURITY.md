# Security policy / 安全政策

## Supported release / 支持版本

Only the current `0.2.x` prerelease line receives security maintenance. Older
source snapshots and binaries are unsupported. / 当前仅 `0.2.x` 预发布系列接受安全维护；
更早的源码快照与二进制不再支持。

## Repository security controls / 仓库安全控制

The repository uses GitHub Secret Scanning and Push Protection, CodeQL,
dependency alerts, immutable Releases, SHA-256 inventories, artifact
attestations, pinned GitHub Actions, and a current-tree privacy gate. / 本仓库使用
GitHub Secret Scanning 与 Push Protection、CodeQL、依赖告警、不可变 Release、
SHA-256 清单、产物证明、固定 SHA 的 GitHub Actions，以及当前源码树隐私门禁。

## No external submission channel / 不开放外部提交入口

Issues, Pull Requests, Discussions, Projects, and private vulnerability reports
are disabled. The project currently does not accept external vulnerability
reports, diagnostic archives, code, audio, databases, logs, credentials, or
personal data. / Issue、Pull Request、Discussion、Project 与私密漏洞报告均已关闭；
项目当前不接收外部漏洞报告、诊断压缩包、代码、音频、数据库、日志、凭据或个人数据。

Never publish a suspected secret. Revoke or rotate the credential first, then
remove it from the current source tree. A new commit does not erase older Git
objects. / 切勿公开疑似密钥；应先吊销或轮换凭据，再从当前源码树移除。新的提交不会
抹除旧 Git 对象。
