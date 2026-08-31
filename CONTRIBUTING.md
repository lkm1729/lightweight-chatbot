# 贡献指南

感谢你考虑为 Easy Chatbox 做出贡献！本文档提供了参与项目的指南。

## 🤝 如何贡献

### 报告问题

如果你发现了 bug 或有功能建议：

1. 先搜索 [Issues](../../issues) 确认问题是否已存在
2. 如果没有，创建新的 issue，包含：
   - 清晰的标题和描述
   - 重现步骤（对于 bug）
   - 期望行为和实际行为
   - 运行环境信息（操作系统、Python 版本等）
   - 相关日志或截图

### 提交代码

1. **Fork 仓库**
   ```bash
   # 点击页面右上角的 Fork 按钮
   ```

2. **克隆你的 fork**
   ```bash
   git clone https://github.com/your-username/lightweight-chatbot.git
   cd lightweight-chatbot
   ```

3. **创建分支**
   ```bash
   git checkout -b feature/your-feature-name
   # 或
   git checkout -b fix/your-bug-fix
   ```

4. **设置开发环境**
   ```bash
   # 安装依赖
   uv sync --group dev
   
   # 运行测试确保环境正常
   pytest
   ```

5. **进行修改**
   - 遵循现有代码风格
   - 为新功能添加测试
   - 更新相关文档

6. **运行测试**
   ```bash
   # 运行所有测试
   pytest -v
   
   # 检查代码覆盖率
   pytest --cov=app --cov-report=html
   ```

7. **提交更改**
   ```bash
   git add .
   git commit -m "feat: 添加新功能的简短描述"
   ```
   
   遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范：
   - `feat:` 新功能
   - `fix:` Bug 修复
   - `docs:` 文档更新
   - `test:` 测试相关
   - `refactor:` 重构
   - `chore:` 构建或辅助工具变动

8. **推送到 GitHub**
   ```bash
   git push origin feature/your-feature-name
   ```

9. **创建 Pull Request**
   - 前往 GitHub 上你的 fork
   - 点击 "Pull Request" 按钮
   - 填写 PR 描述，说明：
     - 改动内容
     - 相关的 issue 编号（如有）
     - 测试情况

## 📝 代码规范

### Python 代码风格

- 遵循 [PEP 8](https://peps.python.org/pep-0008/)
- 使用类型注解
- 函数和类添加 docstring
- 变量和函数使用描述性命名

示例：
```python
async def create_chat_session(
    user_id: str,
    model: str,
    *,
    system_prompt: str | None = None,
) -> ChatSession:
    """创建新的聊天会话。
    
    Args:
        user_id: 用户 ID
        model: 模型名称
        system_prompt: 可选的系统提示词
        
    Returns:
        创建的聊天会话对象
        
    Raises:
        ValueError: 当 model 不存在时
    """
    ...
```

### 测试要求

- 新功能必须包含测试
- Bug 修复应该包含回归测试
- 保持测试覆盖率 > 80%

测试示例：
```python
@pytest.mark.asyncio
async def test_chat_with_valid_request(mock_provider):
    """测试正常聊天请求的处理"""
    request = ChatRequest(
        messages=[{"role": "user", "content": "Hello"}],
        provider="anthropic",
        model="claude-opus-5",
    )
    
    async for event in chat_handler(request):
        assert isinstance(event, ChatEvent)
```

### 提交信息规范

提交信息格式：
```
<type>(<scope>): <subject>

<body>

<footer>
```

示例：
```
feat(providers): 添加 Gemini 2.0 Flash 支持

- 实现 Gemini 2.0 Flash 模型的接口适配
- 添加流式输出支持
- 添加相关测试用例

Closes #123
```

## 🏗️ 项目结构

```
app/
├── routes/          # API 路由层
├── providers/       # AI 协议实现
├── config.py        # 配置管理
├── database.py      # 数据库操作
└── desktop.py       # 桌面包装

tests/               # 测试文件（镜像 app/ 结构）
web/                 # 前端资源
packaging/           # 打包配置
```

## 🧪 测试指南

### 运行特定测试

```bash
# 运行单个文件
pytest tests/test_providers.py

# 运行特定测试
pytest tests/test_providers.py::test_anthropic_chat

# 运行匹配模式的测试
pytest -k "anthropic"
```

### 添加新的 Provider

1. 在 `app/providers/` 创建新文件
2. 继承 `BaseProvider`
3. 实现 `chat()` 和 `probe()` 方法
4. 在 `app/providers/__init__.py` 注册
5. 在 `tests/` 创建对应测试文件
6. 更新文档

## 📚 文档

修改代码时，请同步更新：
- README.md - 如果影响使用说明
- docstring - 对于函数和类
- CHANGELOG.md - 记录重要变更

## ❓ 需要帮助？

- 查看现有的 [Issues](../../issues) 和 [Pull Requests](../../pulls)
- 在 issue 中提问
- 查看 [README.md](README.md) 的架构部分

## 📋 Pull Request 检查清单

提交 PR 前确认：

- [ ] 代码遵循项目风格
- [ ] 添加了必要的测试
- [ ] 所有测试通过
- [ ] 更新了相关文档
- [ ] 提交信息遵循规范
- [ ] PR 描述清晰说明了改动

## 🎯 优先级领域

欢迎在以下领域贡献：

- 🐛 Bug 修复
- 📚 文档改进
- ✨ 新 AI 协议支持
- 🔍 新搜索引擎集成
- 🧪 测试覆盖率提升
- ⚡ 性能优化
- 🌍 国际化（i18n）

---

再次感谢你的贡献！🎉
