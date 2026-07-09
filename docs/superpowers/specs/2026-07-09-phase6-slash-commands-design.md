# Phase 6 斜杠命令系统 — 设计规格说明书

> **版本**: v0.3  
> **创建日期**: 2026-07-09  
> **状态**: 设计完成，待审核  

---

## 1. 概述

Phase 6 的目标是为 MiniCode 实现斜杠命令系统（Slash Command System），
使用户能够通过 `/command` 前缀执行内置操作，
包括退出程序、查看帮助、清除上下文、管理会话和查看配置。

### 1.1 命令清单

| 优先级 | 命令 | 别名 | 功能 |
|--------|------|------|------|
| 1 | `/quit` | `/exit`, `/q` | 退出程序 |
| 2 | `/help` | — | 显示所有可用命令及用法 |
| 3 | `/clear` | — | 清除上下文并创建新会话 |
| 4 | `/session` | — | 会话列表/切换/删除，支持交互式键盘选择 |
| 5 | `/config` | — | 查看当前配置（show 子命令） |

### 1.2 版本边界

- **v0.3 实现**：上述 5 个命令
- **v0.4 实现**：`/model`、`/provider`、`/memory`（不在本 Phase）

---

## 2. 架构设计

### 2.1 在现有架构中的位置

斜杠命令系统位于 **CLI Layer**，在 ChatApp 输入循环中作为路由分支：

```
用户输入 → ChatApp._handle_input()
              ├─ 以 "/" 开头 → CommandRouter.route(cmd_text)
              │                  ├─ 解析命令名 + 参数
              │                  ├─ 匹配 BaseCommand 实例
              │                  ├─ 构建 CommandContext
              │                  └─ 返回 CommandResult
              └─ 普通文本 → AgentLoop（现有逻辑不变）
```

### 2.2 目录结构

```
src/minicode/commands/
├── __init__.py          # 公开 API：CommandRegistry, BaseCommand, register_all_commands()
├── base.py              # BaseCommand + CommandResult + CommandContext
├── registry.py          # CommandRegistry（装饰器注册，参考 ToolRegistry 模式）
├── quit_cmd.py          # /quit, /exit, /q
├── help_cmd.py          # /help
├── clear_cmd.py         # /clear
├── session_cmd.py       # /session list|switch|delete + 交互式键盘选择
└── config_cmd.py        # /config show

tests/test_commands/
├── __init__.py
├── test_registry.py     # 注册/查找/别名/未知命令
├── test_quit.py
├── test_help.py
├── test_clear.py
├── test_session.py
├── test_config.py
└── test_integration.py  # 命令路由 + ChatApp 集成
```

### 2.3 依赖关系

```
commands/
  ├─ 依赖 config/models.py    (AppConfig, ProviderConfig)
  ├─ 依赖 session/manager.py  (SessionManager.create/load/list/delete)
  ├─ 依赖 session/models.py   (Session)
  ├─ 依赖 cli/renderer.py     (StreamingRenderer.show_info/show_error)
  ├─ 依赖 agent/loop.py       (AgentLoop.messages) [仅/clear, /session switch]
  └─ 依赖 Rich Console        (交互式列表渲染)
```

---

## 3. 数据模型

### 3.1 CommandContext

命令执行时注入的上下文对象，为 Pydantic 模型，提供命令所需的一切外部依赖。

```python
class CommandContext(BaseModel):
    """命令执行上下文。"""
    model_config = {"arbitrary_types_allowed": True}

    app_config: AppConfig
    """当前应用配置（只读）。"""
    workspace_root: Path
    """工作区根路径。"""
    session_manager: SessionManager
    """会话管理器实例。"""
    agent_loop: AgentLoop | None = None
    """当前 AgentLoop（首次对话前为 None）。"""
    renderer: StreamingRenderer
    """流式渲染器。"""
    console: Console
    """Rich Console 实例，用于交互式 UI 组件。"""
```

### 3.2 CommandResult

```python
class CommandResult(BaseModel):
    """命令执行结果。"""
    should_exit: bool = False
    """是否退出程序（仅 /quit 为 True）。"""
    message: str | None = None
    """显示给用户的文本消息。"""
    success: bool = True
    """命令是否执行成功。失败时 message 包含错误描述。"""
```

### 3.3 BaseCommand

```python
class BaseCommand(ABC):
    """斜杠命令抽象基类。"""

    name: str
    """命令主名称，不含斜杠前缀。如 'session' 对应 '/session'。"""
    aliases: list[str] = []
    """命令别名列表，如 ['s'] 对应 '/s'。"""
    description: str
    """命令简述，用于 /help 列表展示。"""
    usage: str = ""
    """命令用法示例，如 '/session switch <id>'。"""

    @abstractmethod
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """执行命令。

        Args:
            args: 命令参数（不含命令名本身）。如 '/session switch abc' → 'switch abc'
            ctx: 命令执行上下文

        Returns:
            CommandResult 描述执行结果。
        """
        ...
```

---

## 4. 命令路由

### 4.1 CommandRegistry

采用与 ToolRegistry 一致的装饰器注册模式：

```python
class CommandRegistry:
    """斜杠命令注册中心。"""

    _commands: dict[str, BaseCommand] = {}
    _aliases: dict[str, str] = {}  # alias → name

    @classmethod
    def register(cls, command: BaseCommand) -> BaseCommand:
        """注册一个命令（可用作装饰器）。"""
        ...

    @classmethod
    def find(cls, name_or_alias: str) -> BaseCommand | None:
        """按名称或别名查找命令。"""
        ...

    @classmethod
    def list_all(cls) -> list[BaseCommand]:
        """返回所有已注册的命令。"""
        ...
```

### 4.2 ChatApp 集成点

修改 `ChatApp._handle_input()` 方法（当前 `app.py:170`）：

```python
async def _handle_input(self, text: str) -> bool:
    """处理用户输入，返回是否应退出程序。

    Args:
        text: 用户输入文本。

    Returns:
        True 表示应退出程序（/quit 命令）。
    """
    if text.startswith("/"):
        return await self._handle_command(text)
    else:
        await self._handle_message(text)
        return False

async def _handle_command(self, text: str) -> bool:
    """处理斜杠命令。

    1. 解析命令名和参数
    2. 查找命令
    3. 构建上下文
    4. 执行命令
    5. 处理结果
    """
    ...
```

- `run()` 主循环中：`user_input.startswith("/")` 时走 `_handle_command()`，
  非 `/` 开头走现有 `_handle_message()`。
- 移除 `run()` 中现有的硬编码 `/exit`、`/quit` 检查（`app.py:73`），
  交由 `/quit` 命令统一处理。

### 4.3 命令名解析规则

```
输入: "/quit"        → name="quit", args=""
输入: "/session"     → name="session", args=""
输入: "/session list" → name="session", args="list"
输入: "/session switch abc123" → name="session", args="switch abc123"
输入: "/config show" → name="config", args="show"
```

解析逻辑：以第一个空格分割，前半部分去掉 `/` 前缀为命令名，后半部分为 args。

---

## 5. 各命令详细设计

### 5.1 `/quit`、`/exit`、`/q`

**类**: `QuitCommand`

**执行逻辑**:
1. 返回 `CommandResult(should_exit=True, message="再见！")`

**ChatApp 处理**:
- `_handle_command()` 收到 `should_exit=True` 后返回 `True`
- `run()` 主循环检测到 `True` 后 break

**验收标准**:
- `/quit`、`/exit`、`/q` 均能退出程序
- 退出前显示 "再见！" 消息

### 5.2 `/help`

**类**: `HelpCommand`

**执行逻辑**:
1. 调用 `CommandRegistry.list_all()` 获取所有已注册命令
2. 按名称排序
3. 构建 Rich Table：

```
╭──────────────┬──────────────────────────┬──────────────────────────╮
│ 命令          │ 别名                      │ 描述                      │
├──────────────┼──────────────────────────┼──────────────────────────┤
│ /clear       │ —                        │ 清除对话上下文并创建新会话 │
│ /config      │ —                        │ 查看或修改配置             │
│ /help        │ —                        │ 显示帮助信息              │
│ /quit        │ /exit, /q                │ 退出 MiniCode             │
│ /session     │ —                        │ 管理会话                  │
╰──────────────┴──────────────────────────┴──────────────────────────╯
```

4. 如果 `CommandRegistry` 动态注册新命令（v0.4 的 `/model`、`/provider`），
   `/help` 自动显示，无需修改代码。

**验收标准**:
- 输出包含所有 5 个已注册命令
- 别名正确显示

### 5.3 `/clear`

**类**: `ClearCommand`

**执行逻辑**:

```
1. 如果 ctx.agent_loop 存在且有消息:
   a. 获取 session_manager
   b. 如果 _current_session 存在且有消息，保存到磁盘
   c. agent_loop.messages.clear()
   d. 重新注入 system prompt（调用 agent_loop._init_system_prompt() 或等价方法）
2. 创建新会话:
   a. session_manager.create(model=..., provider=..., workspace_root=...)
   b. 更新 ChatApp._current_session
3. 返回 CommandResult(message="上下文已清除，新会话已创建。")
```

**ChatApp 配合改动**:
- `_clear_and_new_session()` 方法：供 `/clear` 命令调用
- 该方法同时更新 `_current_session` 引用

**异常处理**:
- 如果 agent_loop 为 None（首轮前执行 /clear），仅创建新会话，不操作 messages

**验收标准**:
- 执行 /clear 后 AgentLoop.messages 仅含 system prompt
- 旧会话已保存到磁盘
- 新会话已创建并可继续对话

### 5.4 `/session` — 交互式会话管理

**类**: `SessionCommand`

#### 5.4.1 子命令路由

`SessionCommand.execute(args, ctx)` 内部按 args 前缀路由：

| args 模式 | 行为 |
|-----------|------|
| 空字符串 `""` | 启动交互式方向键选择器 |
| `"list"` | 列出最近 20 条会话摘要 |
| `"switch <id>"` | 切换到指定会话 |
| `"delete <id>"` | 删除指定会话 |

#### 5.4.2 交互式选择器设计（`_interactive_select`）

**技术方案**: 使用 `prompt_toolkit` 的底层 API 构建自定义键盘导航列表。

**不使用** `prompt_toolkit.shortcuts.radiolist_dialog`（会弹出独立对话框），
而是使用 `prompt_toolkit.Application` + 自定义 Layout + KeyBindings，
在当前终端内渲染列表并捕获方向键事件。

**UI 结构**（Rich 渲染 + prompt_toolkit 键盘捕获）:

实际上更简洁的方案是使用 **questionary** 或纯 **prompt_toolkit** 键盘绑定。
考虑到项目已依赖 `prompt_toolkit`，采用以下方案：

```
实现思路：
1. 获取会话列表（最近 20 条）
2. 用 Rich 渲染静态列表，每行一个会话
3. 用 prompt_toolkit 的 session.prompt() 捕获单个按键
4. 支持:
   - ↑ (上箭头): 高亮上一条
   - ↓ (下箭头): 高亮下一条
   - Enter: 确认选择
   - Esc: 取消
5. 每次按键后重新渲染列表，更新高亮位置
```

**但** `prompt_toolkit` 在已有的 `PromptSession.prompt_async()` 内再调用 `prompt()` 会导致嵌套事件循环问题。

**最终技术方案 — 使用 Rich Live + prompt_toolkit 的 input 读取单键**:

```python
async def _interactive_select(self, sessions, ctx):
    """交互式方向键选择会话。

    注意事项:
    - 使用 Rich Live 渲染动态列表
    - 使用 prompt_toolkit 的 keys 模块读取方向键
    - 必须在 stdin 上直接读取原始输入，避免嵌套事件循环
    """
    # 备选方案: 使用 getchar / click.getchar / msvcrt 读取单键
    # 在 prompt_toolkit 环境下，可以通过 patch_stdout + 原始 stdin 读取实现
```

**简化且可靠的实现**: 使用 `prompt_toolkit` 的 `create_input` 低级 API 读取按键事件。

关键实现细节：

1. **列表交互规则**:
   - 初始高亮第一条（最近会话）
   - ↑ 向上移动，到第一条后再按 ↑ 跳到最末条
   - ↓ 向下移动，到最末条后再按 ↓ 跳回第一条
   - Enter 确认选择
   - Esc 或 Ctrl+C 取消选择

2. **列表每行内容**: `序号 · 会话名称 · 时间 · 消息数 · 模型`

3. **选中后**: 返回选中会话的 session_id

**ChatApp 配合 — `switch_session()` 方法**:

```python
async def switch_session(self, session_id: str) -> bool:
    """切换到指定会话。

    1. 保存当前会话
    2. 加载目标会话
    3. 替换 AgentLoop 的 messages
    4. 更新 _current_session
    """
```

#### 5.4.3 `/session list`

- 调用 `SessionManager.list_sessions()`
- 遍历最近 20 条，用 Rich Table 渲染
- 列：序号、会话 ID（前 8 位）、名称、时间、消息数、模型
- 当前活跃的会话用 `*` 标记

#### 5.4.4 `/session switch <id>`

- 校验 session_id 格式（32 位十六进制）
- 调用 `SessionManager.load(id)`
- 调用 `ChatApp.switch_session(id)`
- 返回结果消息

#### 5.4.5 `/session delete <id>`

- 校验 session_id 格式
- 如果删除的是当前会话（`_current_session.id == id`）:
  - 先保存当前会话
  - 删除
  - 创建新会话
  - 清空 AgentLoop 消息
- 否则直接删除
- 返回结果消息

**验收标准**:
- 交互式选择器：↑↓ 移动高亮、Enter 加载、Esc 取消
- 列表显示最近会话，按时间降序
- switch 到新会话后能继续对话
- delete 当前会话后自动创建新会话

### 5.5 `/config show`

**类**: `ConfigCommand`

**执行逻辑**:
1. 读取 `ctx.app_config`
2. 构建 Rich Table 显示：

```
╭─────────────────────┬──────────────────────────────────╮
│ 配置项               │ 值                                │
├─────────────────────┼──────────────────────────────────┤
│ 当前 Provider       │ deepseek                          │
│ 当前 Model          │ deepseek-v4-flash                 │
│ Max Tokens          │ 16384                             │
│ Agent Max Rounds    │ 20                                │
│ Stream              │ 启用                               │
│ Trust Mode          │ 关闭                               │
╰─────────────────────┴──────────────────────────────────╯

已配置的 Providers:
╭──────────────┬──────────────────────────────────┬──────────────────────╮
│ 名称          │ Base URL                          │ 模型                  │
├──────────────┼──────────────────────────────────┼──────────────────────┤
│ openai       │ https://api.openai.com/v1         │ gpt-4o, gpt-4o-mini  │
│ deepseek (*) │ https://api.deepseek.com          │ deepseek-v4-flash    │
╰──────────────┴──────────────────────────────────┴──────────────────────╯
```

3. API Key 脱敏处理：显示前 4 位 + `****` + 后 4 位
4. 当前使用的 Provider 用 `(*)` 标记

**验收标准**:
- 配置正确显示
- API Key 已脱敏
- 当前 Provider 有标记

---

## 6. 错误处理矩阵

| 场景 | 结果 |
|------|------|
| 未知命令 `/foo` | `success=False, message="未知命令：/foo。输入 /help 查看可用命令。"` |
| `/session switch` 缺少 ID | `message="用法：/session switch <会话ID>"` |
| `/session switch <无效ID>` | `message="会话 <id> 不存在。"` |
| `/session delete` 缺少 ID | `message="用法：/session delete <会话ID>"` |
| `/session delete <不存在ID>` | `message="会话 <id> 不存在，无需删除。"` |
| `/config` 未知子命令 | `message="/config show — 查看当前配置"` |
| 命令执行中异常 | `success=False, message="命令执行失败：<错误信息>"` |

---

## 7. ChatApp 改动汇总

| 改动 | 说明 |
|------|------|
| `run()` 方法 | 移除硬编码的 `/exit`/`/quit` 检查（`app.py:73`），路由到 `_handle_input()` |
| 新增 `_handle_input()` | 判断 `/` 前缀，分别调用命令路由或 AgentLoop |
| 新增 `_handle_command()` | 解析命令、查找、构建上下文、执行、处理结果 |
| 新增 `_build_command_context()` | 构建 CommandContext 实例 |
| 新增 `switch_session()` | 加载会话并替换 AgentLoop 消息 |
| 新增 `_clear_and_new_session()` | 清除消息 + 创建新会话 |

---

## 8. 测试策略

### 8.1 单元测试

| 测试文件 | 覆盖内容 |
|----------|---------|
| `test_registry.py` | 注册命令、按名称查找、按别名查找、未知命令返回 None、list_all 返回全部 |
| `test_quit.py` | execute 返回 should_exit=True、message 非空 |
| `test_help.py` | execute 返回包含所有已注册命令名的文本 |
| `test_clear.py` | 有 AgentLoop 时清空 messages、无 AgentLoop 时不报错、创建新会话 |
| `test_session.py` | list 输出格式正确、switch 正常、switch 不存在的 ID、delete 正常、delete 不存在 ID |
| `test_config.py` | show 输出包含关键配置项、API key 脱敏 |

### 8.2 集成测试

| 测试文件 | 覆盖内容 |
|----------|---------|
| `test_integration.py` | 命令路由 + ChatApp 完整链：`/help` → `/clear` → 对话 → `/session list` → `/session switch` → 对话 → `/quit` |
| `test_integration.py` | `/session delete` 当前会话后自动创建新会话 |
| `test_integration.py` | 未知命令返回友好错误 |

### 8.3 测试辅助

- `tests/test_commands/conftest.py`：提供 `mock_ctx` fixture（构造 CommandContext 用于命令单测）
- 使用 `MockProvider` + `AgentLoop` 构建可测试的上下文

---

## 9. 文件清单

### 新增文件

| 文件 | 行数估算 |
|------|---------|
| `src/minicode/commands/base.py` | ~50 行 |
| `src/minicode/commands/registry.py` | ~50 行 |
| `src/minicode/commands/quit_cmd.py` | ~30 行 |
| `src/minicode/commands/help_cmd.py` | ~60 行 |
| `src/minicode/commands/clear_cmd.py` | ~70 行 |
| `src/minicode/commands/session_cmd.py` | ~250 行 |
| `src/minicode/commands/config_cmd.py` | ~80 行 |
| `src/minicode/commands/__init__.py` | ~20 行 |
| `tests/test_commands/__init__.py` | ~2 行 |
| `tests/test_commands/conftest.py` | ~40 行 |
| `tests/test_commands/test_registry.py` | ~60 行 |
| `tests/test_commands/test_quit.py` | ~30 行 |
| `tests/test_commands/test_help.py` | ~40 行 |
| `tests/test_commands/test_clear.py` | ~60 行 |
| `tests/test_commands/test_session.py` | ~120 行 |
| `tests/test_commands/test_config.py` | ~50 行 |
| `tests/test_commands/test_integration.py` | ~100 行 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/minicode/cli/app.py` | 新增命令路由逻辑、切换会话方法 |

---

## 10. 设计决策记录

| 决策 | 选项 | 理由 |
|------|------|------|
| 命令注册模式 | 装饰器注册（同 ToolRegistry） | 保持项目一致性，可扩展，单测友好 |
| `/session` 键盘交互 | prompt_toolkit 低级 API 读键 + Rich 渲染 | 避免嵌套事件循环，保持单线程模型 |
| `/clear` 行为 | 保存旧会话 + 创建新会话 + 清空消息 | 用户选择的方案 B，保留历史可恢复 |
| 命令上下文注入 | Pydantic BaseModel + arbitrary_types_allowed | 类型安全，依赖显式注入，可测试 |
| 命令路由位置 | ChatApp._handle_input() 中新分支 | 不侵入 AgentLoop，正交设计 |
| Help 动态生成 | 遍历 CommandRegistry | 新增命令无需改 /help |
