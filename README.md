# 💬 Easy Chatbox - 轻量级多后端聊天客户端

<div align="center">

**一个本地运行的 AI 聊天客户端，支持多种主流 API 协议**

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[特性](#-特性) • [快速开始](#-快速开始) • [使用说明](#-使用说明) • [开发](#-开发) • [架构](#-架构)

</div>

---

## 📋 简介

Easy Chatbox 是一个功能完整的本地 AI 聊天客户端，支持同时配置多个 API 供应商（官方或中转），让你可以在一个界面中灵活切换不同的模型。**声明**：本客户端由**Claude-Opus-5**协助开发

### 支持的协议

- 🤖 **Anthropic Messages API** - Claude 系列模型
- 💡 **OpenAI Chat Completions API** - GPT 系列及兼容接口
- ⚡ **OpenAI Responses API** - 新一代流式对话接口
- 🌟 **Google Gemini API** - Gemini 系列模型

## ✨ 特性

### 🎯 核心功能

- ✅ **多协议支持** - 一个客户端，四套接口协议随心切换
- ✅ **多供应商管理** - 同时配置多个 API 提供商，官方站或中转站均可
- ✅ **模型自由切换** - 在不同对话中使用不同模型，灵活应对各种场景
- ✅ **完整消息历史** - 本地 SQLite 数据库，所有对话永久保存
- ✅ **附件上传** - 支持图片、文档等文件上传（取决于模型能力）
- ✅ **流式输出** - 实时显示 AI 回复，体验流畅

### 🔍 增强功能

- 🌐 **联网搜索** - 集成 Tavily / Brave / Serper / SearXNG / Exa 五种搜索引擎
- 🔒 **隐私优先** - 全部数据存储在本地，不依赖云端服务
- 🚀 **独立运行** - 打包为单文件 exe，无需安装 Python 环境
- 💾 **便携设计** - 所有配置和历史在 data 目录，可整体迁移

### 🛠️ 技术特性

- ⚡ **异步架构** - FastAPI + httpx 异步处理，高性能低延迟
- 🎨 **现代界面** - 基于 Web 技术的本地界面，支持 WebView2 独立窗口
- 🧪 **完整测试** - 175+ 单元测试保证代码质量
- 📦 **简洁打包** - PyInstaller 打包，仅 20MB 的独立可执行文件

## 🚀 快速开始

### 方式一：使用打包版本（推荐）

1. 下载 `Easy-Chatbox-聊天助手` 文件夹
2. 双击运行 `Easy-Chatbox 启动.exe`
3. 首次启动需要 4-8 秒解压，之后会自动打开聊天窗口

> 💡 **提示**：Windows 11 自带 WebView2，Win10 用户如遇到窗口问题，请安装 [WebView2 运行时](https://developer.microsoft.com/microsoft-edge/webview2/)

### 方式二：从源码运行

**环境要求**

- Python 3.11 或更高版本
- [uv](https://github.com/astral-sh/uv) 包管理器（推荐）或 pip

**安装步骤**

```bash
# 克隆仓库
git clone https://github.com/lkm1729/lightweight-chatbot.git
cd lightweight-chatbot

# 使用 uv 安装依赖
uv sync

# 或使用 pip
pip install -e .

# 运行程序
python run_chatbox.py
```

程序将在 `http://127.0.0.1:8000` 启动，并自动打开客户端窗口。

## 📖 使用说明

### 初次配置

1. 🔧 点击界面中的 **「设置」** 按钮
2. 📡 选择一个协议（例如：Anthropic Messages）
3. ➕ 点击 **「新增供应商」**
4. 📝 填写配置信息：
   - **Base URL**: API 端点地址（例如：`https://api.anthropic.com`）
   - **API Key**: 你的 API 密钥
5. 🎯 添加想要使用的模型（例如：`claude-opus-5`）
6. 💾 保存配置
7. 🎉 回到主界面，在顶部下拉框选择模型，开始对话

### API 连通性测试

在供应商配置页面，每个配置旁都有 **「测试连通性」** 按钮，可以快速验证：
- ✅ Base URL 是否正确
- ✅ API Key 是否有效
- ✅ 模型是否可用

### 联网搜索配置

Easy Chatbox 支持五种搜索引擎，在设置中配置任意一个即可：

- **Tavily** - 专为 AI 优化的搜索 API
- **Brave Search** - 隐私友好的搜索引擎
- **Serper** - Google 搜索 API 封装
- **SearXNG** - 开源元搜索引擎（可自建）
- **Exa** - 语义搜索引擎

### 数据存储位置

```
data/
├── config.json         # API 配置（包含明文密钥）
├── chats.db           # 对话历史数据库
├── attachments/       # 上传的附件文件
└── startup.log        # 启动日志
```

> ⚠️ **安全提示**：`config.json` 中的 API Key 是明文保存的。分享项目前请务必删除此文件。

### 便携使用

整个程序是完全便携的：
- 📁 所有数据都在 `data` 目录
- 🚫 不写入注册表或系统目录
- 💼 可以复制到 U 盘或其他电脑直接使用
- 🗑️ 不需要时直接删除文件夹即可，无残留

## 🏗️ 架构

### 技术栈

**后端**
- **FastAPI** - 现代异步 Web 框架
- **Uvicorn** - ASGI 服务器
- **httpx** - 异步 HTTP 客户端
- **Pydantic** - 数据验证和序列化
- **SQLite** - 轻量级数据库

**前端**
- **原生 JavaScript** - 无框架依赖
- **Server-Sent Events (SSE)** - 实时流式输出
- **Fetch API** - 现代 HTTP 请求

**桌面封装**
- **pywebview** - 原生窗口包装
- **PyInstaller** - 打包为独立可执行文件

### 项目结构

```
.
├── app/                    # 后端应用
│   ├── routes/            # API 路由
│   │   ├── chat.py       # 对话接口
│   │   ├── providers.py  # 供应商管理
│   │   └── search.py     # 搜索集成
│   ├── providers/         # 协议实现
│   │   ├── anthropic.py  # Anthropic Messages
│   │   ├── openai_chat.py # OpenAI Chat
│   │   ├── openai_responses.py # OpenAI Responses
│   │   └── gemini.py     # Google Gemini
│   ├── config.py          # 配置管理
│   ├── database.py        # 数据库操作
│   └── desktop.py         # 桌面包装器
├── web/                   # 前端资源
│   ├── index.html        # 主界面
│   └── settings.html     # 设置页面
├── packaging/             # 打包配置
│   ├── build.py          # 构建脚本
│   └── Easy-Chatbox.spec # PyInstaller 配置
├── tests/                 # 测试套件
├── run_chatbox.py        # 启动入口
└── pyproject.toml        # 项目配置
```

### 核心设计

**三层配置结构**
```
协议 (Protocol)
  └─ 供应商 (Vendor)
      └─ 模型 (Model)
```

**Provider 抽象**
- 统一接口：`chat()` 和 `probe()` 方法
- SSE 事件流：`TextDelta`, `ErrorEvent`, `UsageEvent` 等
- 自动超时控制：connect 30s, total 60s-300s

**API Key 安全**
- 前端使用 `…` (U+2026) 标记遮蔽已保存的密钥
- 后端自动解析遮蔽标记，映射到存储的明文
- 响应中不返回明文密钥

## 🛠️ 开发

### 环境设置

```bash
# 安装开发依赖
uv sync --group dev

# 运行测试
pytest

# 运行测试并显示覆盖率
pytest --cov=app --cov-report=html
```

### 打包为可执行文件

```bash
# 安装打包依赖
uv sync --group packaging

# 构建 exe（使用 .venv 中的 Python）
.venv/Scripts/python.exe packaging/build.py

# 输出位置
# Easy-Chatbox-聊天助手/Easy-Chatbox 启动.exe
```

### 测试套件

项目包含 175+ 个测试，覆盖：
- ✅ 所有四种协议的实现
- ✅ API 路由和请求验证
- ✅ 配置管理和密钥解析
- ✅ 数据库操作
- ✅ 搜索引擎集成

运行测试：
```bash
pytest -v
```

### 添加新协议

1. 在 `app/providers/` 创建新的 provider 类
2. 继承 `BaseProvider` 并实现 `chat()` 和 `probe()` 方法
3. 在 `app/providers/__init__.py` 中注册
4. 在 `app/config.py` 的 `PROTOCOLS` 中添加协议元数据
5. 编写对应的测试用例

## 🐛 常见问题

<details>
<summary><b>窗口打开后一片空白</b></summary>

**原因**：系统缺少 WebView2 运行时。

**解决**：
- Win11 自带 WebView2，无需安装
- Win10 用户请安装 [WebView2 运行时](https://developer.microsoft.com/microsoft-edge/webview2/)
- 程序会自动回退到使用默认浏览器打开
</details>

<details>
<summary><b>提示「本地服务启动超时」</b></summary>

**原因**：安全软件拦截了本地端口监听。

**解决**：
- 将程序添加到安全软件的信任列表
- 确认防火墙允许程序访问 127.0.0.1
</details>

<details>
<summary><b>对话报错、连不上模型</b></summary>

**排查步骤**：
1. 在设置页面点击「测试连通性」
2. 确认 Base URL 格式正确（是否需要 `/v1` 结尾取决于供应商）
3. 验证 API Key 有效且有余额
4. 检查网络连接和代理设置
</details>

<details>
<summary><b>想自定义数据存储位置</b></summary>

设置环境变量 `CHATBOX_DATA_DIR` 指向目标目录：

```bash
# Windows
set CHATBOX_DATA_DIR=D:\MyData\Chatbox

# Linux/Mac
export CHATBOX_DATA_DIR=/path/to/data
```
</details>

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 🙏 致谢

本项目使用了以下优秀的开源项目：

- [FastAPI](https://fastapi.tiangolo.com/) - 现代 Python Web 框架
- [httpx](https://www.python-httpx.org/) - 下一代 HTTP 客户端
- [pywebview](https://pywebview.flowrl.com/) - 轻量级 WebView 包装
- [PyInstaller](https://pyinstaller.org/) - Python 打包工具

---

<div align="center">

**由 ❤️ 制作 | 保持简洁，保持强大**

</div>
