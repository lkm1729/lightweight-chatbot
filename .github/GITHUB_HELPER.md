# 📖 GitHub Helper 使用指南

本项目提供了便捷的 GitHub 操作工具，帮助你在 Claude Code 中直接管理 GitHub 仓库，无需重复登录授权。

## 🛠️ 工具说明

### 1. GitHub CLI Helper 脚本

位置：`.github/gh-helper.sh`

这是一个 Bash 脚本，封装了常用的 GitHub CLI 操作，让你可以快速完成各种 GitHub 任务。

## 📋 前置要求

### 安装 GitHub CLI

**Windows:**
```bash
winget install GitHub.cli
# 或使用 Scoop
scoop install gh
```

**macOS:**
```bash
brew install gh
```

**Linux:**
```bash
# Debian/Ubuntu
sudo apt install gh

# Fedora/RHEL
sudo dnf install gh

# Arch Linux
sudo pacman -S github-cli
```

### 登录 GitHub CLI

首次使用需要登录（仅需一次）：

```bash
gh auth login
```

选择：
1. **GitHub.com**
2. **HTTPS**
3. **Login with a web browser** (推荐)
4. 复制显示的验证码，按回车在浏览器中完成授权

验证登录状态：
```bash
gh auth status
```

看到 `✓ Logged in to github.com` 即表示成功。

## 🚀 快速开始

### 一键初始化并推送到 GitHub

```bash
# 在项目根目录执行
.github/gh-helper.sh init
```

这个命令会：
- ✅ 在 GitHub 创建私有仓库
- ✅ 添加远程仓库地址
- ✅ 推送所有代码到 GitHub

### 查看仓库状态

```bash
.github/gh-helper.sh status
```

显示：
- Git 分支状态
- 远程仓库信息
- 待处理的 PR 和 Issues

## 📚 命令参考

### 基础操作

#### 推送代码到远程

```bash
.github/gh-helper.sh push
```

等同于 `git push -u origin <当前分支>`

#### 创建 Pull Request

```bash
# 先创建功能分支
git checkout -b feature/new-feature

# 提交改动
git add .
git commit -m "feat: 添加新功能"

# 创建 PR
.github/gh-helper.sh pr "添加新功能的标题"
```

会自动：
1. 推送当前分支
2. 打开浏览器创建 PR
3. 使用预设的 PR 模板

### Issue 管理

#### 列出所有 Issues

```bash
.github/gh-helper.sh issues
```

#### 创建新 Issue

```bash
.github/gh-helper.sh issue-create "发现的Bug标题"
```

会打开浏览器，使用预设的 Issue 模板。

### Release 管理

#### 创建新版本

```bash
# 创建 v0.2.0 版本（不上传文件）
.github/gh-helper.sh release v0.2.0

# 创建版本并上传打包好的客户端
.github/gh-helper.sh release v0.2.0 "Easy-Chatbox-v0.2.0.zip"

# 或者不带 v 前缀（自动添加）
.github/gh-helper.sh release 0.2.0 "path/to/package.zip"
```

会自动：
1. 创建 Git tag
2. 推送 tag 到远程
3. 创建 Release 草稿（带模板）
4. 如果提供了文件路径，自动上传到 Release

然后你可以在 GitHub 上编辑 Release 说明并发布。

#### 补充上传文件到已有 Release

如果创建 Release 时忘记上传文件：

```bash
# 上传文件到指定版本
gh release upload v0.2.0 Easy-Chatbox-v0.2.0.zip

# 上传多个文件
gh release upload v0.2.0 client.zip source.tar.gz
```

### Fork 同步

如果你 fork 了别人的仓库：

```bash
.github/gh-helper.sh sync
```

### 设置 CI/CD Secrets

```bash
.github/gh-helper.sh setup-secrets
```

交互式设置 GitHub Secrets（如 CODECOV_TOKEN）。

### 显示帮助

```bash
.github/gh-helper.sh help
```

## 🎯 常见工作流

### 工作流 1：功能开发

```bash
# 1. 创建功能分支
git checkout -b feature/add-new-protocol

# 2. 开发并提交
git add .
git commit -m "feat: 添加 XXX 协议支持"

# 3. 推送并创建 PR
.github/gh-helper.sh pr "添加 XXX 协议支持"

# 4. 在 GitHub 上进行 Code Review 和合并
```

### 工作流 2：Bug 修复

```bash
# 1. 创建修复分支
git checkout -b fix/connection-timeout

# 2. 修复并测试
# ... 修改代码 ...
pytest  # 运行测试

# 3. 提交
git add .
git commit -m "fix: 修复连接超时问题"

# 4. 创建 PR
.github/gh-helper.sh pr "修复连接超时问题"
```

### 工作流 3：发布新版本

```bash
# 1. 确保在 main/master 分支
git checkout master
git pull

# 2. 打包客户端
cd packaging
python build.py
cd ..

# 3. 压缩发布文件
# Windows: 右键 "Easy-Chatbox-聊天助手" 文件夹 → 压缩为 ZIP
# 或使用命令行
powershell Compress-Archive -Path "Easy-Chatbox-聊天助手" -DestinationPath "Easy-Chatbox-v0.2.0.zip"

# 4. 更新版本号（pyproject.toml）
# version = "0.2.0"

# 5. 提交版本更新
git add pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git push

# 6. 创建 Release 并上传客户端
.github/gh-helper.sh release v0.2.0 "Easy-Chatbox-v0.2.0.zip"

# 7. 在 GitHub 编辑 Release 说明并发布
```

## 🔧 直接使用 GitHub CLI

除了脚本，你也可以直接使用 `gh` 命令：

### 查看仓库信息

```bash
gh repo view
```

### 克隆仓库

```bash
gh repo clone owner/repo
```

### 管理 Issues

```bash
# 列出 issues
gh issue list

# 查看 issue 详情
gh issue view 123

# 关闭 issue
gh issue close 123

# 重开 issue
gh issue reopen 123
```

### 管理 Pull Requests

```bash
# 列出 PRs
gh pr list

# 查看 PR 详情
gh pr view 456

# 检出 PR 到本地
gh pr checkout 456

# 合并 PR
gh pr merge 456

# Review PR
gh pr review 456 --approve
gh pr review 456 --request-changes --body "需要修改..."
```

### 管理 Releases

```bash
# 列出 releases
gh release list

# 查看 release
gh release view v0.1.0

# 下载 release 资源
gh release download v0.1.0
```

### 执行 GitHub Actions

```bash
# 查看 workflow 运行状态
gh run list

# 查看特定运行的详情
gh run view 789

# 重新运行失败的 workflow
gh run rerun 789
```

## 💡 在 Claude Code 中使用

在 Claude Code 对话中，你可以直接要求执行这些操作：

**示例 1：推送代码**
> "请使用 gh-helper 推送代码到 GitHub"

**示例 2：创建 PR**
> "创建一个 PR，标题是：优化性能"

**示例 3：查看状态**
> "检查一下 GitHub 仓库的状态"

**示例 4：发布版本**
> "创建 v0.2.0 版本的 release"

Claude Code 会自动调用相应的命令完成操作。

## 🔐 安全说明

- ✅ **GitHub CLI 使用 OAuth token**，比 Personal Access Token 更安全
- ✅ **Token 存储在系统密钥链**（Windows Credential Manager / macOS Keychain）
- ✅ **Token 有完整的权限管理**，可以随时在 GitHub 设置中撤销
- ✅ **不会在代码或日志中暴露凭据**

撤销访问：
1. 访问 https://github.com/settings/apps/authorizations
2. 找到 "GitHub CLI"
3. 点击 "Revoke"

## 📝 环境变量

可选的环境变量配置：

```bash
# 设置 GitHub 用户名（默认自动获取）
export GITHUB_USER="your-username"

# 设置仓库名称（默认：lightweight-chatbot）
export REPO_NAME="your-repo-name"
```

## 🆘 故障排除

### 问题 1：gh 命令不存在

**解决**：安装 GitHub CLI
```bash
winget install GitHub.cli
```

### 问题 2：gh 未登录

**解决**：运行登录命令
```bash
gh auth login
```

### 问题 3：权限不足

**解决**：重新登录并授予必要的权限
```bash
gh auth refresh -s repo,workflow
```

### 问题 4：bash 脚本无法执行（Windows）

**解决**：使用 Git Bash 或 WSL
```bash
# Git Bash (推荐)
"C:\Program Files\Git\bin\bash.exe" .github/gh-helper.sh <command>

# 或者使用 WSL
wsl bash .github/gh-helper.sh <command>
```

### 问题 5：远程仓库已存在

如果运行 `init` 时提示远程仓库已存在：

```bash
# 查看现有远程
git remote -v

# 如果不对，删除重新添加
git remote remove origin
.github/gh-helper.sh init
```

## 📖 更多资源

- [GitHub CLI 官方文档](https://cli.github.com/manual/)
- [GitHub CLI 完整命令列表](https://cli.github.com/manual/gh)
- [GitHub Actions 文档](https://docs.github.com/en/actions)

---

**提示**：这个工具集成在项目中，可以在 Claude Code 对话中随时使用，无需离开编辑器！
