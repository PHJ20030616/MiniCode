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
│  │  OpenAI兼容优先   │    │  read/grep/glob first       │ │
│  │  Anthropic 后续   │    │  workspace 安全边界         │ │
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

## 2.1 版本边界

MiniCode 采用分阶段交付，避免在 MVP 阶段同时处理 Provider 适配、破坏性工具、权限、会话和记忆系统。

| 版本 | 核心交付 | 暂不包含 |
|------|----------|----------|
| v0.1 | OpenAI-compatible 流式对话、只读工具、ReAct 主闭环 | 写文件、shell、会话、记忆、Anthropic |
| v0.2 | 写入/编辑/shell 工具、参数级权限、会话持久化 | Anthropic、PyPI 发布 |
| v0.3 | 斜杠命令、会话和配置查看命令 | 记忆、Provider 切换、Anthropic、复杂多模态 |
| v0.4 | 记忆、多 OpenAI-compatible Provider 切换 | Anthropic、并行工具、PyPI 发布 |
| v1.0 | CI、文档、错误处理、Anthropic、发布质量 | 新增大功能 |

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
│       │   ├── shell.py              # 执行平台默认 shell 命令（v0.2）
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
│   │   ├── test_shell.py
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
       ├─ 安全检查（workspace root / 敏感文件 / 权限）
       ├─ 需要确认？→ prompt_toolkit 弹确认框
       ├─ 执行工具 → ToolResult
       ├─ 追加 tool_result 到 messages
       └─ 回到 [调用 LLM]，循环直到文本响应或达到 max_rounds(20)
```

**关键设计点：**
- 串行工具执行（MVP）：单轮多工具按顺序执行
- 并行工具执行（后续优化）：无依赖的工具并行执行
- 最大轮次限制：v0.1 默认 8 轮，从配置读取，后续可配置到 20 轮
- 用户中断：Ctrl+C 可中断当前 Agent Loop
- 空消息处理：tool_calls 为空的异常情况
- v0.1 优先保证工具调用正确性：文本回复保持流式，工具调用可先使用非流式响应或完整 tool_call 收集后执行

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

**Provider 抽象约束：**
- 内部模型可以采用 OpenAI-compatible 命名，但 Agent Loop 不应直接依赖 OpenAI SDK 返回对象。
- `system`、普通消息、assistant tool call、tool result 必须能转换到 Anthropic 风格消息结构。
- v0.1 不实现真实 Anthropic Provider，但必须用 mock contract test 验证上述转换可行，避免后续适配时大规模重构。

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

**工具分阶段列表：**

| 工具 | 版本 | 风险级别 | 功能 |
|------|------|----------|------|
| `read_file` | v0.1 | 🟢 safe | 读取 workspace 内文本文件，支持行范围 |
| `grep` | v0.1 | 🟢 safe | 搜索 workspace 内文本内容，优先 ripgrep，支持 Python fallback |
| `glob` | v0.1 | 🟢 safe | 文件模式匹配 |
| `write_file` | v0.2 | 🟡 caution | 创建/覆盖文件 |
| `edit_file` | v0.2 | 🟡 caution | 精确字符串替换（类似 Claude Code Edit） |
| `shell` | v0.2 | 🔴 dangerous | 按平台执行 shell 命令，可配置超时 |

**默认安全边界：**
- 所有文件工具默认只能访问当前 workspace root 内的路径。
- `../` 逃逸路径、指向 workspace 外的绝对路径默认拒绝。
- `.env`、SSH/Git 凭据、私钥、token 文件等敏感路径默认拒绝读取，即使是 safe 工具。
- 工具输出必须截断，避免把过长文件或搜索结果直接塞满上下文。
- PDF/图片读取不属于 v0.1，后续作为多模态扩展单独设计。

### 4.5 权限控制

```
操作分级 + 参数级判断 + 授权记忆：

🟢 safe（读 workspace 内普通文件、搜索）→ 静默执行，不询问
🟡 caution（写文件、编辑文件、覆盖已有文件）→ 询问 [Y/n/always]
🔴 dangerous（shell、删除、批量破坏性操作）→ 每次询问
⛔ deny（敏感文件、workspace 外路径、明显危险参数）→ 默认拒绝

授权模式：
- 默认模式：按上述分级询问
- --trust 模式：跳过 caution/dangerous 的确认，但不绕过 deny
- 会话记忆：用户选择 "Always allow" → 写入权限 store，按工具名和路径 pattern 匹配
- v0.1 只实现安全检查和只读工具；完整权限确认交互在 v0.2 引入
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

**斜杠命令分阶段：**

| 命令 | 版本 | 别名 | 功能 |
|------|------|------|------|
| `/quit` | v0.3 | `/exit`, `/q` | 退出 |
| `/help` | v0.3 | `/h`, `/?` | 帮助信息 |
| `/clear` | v0.3 | `/c` | 清除当前对话上下文 |
| `/session` | v0.3 | `/s` | 列表/切换/删除会话 |
| `/model` | v0.4 | `/m` | 切换模型 |
| `/provider` | v0.4 | `/p` | 切换服务提供商 |
| `/config` | v0.3 | `/cfg` | 查看配置 |
| `/memory` | v0.4 | `/mem` | 记忆增删查 |

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
updated: 2026-07-02T14:30:00
source: user
scope: global
confidence: 0.8
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

**加载策略：** v0.4 起，每次会话启动时读取 `.minicode/memory/*.md`（除 MEMORY.md），在总长度上限内合并后注入 system prompt。默认上限 8,000 字符，避免记忆污染上下文。

**冲突与时效性：**
- 每条记忆包含 `created_at`、`updated_at`、`source`、`scope`、`confidence`。
- 注入时优先选择 scope 匹配当前 workspace、`updated_at` 较新、`confidence` 较高的记忆。
- 同名或同 scope 记忆冲突时不自动删除旧记录，但 debug 日志应记录冲突和最终注入选择。

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
                               ├─ → 安全/权限检查（需要确认？）
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

## 7. 测试策略

| 测试类型 | 目标 | 开始版本 |
|----------|------|----------|
| 单元测试 | 验证配置、工具、权限、Provider 转换等独立模块 | v0.1 |
| Provider contract test | 验证内部消息模型能映射到 OpenAI-compatible 和 Anthropic 风格 | v0.1 |
| 关键路径集成测试 | 验证 Agent Loop + mock Provider + ToolRegistry + workspace 协作 | v0.1 |
| 会话集成测试 | 验证 Agent Loop 后保存、加载、继续追加消息 | v0.2 |
| 权限集成测试 | 验证权限拒绝/允许会影响实际工具执行 | v0.2 |
| 命令集成测试 | 验证 slash command 改变会话、清空上下文、查看配置 | v0.3 |
| Provider 切换集成测试 | 验证命令切换模型和 Provider 后下一轮对话使用新配置 | v0.4 |
