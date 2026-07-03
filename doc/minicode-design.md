# MiniCode — 设计文档

> 从零构建一个简化版 Claude Code 的 Python CLI 工具，用于 AI 辅助编程。
> 开源项目，面向求职简历，注重工程质量和架构设计。

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                    CLI Layer (Rich + prompt_toolkit)      │
│  ┌──────────┐  ┌───────────┐  ┌──────────────────────┐  │
│  │ 渲染器    │  │ 输入框     │  │ 命令路由器            │  │
│  │ markdown  │  │ 补全/历史  │  │ /开头 → Command      │  │
│  │ 语法高亮  │  │ 多行输入   │  │ 否则  → Agent Loop   │  │
│  └──────────┘  └───────────┘  └──────────┬───────────┘  │
├──────────────────────────────────────────┼──────────────┤
│                    Agent Layer            │              │
│  ┌───────────────────────────────────────▼────────────┐  │
│  │              ReAct Agent Loop                       │  │
│  │  LLM ⇄ 工具调用 ⇄ 结果反馈 ⇄ 循环直到完成          │  │
│  └────────┬──────────────────────────────┬────────────┘  │
│           │                              │               │
│  ┌────────▼──────────┐    ┌──────────────▼─────────────┐ │
│  │  Provider Adapter │    │     Tool Registry           │ │
│  │  OpenAI兼容优先   │    │  read/write/grep/bash/...   │ │
│  │  Anthropic 后续   │    │  权限检查器包装             │ │
│  └───────────────────┘    └─────────────────────────────┘ │
├──────────────────────────────────────────────────────────┤
│                    Storage Layer                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐   │
│  │ Sessions  │  │ Memory   │  │ Config (多层 YAML)    │   │
│  │ JSON 文件  │  │ MD 文件   │  │ CLI > ENV > 项目 >   │   │
│  │ .minicode │  │ .minicode │  │ 全局 > 默认          │   │
│  └──────────┘  └──────────┘  └──────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

## 2. 技术栈

| 类别 | 技术 | 选型理由 |
|------|------|----------|
| 语言 | Python 3.12 | `|` 联合类型、PEP 695 泛型、更好的错误信息 |
| 包管理 | uv | 2024-2025 最火的 Python 工具链，极快，简历亮点 |
| 数据模型 | Pydantic v2 | JSON Schema 自动生成（天然适配工具参数定义），序列化零代码 |
| HTTP | httpx | 现代 async HTTP 客户端，openai/anthropic SDK 底层依赖 |
| 终端渲染 | Rich | Markdown、语法高亮、表格、Panel |
| 终端输入 | prompt_toolkit | 多行输入、自动补全、历史记录、异步模式 |
| 异步框架 | asyncio | 全链路异步（Provider、工具、流式输出） |
| 日志 | structlog | 结构化日志，现代 Python 日志标准 |
| 测试 | pytest + pytest-asyncio | 异步测试支持 |
| 类型检查 | mypy | CI 集成，严格模式 |

## 3. 目录结构

```
MiniCode/
├── pyproject.toml
├── README.md
├── .gitignore
├── .github/
│   └── workflows/
│       └── ci.yml                    # GitHub Actions CI
│
├── src/
│   └── minicode/
│       ├── __init__.py
│       ├── main.py                   # CLI 入口（Typer 解析参数）
│       │
│       ├── cli/                      # 终端 UI 层
│       │   ├── __init__.py
│       │   ├── app.py                # Rich + prompt_toolkit 主循环
│       │   ├── renderer.py           # Markdown/语法高亮/代码块渲染
│       │   ├── theme.py              # 颜色/样式/图标
│       │   └── input.py              # prompt_toolkit 输入配置
│       │
│       ├── agent/                    # Agent 核心
│       │   ├── __init__.py
│       │   ├── loop.py               # ReAct Agent Loop
│       │   ├── context.py            # 上下文窗口管理（token 估算）
│       │   └── system_prompt.py      # System prompt 构建
│       │
│       ├── providers/                # LLM Provider 适配器
│       │   ├── __init__.py
│       │   ├── base.py               # BaseProvider 抽象类
│       │   ├── openai_compatible.py  # OpenAI / DeepSeek / 中转站
│       │   ├── anthropic.py          # Anthropic（后续）
│       │   └── registry.py           # Provider 注册与发现
│       │
│       ├── tools/                    # 工具系统（插件式）
│       │   ├── __init__.py
│       │   ├── base.py               # BaseTool + ToolResult
│       │   ├── registry.py           # ToolRegistry（装饰器注册）
│       │   ├── file_read.py          # 读取文件
│       │   ├── file_write.py         # 写入文件
│       │   ├── file_edit.py          # 精确字符串替换编辑
│       │   ├── bash.py               # 执行 shell 命令
│       │   ├── grep.py               # ripgrep 内容搜索
│       │   └── glob.py               # 文件模式匹配
│       │
│       ├── commands/                 # /slash 命令系统
│       │   ├── __init__.py
│       │   ├── base.py               # BaseCommand + CommandResult
│       │   ├── registry.py           # CommandRegistry（装饰器注册）
│       │   ├── new_cmd.py            # /new   - 新建会话
│       │   ├── session_cmd.py        # /session - 会话管理
│       │   ├── model_cmd.py          # /model  - 切换模型
│       │   ├── provider_cmd.py       # /provider - 切换提供商
│       │   ├── clear_cmd.py          # /clear  - 清除上下文
│       │   ├── memory_cmd.py         # /memory - 记忆管理
│       │   ├── config_cmd.py         # /config - 配置管理
│       │   ├── help_cmd.py           # /help   - 帮助
│       │   └── quit_cmd.py           # /quit / /exit - 退出
│       │
│       ├── config/                   # 配置管理
│       │   ├── __init__.py
│       │   ├── loader.py             # 多层配置加载+合并
│       │   └── models.py             # Pydantic 配置模型
│       │
│       ├── session/                  # 会话管理
│       │   ├── __init__.py
│       │   ├── manager.py            # 会话 CRUD（JSON 文件）
│       │   └── models.py             # Session / Message Pydantic 模型
│       │
│       ├── memory/                   # 记忆系统
│       │   ├── __init__.py
│       │   ├── manager.py            # 记忆读写（Markdown 文件）
│       │   └── models.py             # Memory frontmatter 模型
│       │
│       ├── permissions/              # 权限控制
│       │   ├── __init__.py
│       │   ├── checker.py            # 三级权限判断逻辑
│       │   └── store.py              # 授权记忆持久化
│       │
│       └── utils/                    # 工具函数
│           ├── __init__.py
│           ├── logger.py             # structlog 配置
│           └── exceptions.py         # 自定义异常类
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # pytest fixtures（mock provider, tmp dir）
│   ├── test_agent/
│   │   ├── test_loop.py
│   │   └── test_system_prompt.py
│   ├── test_providers/
│   │   └── test_openai_compatible.py
│   ├── test_tools/
│   │   ├── test_file_read.py
│   │   ├── test_file_write.py
│   │   ├── test_file_edit.py
│   │   ├── test_bash.py
│   │   ├── test_grep.py
│   │   └── test_glob.py
│   ├── test_commands/
│   │   └── test_command_routing.py
│   ├── test_config/
│   │   └── test_loader.py
│   ├── test_session/
│   │   └── test_manager.py
│   ├── test_memory/
│   │   └── test_manager.py
│   └── test_permissions/
│       └── test_checker.py
│
└── doc/
    └── minicode-design.md            # 本文件
```

## 4. 核心模块设计

### 4.1 Agent Loop（ReAct 循环）

```
用户输入
  ↓
[构建 messages] → system_prompt + memory + 历史消息 + user_input
  ↓
[调用 LLM] → Provider.chat(messages, tools, stream=True)
  ↓
[流式解析] ─→ 文本 delta → Rich 实时渲染到终端
  │           → tool_calls delta → 收集完整 tool_call
  ↓
[判断响应类型]
  ├─ 纯文本 → 渲染输出，等待下一轮用户输入 ✓
  └─ 含 tool_calls → 进入工具执行流程:
       ├─ 权限检查（checker.check(tool_name, args)）
       ├─ 需要确认？→ prompt_toolkit 弹确认框
       ├─ 执行工具 → ToolResult
       ├─ 追加 tool_result 到 messages
       └─ 回到 [调用 LLM]，循环直到文本响应或达到 max_rounds(20)
```

**关键设计点：**
- 串行工具执行（MVP）：单轮多工具按顺序执行
- 并行工具执行（后续优化）：无依赖的工具并行执行
- 最大轮次限制：20 轮，防止无限循环
- 用户中断：Ctrl+C 可中断当前 Agent Loop
- 空消息处理：tool_calls 为空的异常情况

### 4.2 Provider 内部统一格式

```python
# 统一的内部消息格式（OpenAI 兼容风格）
class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentBlock] | None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None  # tool 消息的发送者名

class ContentBlock(BaseModel):
    """多模态内容块"""
    type: Literal["text", "image_url"]
    text: str | None = None
    image_url: dict | None = None

class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: FunctionCall

class FunctionCall(BaseModel):
    name: str
    arguments: str  # JSON string
```

### 4.3 BaseProvider 抽象

```python
class BaseProvider(ABC):
    """LLM Provider 抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]: ...

    @abstractmethod
    async def list_models(self) -> list[str]: ...

class StreamChunk(BaseModel):
    """统一的流式响应块"""
    type: Literal["text_delta", "tool_call_delta", "done", "error"]
    text: str | None = None
    tool_call: PartialToolCall | None = None
    usage: UsageInfo | None = None
```

### 4.4 工具系统

```python
class BaseTool(ABC):
    """工具抽象基类"""
    name: str                    # "read_file"
    description: str             # 给 LLM 看的描述
    parameters: dict             # JSON Schema 参数定义
    risk_level: Literal["safe", "caution", "dangerous"]  # 🟢🟡🔴

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

class ToolResult(BaseModel):
    """工具执行结果"""
    success: bool
    content: str                 # 返回给 LLM 的文本
    error: str | None = None     # 错误信息（如有）

# 注册工具（装饰器模式）
@ToolRegistry.register
class ReadFileTool(BaseTool):
    name = "read_file"
    description = "读取文件内容..."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径"}
        },
        "required": ["file_path"]
    }
    risk_level = "safe"
```

**MVP 工具列表：**

| 工具 | 风险级别 | 功能 |
|------|----------|------|
| `read_file` | 🟢 safe | 读取文件，支持行范围、PDF/图片 |
| `write_file` | 🟡 caution | 创建/覆盖文件 |
| `edit_file` | 🟡 caution | 精确字符串替换（类似 Claude Code Edit） |
| `bash` | 🔴 dangerous | 执行 shell 命令，可配置超时 |
| `grep` | 🟢 safe | ripgrep 内容搜索，支持正则、多行、文件类型过滤 |
| `glob` | 🟢 safe | 文件模式匹配 |

### 4.5 权限控制

```
操作分级 + 授权记忆：

🟢 safe（读文件、搜索）→ 静默执行，不询问
🟡 caution（写文件、编辑文件）→ 首次询问 [Y/n/always]，会话内记住
🔴 dangerous（bash、删除）→ 每次都询问

授权模式：
- 默认模式：按上述分级询问
- --trust 模式：跳过所有询问（全局信任标志）
- 会话记忆：用户选择 "Always allow" → 写入权限 store
```

### 4.6 斜杠命令系统

```python
class CommandResult(BaseModel):
    """命令执行结果"""
    should_exit: bool = False         # 是否退出程序
    should_continue: bool = True      # 是否继续对话循环
    message: str | None = None        # 显示给用户的文本
    session_switch: str | None = None # 是否需要切换会话

class BaseCommand(ABC):
    name: str           # "/session"
    aliases: list[str]  # ["/s"]
    description: str

    @abstractmethod
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult: ...
```

**MVP 命令：**

| 命令 | 别名 | 功能 |
|------|------|------|
| `/new` | - | 创建新会话 |
| `/session` | `/s` | 列表/切换/删除会话 |
| `/model` | `/m` | 切换模型 |
| `/provider` | `/p` | 切换服务提供商 |
| `/clear` | `/c` | 清除当前对话上下文（保留 system prompt） |
| `/memory` | `/mem` | 记忆增删查 |
| `/config` | `/cfg` | 查看/修改当前配置 |
| `/help` | `/h`, `/?` | 帮助信息 |
| `/quit` | `/exit`, `/q` | 退出 |

### 4.7 配置系统

```yaml
# ~/.minicode/config.yaml（全局用户配置）
providers:
  default: "openai"
  openai:
    api_key: "${MINICODE_OPENAI_API_KEY}"  # 支持环境变量引用
    base_url: "https://api.openai.com/v1"
    models: ["gpt-4o", "gpt-4o-mini"]
  deepseek:
    api_key: "${MINICODE_DEEPSEEK_API_KEY}"
    base_url: "https://api.deepseek.com/v1"
    models: ["deepseek-chat"]
  custom: []  # 用户可通过 CLI 动态添加

model:
  default: "gpt-4o-mini"
  max_tokens: 4096

agent:
  max_rounds: 20
  stream: true

permissions:
  trust_mode: false
```

**加载优先级：** CLI 参数 > 环境变量 > `./.minicode/config.yaml` > `~/.minicode/config.yaml` > 代码默认值

### 4.8 会话管理

```python
class Session(BaseModel):
    id: str                          # UUID
    name: str                        # 会话名称（时间戳生成）
    messages: list[Message]          # 完整对话历史
    created_at: datetime
    updated_at: datetime
    model: str                       # 使用的模型
    provider: str                    # 使用的 Provider
    metadata: dict = {}              # 自定义元数据

# 存储格式
.minicode/sessions/
├── index.json                       # [{id, name, created_at, model}, ...]
├── <session-id-1>.json              # Session 完整数据
└── <session-id-2>.json
```

### 4.9 记忆系统

```markdown
<!-- .minicode/memory/user-preference.md -->
---
name: user-preference
description: 用户偏好 Python 使用 pytest 进行测试
created: 2026-07-02T14:30:00
type: user
---

用户偏好使用 pytest 作为测试框架，使用 pytest-asyncio 进行异步测试。
所有测试文件放在 tests/ 目录下。

**Why:** 用户口头告知
**How to apply:** 在讨论测试策略时默认推荐 pytest
```

**目录结构：**
```
.minicode/memory/
├── MEMORY.md           # 索引文件
├── user-preference.md  # 每条记忆一个文件
└── ...
```

**加载策略：** 每次会话启动时，读取所有 `.minicode/memory/*.md`（除 MEMORY.md），合并内容注入 system prompt。

### 4.10 日志系统

```
.minicode/logs/
├── minicode-2026-07-02.log     # 按天轮转
├── minicode-2026-07-01.log
└── ...

日志级别：
- 默认（控制台）：INFO
- --verbose：INFO + 工具调用详情
- --debug：DEBUG 全量日志写入文件
```

## 5. 核心数据流

```
用户输入 → CommandRouter
              │
              ├─ /开头 → Command.execute() → 结果渲染
              │
              └─ 普通文本 → ReAct Loop:
                               │
                               ├─ 构建 messages (system + history + user)
                               ├─ → Provider.chat(messages, tools, stream=True)
                               ├─ → 流式渲染文本 delta
                               ├─ → 收集 tool_calls
                               ├─ → 权限检查（需要确认？）
                               ├─ → 执行工具
                               ├─ → 追加 tool_result 到 messages
                               └─ → 循环直到文本响应
```

## 6. 设计决策记录

| 决策 | 选项 | 理由 |
|------|------|------|
| CLI 框架 | Rich + prompt_toolkit | 灵活可控，不会过度抽象 |
| 内部消息格式 | OpenAI 兼容协议 | 生态标准，中转站直接可用 |
| 工具架构 | 插件式注册 | 可扩展，单测友好 |
| 会话存储 | JSON 文件 | 透明，Git 友好，实现简单 |
| 记忆存储 | Markdown 文件 | Claude Code 同款，透明可编辑 |
| 配置格式 | YAML | 可读性好，支持注释 |
| Python 版本 | 3.12 | 现代特性支持，仍广泛可用 |
| 包管理 | uv | 2024-2025 最火工具，极快 |
| HTTP 客户端 | httpx | async 原生，SDK 底层依赖 |
| 日志库 | structlog | 结构化，现代标准 |
