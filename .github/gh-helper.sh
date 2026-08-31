#!/bin/bash
# GitHub CLI Helper for Easy Chatbox
# 简化 GitHub 操作的辅助脚本

set -e

REPO_NAME="lightweight-chatbot"
REPO_OWNER="${GITHUB_USER:-$(gh api user --jq .login)}"

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 打印带颜色的消息
info() { echo -e "${BLUE}ℹ${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warning() { echo -e "${YELLOW}⚠${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1"; }

# 显示帮助信息
show_help() {
    cat << EOF
🚀 GitHub CLI Helper for Easy Chatbox

用法: ./gh-helper.sh <命令> [参数]

命令:
    init            初始化 GitHub 仓库（创建远程仓库并推送）
    push            推送当前分支到远程
    pr              创建 Pull Request
    status          查看仓库状态
    issues          列出所有 issues
    issue-create    创建新 issue
    release         创建新 release
    sync            同步 fork（如果是 fork 的仓库）
    setup-secrets   设置 GitHub Secrets（用于 CI/CD）

示例:
    ./gh-helper.sh init                                    # 创建私有仓库并推送
    ./gh-helper.sh push                                     # 推送到远程
    ./gh-helper.sh pr "添加新功能"                          # 创建 PR
    ./gh-helper.sh issue-create "Bug标题"                   # 创建 issue
    ./gh-helper.sh release v0.2.0                           # 创建 release（不上传文件）
    ./gh-helper.sh release v0.2.0 Easy-Chatbox.zip          # 创建 release 并上传文件

环境变量:
    GITHUB_USER     GitHub 用户名（默认从 gh 获取）
    REPO_NAME       仓库名称（默认: lightweight-chatbot）

EOF
}

# 检查 gh CLI 是否已安装和登录
check_gh() {
    if ! command -v gh &> /dev/null; then
        error "GitHub CLI (gh) 未安装"
        info "请访问: https://cli.github.com/"
        exit 1
    fi

    if ! gh auth status &> /dev/null; then
        error "GitHub CLI 未登录"
        info "请运行: gh auth login"
        exit 1
    fi

    success "GitHub CLI 已就绪"
}

# 初始化 GitHub 仓库
init_repo() {
    info "正在初始化 GitHub 仓库..."

    # 检查是否已有远程仓库
    if git remote get-url origin &> /dev/null; then
        warning "远程仓库已存在"
        git remote -v
        return
    fi

    # 创建私有仓库
    info "正在创建私有仓库 ${REPO_OWNER}/${REPO_NAME}..."
    gh repo create "$REPO_NAME" \
        --private \
        --source=. \
        --description "轻量级多后端 AI 聊天客户端" \
        --push

    success "仓库创建成功！"
    info "仓库地址: https://github.com/${REPO_OWNER}/${REPO_NAME}"
}

# 推送到远程
push_code() {
    local branch=$(git branch --show-current)
    info "正在推送分支 '$branch' 到远程..."

    git push -u origin "$branch"

    success "推送成功！"
}

# 创建 Pull Request
create_pr() {
    local title="${1:-}"

    if [ -z "$title" ]; then
        error "请提供 PR 标题"
        info "用法: ./gh-helper.sh pr \"PR标题\""
        exit 1
    fi

    local branch=$(git branch --show-current)

    if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
        error "不能从主分支创建 PR"
        info "请先创建功能分支: git checkout -b feature/your-feature"
        exit 1
    fi

    info "正在创建 Pull Request..."

    # 推送当前分支
    git push -u origin "$branch"

    # 创建 PR
    gh pr create \
        --title "$title" \
        --body "## 变更说明

请在此描述你的变更...

## 测试情况

- [ ] 所有测试通过
- [ ] 已手动测试功能

## 相关 Issue

Closes #
" \
        --web

    success "PR 创建成功！"
}

# 查看仓库状态
show_status() {
    info "📊 仓库状态"
    echo ""

    # Git 状态
    echo "=== Git 状态 ==="
    git status -sb
    echo ""

    # 远程仓库信息
    if git remote get-url origin &> /dev/null; then
        echo "=== 远程仓库 ==="
        git remote -v
        echo ""
    fi

    # GitHub 仓库信息
    if gh repo view &> /dev/null; then
        echo "=== GitHub 信息 ==="
        gh repo view --json name,owner,description,isPrivate,url \
            --jq '"名称: \(.name)\n所有者: \(.owner.login)\n描述: \(.description)\n私有: \(.isPrivate)\nURL: \(.url)"'
        echo ""

        # PR 状态
        echo "=== Pull Requests ==="
        gh pr list --limit 5 || echo "无未完成的 PR"
        echo ""

        # Issues 状态
        echo "=== Issues ==="
        gh issue list --limit 5 || echo "无未完成的 issues"
    fi
}

# 列出 issues
list_issues() {
    info "📋 Issues 列表"
    gh issue list --limit 20
}

# 创建 issue
create_issue() {
    local title="${1:-}"

    if [ -z "$title" ]; then
        error "请提供 issue 标题"
        info "用法: ./gh-helper.sh issue-create \"Issue标题\""
        exit 1
    fi

    info "正在创建 Issue..."

    gh issue create \
        --title "$title" \
        --body "## 问题描述

请在此描述问题...

## 重现步骤

1.
2.
3.

## 期望行为



## 实际行为



## 环境信息

- OS:
- Python:
- 版本:
" \
        --web

    success "Issue 创建成功！"
}

# 创建 release
create_release() {
    local version="${1:-}"
    local exe_path="${2:-}"

    if [ -z "$version" ]; then
        error "请提供版本号"
        info "用法: ./gh-helper.sh release v0.2.0 [exe文件路径]"
        exit 1
    fi

    # 确保版本号以 v 开头
    if [[ ! "$version" =~ ^v ]]; then
        version="v${version}"
    fi

    info "正在创建 Release $version..."

    # 创建 tag
    git tag -a "$version" -m "Release $version"
    git push origin "$version"

    # 准备 release 命令参数
    local release_args=(
        "$version"
        --title "Release $version"
        --notes "## 🎉 Release $version

### 📦 下载

- **Windows 用户（推荐）**: 下载 \`Easy-Chatbox-${version}.zip\` 解压后运行 \`Easy-Chatbox 启动.exe\`
- **源码运行**: 克隆仓库后运行 \`python run_chatbox.py\`

### ✨ 新特性

-

### 🐛 Bug 修复

-

### 📝 文档更新

-

### 🔧 其他改进

-

---

**系统要求**: Windows 10/11 (需要 WebView2 运行时)
**大小**: ~20MB (Windows 可执行文件)
"
        --draft
    )

    # 如果提供了 exe 路径，添加到 release
    if [ -n "$exe_path" ] && [ -f "$exe_path" ]; then
        info "准备上传文件: $exe_path"
        release_args+=("$exe_path")
    fi

    # 创建 release
    gh release create "${release_args[@]}"

    success "Release 草稿创建成功！"
    info "请访问 GitHub 编辑详情并发布"

    if [ -z "$exe_path" ]; then
        warning "未提供 exe 文件，请手动上传编译好的客户端"
        info "上传命令: gh release upload $version <文件路径>"
    fi
}

# 同步 fork
sync_fork() {
    info "正在同步 fork..."

    gh repo sync

    success "同步完成！"
}

# 设置 GitHub Secrets
setup_secrets() {
    info "设置 GitHub Secrets"
    echo ""
    warning "这将设置 CI/CD 所需的 secrets"
    echo ""

    read -p "是否继续? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "已取消"
        exit 0
    fi

    # 示例：设置 CODECOV_TOKEN
    read -p "输入 CODECOV_TOKEN (留空跳过): " codecov_token
    if [ -n "$codecov_token" ]; then
        gh secret set CODECOV_TOKEN --body "$codecov_token"
        success "CODECOV_TOKEN 设置成功"
    fi

    info "Secrets 设置完成"
}

# 主函数
main() {
    local command="${1:-help}"
    shift || true

    case "$command" in
        init)
            check_gh
            init_repo
            ;;
        push)
            check_gh
            push_code
            ;;
        pr)
            check_gh
            create_pr "$@"
            ;;
        status)
            check_gh
            show_status
            ;;
        issues)
            check_gh
            list_issues
            ;;
        issue-create)
            check_gh
            create_issue "$@"
            ;;
        release)
            check_gh
            create_release "$@"
            ;;
        sync)
            check_gh
            sync_fork
            ;;
        setup-secrets)
            check_gh
            setup_secrets
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            error "未知命令: $command"
            echo ""
            show_help
            exit 1
            ;;
    esac
}

# 运行主函数
main "$@"
