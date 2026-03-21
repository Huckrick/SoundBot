# GitHub 仓库设置指南 / GitHub Repository Setup Guide

## 📋 目录 / Table of Contents
1. [创建仓库 / Create Repository](#1-创建仓库--create-repository)
2. [安全设置 / Security Settings](#2-安全设置--security-settings)
3. [推送代码 / Push Code](#3-推送代码--push-code)
4. [创建发布 / Create Release](#4-创建发布--create-release)

---

## 1. 创建仓库 / Create Repository

### 步骤 / Steps:

1. **登录 GitHub** / Login to GitHub
   - 访问 https://github.com
   - 使用你的账号登录

2. **创建新仓库** / Create New Repository
   - 点击右上角 `+` 号 → `New repository`
   - 或访问 https://github.com/new

3. **填写仓库信息** / Fill Repository Info:
   ```
   Repository name: SoundBot
   Description: AI驱动的音效管理器 / AI-powered sound effect manager
   Visibility: Public (公开)
   ```

4. **初始化选项** / Initialization Options:
   - ❌ **不要勾选** "Add a README file"（我们已有）
   - ❌ **不要勾选** "Add .gitignore"（我们已有）
   - ❌ **不要勾选** "Choose a license"（我们已有 GPL-3.0）

5. 点击 **"Create repository"**

---

## 2. 安全设置 / Security Settings

### 2.1 分支保护 / Branch Protection

**目的：防止别人直接推送代码到 main 分支**

1. 进入仓库 → `Settings` → `Branches`
2. 点击 `Add branch protection rule`
3. 设置如下：
   ```
   Branch name pattern: main
   
   ✅ Require a pull request before merging
      - Require approvals: 1 (需要1人审核)
   
   ✅ Require status checks to pass before merging
   
   ✅ Restrict pushes that create files larger than 100 MB
   
   ✅ Allow force pushes: No
   ✅ Allow deletions: No
   ```

### 2.2 仓库权限 / Repository Permissions

**目的：控制谁可以修改代码**

1. 进入 `Settings` → `Manage access` (或 `Collaborators and teams`)
2. 默认只有你（仓库所有者）有完整权限
3. **不要添加协作者**，除非你信任他们

### 2.3 代码审查 / Code Review

**目的：确保所有更改都经过审核**

1. 进入 `Settings` → `Branches`
2. 在保护规则中启用：
   ```
   ✅ Require pull request reviews before merging
   ✅ Dismiss stale PR approvals when new commits are pushed
   ✅ Require review from Code Owners
   ```

### 2.4 安全告警 / Security Alerts

1. 进入 `Settings` → `Security` → `Security overview`
2. 启用以下功能：
   ```
   ✅ Dependabot alerts (依赖漏洞告警)
   ✅ Dependabot security updates (自动安全更新)
   ✅ Secret scanning (密钥扫描)
   ```

### 2.5 Actions 权限 / Actions Permissions

**目的：控制谁可以运行 GitHub Actions**

1. 进入 `Settings` → `Actions` → `General`
2. 设置：
   ```
   Actions permissions:
   ✅ Allow all actions and reusable workflows
   
   Workflow permissions:
   ✅ Read and write permissions
   ✅ Allow GitHub Actions to create and approve pull requests
   ```

---

## 3. 推送代码 / Push Code

创建仓库后，GitHub 会显示类似命令：

```bash
# 在本地项目目录中运行 / Run in local project directory
cd /Users/huyang/Downloads/SoundBot

# 添加远程仓库 / Add remote repository
git remote add origin https://github.com/Nagisa_Huckrick/SoundBot.git

# 确保在 main 分支 / Ensure on main branch
git branch -M main

# 推送代码 / Push code
git push -u origin main
```

**如果推送失败，可能需要先拉取：**
```bash
git pull origin main --rebase
git push -u origin main
```

---

## 4. 创建发布 / Create Release

### 4.1 创建标签 / Create Tag

```bash
# 创建版本标签 / Create version tag
git tag -a v0.1.0-alpha -m "首次发布 / First Release"

# 推送标签到 GitHub / Push tag to GitHub
git push origin v0.1.0-alpha
```

### 4.2 在 GitHub 上创建 Release

1. 进入仓库页面 → 右侧 `Releases` → `Create a new release`

2. **选择标签** / Choose a tag:
   - 选择 `v0.1.0-alpha`

3. **填写发布信息** / Fill release info:
   ```
   Release title: SoundBot v0.1.0-alpha
   
   Describe this release:
   （复制 .github/RELEASE_TEMPLATE.md 的内容）
   ```

4. **附加文件** / Attach binaries:
   - 暂时不要上传文件（GitHub Actions 会自动构建并上传）

5. 点击 **"Publish release"**

### 4.3 等待自动构建 / Wait for Auto-Build

1. 发布后会自动触发 GitHub Actions
2. 进入 `Actions` 标签查看构建进度
3. 构建完成后（约 10-20 分钟），安装包会自动添加到 Release

---

## 🔒 安全最佳实践 / Security Best Practices

### ✅ 应该做的 / DO:
- ✅ 启用分支保护，要求 PR 审核
- ✅ 定期检查 Dependabot 安全告警
- ✅ 使用强密码和双因素认证 (2FA)
- ✅ 定期审查协作者列表
- ✅ 敏感信息（API 密钥等）使用 GitHub Secrets

### ❌ 不应该做的 / DON'T:
- ❌ 不要直接推送敏感信息到代码
- ❌ 不要随意添加不信任的协作者
- ❌ 不要禁用安全扫描功能
- ❌ 不要分享你的 GitHub 个人访问令牌

---

## 🆘 常见问题 / FAQ

### Q: 别人可以修改我的代码吗？
**A:** 默认只有你可以修改。其他人需要：
1. Fork 你的仓库
2. 修改后提交 Pull Request
3. 你审核通过后才能合并

### Q: 如何防止恶意 PR？
**A:** 
1. 启用分支保护，要求审核
2. 不要自动合并 PR
3. 仔细检查每个 PR 的更改

### Q: GitHub Actions 安全吗？
**A:** 
- Actions 在隔离环境中运行
- 不要运行不信任的 Actions
- 定期检查工作流文件

### Q: 如何删除恶意评论或 Issue？
**A:** 
- 作为仓库所有者，你可以删除任何评论、Issue 或 PR
- 也可以锁定对话防止进一步评论

---

## 📞 需要帮助？/ Need Help?

- GitHub 文档: https://docs.github.com
- GitHub 社区: https://github.community
- 联系支持: https://support.github.com

---

**祝你发布顺利！/ Good luck with your release! 🎉**
