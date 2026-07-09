# Phase 6 斜杠命令系统 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MiniCode CLI 实现完整的斜杠命令系统，支持 /quit、/help、/clear、/session、/config 五个命令，包括交互式方向键会话选择器。

**Architecture:** 在 CLI Layer 新增命令路由分支。用户输入 `/` 前缀时走 CommandRegistry 查找 → BaseCommand.execute() → CommandResult 返回。命令通过类方法注册（参考 ProviderRegistry 模式），上下文通过 CommandContext 依赖注入。

**Tech Stack:** Python 3.12+, Pydantic v2, Rich, prompt_toolkit

## Global Constraints

- 所有重要代码添加中文注释
- 用户可见文字使用中文
- 所有 prompt 使用中文
- 遵循项目现有三层架构（CLI → Agent → Storage）
- 使用 `from __future__ import annotations`
- 日志器使用 `get_logger(__name__)`
- Docstring 中文 Google 风格
- 异步执行：`BaseCommand.execute()` 为 `async` 方法
- 装饰器注册模式（参考 ToolRegistry）
- 测试镜像 `src/minicode/` 结构到 `tests/`

---

### Task 1: 命令基础设施（base.py + registry.py + __init__.py）

**Files:**
- Create: `src/minicode/commands/base.py`
- Create: `src/minicode/commands/registry.py`
- Modify: `src/minicode/commands/__init__.py`
- Create: `tests/test_commands/__init__.py`
- Create: `tests/test_commands/test_registry.py`

**Interfaces:**
- Consumes: 无（基础模块）
- Produces:
  - `CommandResult(success: bool, message: str | None, should_exit: bool)` — 命令执行结果
  - `CommandContext(app_config, workspace_root, session_manager, agent_loop, renderer, console)` — 命令执行上下文
  - `BaseCommand(name, aliases, description, usage).execute(args: str, ctx: CommandContext) -> CommandResult` — 命令抽象基类
  - `CommandRegistry.register(command: BaseCommand) -> BaseCommand` — 注册命令（装饰器）
  - `CommandRegistry.find(name_or_alias: str) -> BaseCommand | None` — 按名称/别名查找
  - `CommandRegistry.list_all() -> list[BaseCommand]` — 列出全部已注册命令

- [ ] **Step 1: 编写 `base.py` 测试（TDD）**

创建 `tests/test_commands/test_registry.py`：

```python
"""命令注册器单元测试。"""

from __future__ import annotations

import pytest

from minicode.commands.base import BaseCommand, CommandContext, CommandResult


class _StubCommand(BaseCommand):
    """用于测试的桩命令。"""

    name: str = "stub"
    description: str = "测试命令"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        return CommandResult(message=f"stub executed with: {args}")
```

先不做 registry 测试，仅验证 BaseCommand 能被子类化。

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_commands/test_registry.py -v
```

预期：ImportError，因为 `base.py` 还不存在。

- [ ] **Step 3: 实现 `base.py`**

创建 `src/minicode/commands/base.py`：

```python
"""斜杠命令的抽象基类与数据模型。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from minicode.agent import AgentLoop
    from minicode.cli.renderer import StreamingRenderer
    from minicode.config.models import AppConfig
    from minicode.session.manager import SessionManager
    from rich.console import Console


class CommandResult(BaseModel):
    """命令执行结果。

    由 BaseCommand.execute() 返回，ChatApp 根据此结果决定后续行为。
    """

    should_exit: bool = False
    """是否退出程序（仅 /quit 为 True）。"""
    message: str | None = None
    """显示给用户的文本消息。None 表示无输出。"""
    success: bool = True
    """命令是否执行成功。失败时 message 包含错误描述。"""


class CommandContext(BaseModel):
    """命令执行时注入的上下文。

    包含命令所需的所有外部依赖，通过依赖注入方式传入，
    确保命令可测试、不直接访问全局状态。

    使用 arbitrary_types_allowed 以支持 Rich Console 等非 Pydantic 类型。
    """

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


class BaseCommand(ABC):
    """斜杠命令抽象基类。

    所有命令必须继承此类并实现 execute 方法。
    命令通过 CommandRegistry.register() 注册。
    """

    name: str = ""
    """命令主名称，不含斜杠前缀。如 'session' 对应 '/session'。"""
    aliases: list[str] = []
    """命令别名列表，如 ['s'] 对应 '/s'。"""
    description: str = ""
    """命令简述，用于 /help 列表展示。"""
    usage: str = ""
    """命令用法示例，如 '/session switch <id>'。"""

    @abstractmethod
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """执行命令。

        Args:
            args: 命令参数（不含命令名本身）。
                  如 '/session switch abc' → 'switch abc'
            ctx: 命令执行上下文。

        Returns:
            CommandResult 描述执行结果。
        """
        ...
```

- [ ] **Step 4: 运行测试，确认 base.py 可导入**

```bash
uv run pytest tests/test_commands/test_registry.py -v
```

预期：PASS（无实际测试用例，仅验证导入）。

- [ ] **Step 5: 编写 registry 测试**

在 `tests/test_commands/test_registry.py` 末尾追加：

```python
class TestCommandRegistry:
    """CommandRegistry 核心功能测试。"""

    def test_register_and_find_by_name(self) -> None:
        """按名称查找已注册的命令。"""
        from minicode.commands.registry import CommandRegistry

        # 确保测试环境干净
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        cmd = _StubCommand()
        CommandRegistry.register(cmd)
        found = CommandRegistry.find("stub")
        assert found is cmd

    def test_register_and_find_by_alias(self) -> None:
        """按别名查找已注册的命令。"""
        from minicode.commands.registry import CommandRegistry

        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        cmd = _StubCommand()
        cmd.aliases = ["s"]
        CommandRegistry.register(cmd)
        found = CommandRegistry.find("s")
        assert found is cmd

    def test_find_nonexistent_returns_none(self) -> None:
        """查找不存在的命令应返回 None。"""
        from minicode.commands.registry import CommandRegistry

        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        found = CommandRegistry.find("nonexistent")
        assert found is None

    def test_register_duplicate_name_raises(self) -> None:
        """注册同名命令应抛出 ValueError。"""
        from minicode.commands.registry import CommandRegistry

        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        CommandRegistry.register(_StubCommand())
        with pytest.raises(ValueError, match="已注册"):
            CommandRegistry.register(_StubCommand())

    def test_register_duplicate_alias_raises(self) -> None:
        """注册含冲突别名的命令应抛出 ValueError。"""
        from minicode.commands.registry import CommandRegistry

        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        cmd1 = _StubCommand()
        cmd1.aliases = ["x"]
        CommandRegistry.register(cmd1)

        cmd2 = _StubCommand()
        cmd2.name = "other"
        cmd2.aliases = ["x"]
        with pytest.raises(ValueError, match="别名.*已注册"):
            CommandRegistry.register(cmd2)

    def test_list_all(self) -> None:
        """list_all 应返回所有已注册的命令。"""
        from minicode.commands.registry import CommandRegistry

        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        CommandRegistry.register(_StubCommand())
        all_cmds = CommandRegistry.list_all()
        assert len(all_cmds) == 1
        assert all_cmds[0].name == "stub"
```

- [ ] **Step 6: 运行测试，确认失败**

```bash
uv run pytest tests/test_commands/test_registry.py::TestCommandRegistry -v
```

预期：FAIL，因为 `registry.py` 还不存在。

- [ ] **Step 7: 实现 `registry.py`**

创建 `src/minicode/commands/registry.py`：

```python
"""斜杠命令注册中心。

采用类方法注册模式（参考 ProviderRegistry），
管理所有斜杠命令的注册、查找和列表功能。
"""

from __future__ import annotations

from minicode.commands.base import BaseCommand


class CommandRegistry:
    """斜杠命令注册中心。

    管理所有可用命令：
    - 通过 name 或 alias 查找命令
    - 列出所有已注册命令
    - 检测名称和别名冲突

    用法：
        cmd = QuitCommand()
        CommandRegistry.register(cmd)
        result = CommandRegistry.find("quit")
    """

    _commands: dict[str, BaseCommand] = {}
    """命令名 → 命令实例 映射。"""
    _aliases: dict[str, str] = {}
    """别名 → 命令名 映射。"""

    @classmethod
    def register(cls, command: BaseCommand) -> BaseCommand:
        """注册一个命令。

        检查命令名和别名的唯一性，冲突时抛出 ValueError。

        Args:
            command: 要注册的 BaseCommand 实例。

        Returns:
            注册成功的命令实例（原样返回，可用作装饰器）。

        Raises:
            ValueError: 命令名或别名已存在时抛出。
        """
        # 检查命令名冲突
        if command.name in cls._commands:
            raise ValueError(
                f"命令名 '{command.name}' 已注册。"
            )

        # 检查别名冲突
        for alias in command.aliases:
            if alias in cls._aliases:
                existing = cls._aliases[alias]
                raise ValueError(
                    f"别名 '{alias}' 已被命令 '{existing}' 注册。"
                )

        # 注册命令
        cls._commands[command.name] = command

        # 注册别名
        for alias in command.aliases:
            cls._aliases[alias] = command.name

        return command

    @classmethod
    def find(cls, name_or_alias: str) -> BaseCommand | None:
        """按名称或别名查找命令。

        Args:
            name_or_alias: 命令名或别名（不含 '/' 前缀）。

        Returns:
            找到的 BaseCommand 实例，未找到时返回 None。
        """
        # 优先按命令名查找
        if name_or_alias in cls._commands:
            return cls._commands[name_or_alias]

        # 按别名查找
        if name_or_alias in cls._aliases:
            cmd_name = cls._aliases[name_or_alias]
            return cls._commands.get(cmd_name)

        return None

    @classmethod
    def list_all(cls) -> list[BaseCommand]:
        """返回所有已注册的命令列表。

        Returns:
            已注册的 BaseCommand 实例列表。
        """
        return list(cls._commands.values())
```

- [ ] **Step 8: 运行测试，确认通过**

```bash
uv run pytest tests/test_commands/test_registry.py::TestCommandRegistry -v
```

预期：全部 PASS。

- [ ] **Step 9: 更新 `__init__.py`**

修改 `src/minicode/commands/__init__.py`：

```python
"""斜杠命令系统。

提供命令抽象、注册、路由的完整基础设施。
所有命令通过 CommandRegistry.register() 注册后，
由 ChatApp 的输入路由自动分发。
"""

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.commands.registry import CommandRegistry

__all__ = [
    "BaseCommand",
    "CommandContext",
    "CommandResult",
    "CommandRegistry",
]
```

- [ ] **Step 10: 提交**

```bash
git add src/minicode/commands/base.py src/minicode/commands/registry.py src/minicode/commands/__init__.py tests/test_commands/
git commit -m "feat: 命令基础设施（BaseCommand、CommandRegistry）"
```

---

### Task 2: `/quit` 命令

**Files:**
- Create: `src/minicode/commands/quit_cmd.py`
- Create: `tests/test_commands/test_quit.py`

**Interfaces:**
- Consumes: `BaseCommand`, `CommandResult`, `CommandContext` (from Task 1)
- Produces: `QuitCommand` 实例（name="quit", aliases=["exit", "q"]）

- [ ] **Step 1: 编写测试**

创建 `tests/test_commands/test_quit.py`：

```python
"""/quit 命令单元测试。"""

from __future__ import annotations

import pytest

from minicode.commands.base import CommandResult
from minicode.commands.quit_cmd import QuitCommand


class TestQuitCommand:
    """QuitCommand 核心功能测试。"""

    def test_name_and_aliases(self) -> None:
        """验证命令名和别名。"""
        cmd = QuitCommand()
        assert cmd.name == "quit"
        assert "exit" in cmd.aliases
        assert "q" in cmd.aliases

    @pytest.mark.asyncio
    async def test_execute_returns_should_exit(self) -> None:
        """execute 应返回 should_exit=True。"""
        cmd = QuitCommand()
        result = await cmd.execute("", None)  # type: ignore[arg-type]
        assert isinstance(result, CommandResult)
        assert result.should_exit is True

    @pytest.mark.asyncio
    async def test_execute_has_farewell_message(self) -> None:
        """execute 应包含告别消息。"""
        cmd = QuitCommand()
        result = await cmd.execute("", None)  # type: ignore[arg-type]
        assert result.message is not None
        assert len(result.message) > 0
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_commands/test_quit.py -v
```

预期：FAIL，`quit_cmd.py` 不存在。

- [ ] **Step 3: 实现 `quit_cmd.py`**

创建 `src/minicode/commands/quit_cmd.py`：

```python
"""/quit 命令 —— 退出 MiniCode。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult


class QuitCommand(BaseCommand):
    """退出 MiniCode 程序。"""

    name: str = "quit"
    aliases: list[str] = ["exit", "q"]
    description: str = "退出 MiniCode"
    usage: str = "/quit"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """执行退出命令。

        Args:
            args: 命令参数（忽略）。
            ctx: 命令执行上下文。

        Returns:
            CommandResult(should_exit=True)。
        """
        return CommandResult(
            should_exit=True,
            message="再见！",
        )
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/test_commands/test_quit.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/minicode/commands/quit_cmd.py tests/test_commands/test_quit.py
git commit -m "feat: /quit 命令实现"
```

---

### Task 3: `/help` 命令

**Files:**
- Create: `src/minicode/commands/help_cmd.py`
- Create: `tests/test_commands/test_help.py`

**Interfaces:**
- Consumes: `BaseCommand`, `CommandResult`, `CommandContext`, `CommandRegistry` (from Task 1)
- Produces: `HelpCommand` 实例（name="help"）

- [ ] **Step 1: 编写测试**

创建 `tests/test_commands/test_help.py`：

```python
"""/help 命令单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.commands.help_cmd import HelpCommand
from minicode.commands.registry import CommandRegistry


class _FakeRenderer:
    """测试用假渲染器。"""

    def show_info(self, message: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        pass


def _make_ctx() -> CommandContext:
    """构建最小 CommandContext。"""
    return CommandContext(
        app_config=None,  # type: ignore[arg-type]
        workspace_root=Path.cwd(),
        session_manager=None,  # type: ignore[arg-type]
        agent_loop=None,
        renderer=_FakeRenderer(),  # type: ignore[arg-type]
        console=Console(file=None),  # 无输出的 Console
    )


class TestHelpCommand:
    """/help 命令测试。"""

    def test_name(self) -> None:
        """验证命令名。"""
        cmd = HelpCommand()
        assert cmd.name == "help"

    @pytest.mark.asyncio
    async def test_execute_lists_registered_commands(self) -> None:
        """/help 应列出所有已注册的命令。"""
        # 清理并注册桩命令
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        class _CmdA(BaseCommand):
            name = "cmd_a"
            description = "命令 A"
            aliases = ["a"]

            async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
                return CommandResult()

        class _CmdB(BaseCommand):
            name = "cmd_b"
            description = "命令 B"

            async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
                return CommandResult()

        CommandRegistry.register(_CmdA())
        CommandRegistry.register(_CmdB())

        cmd = HelpCommand()
        result = await cmd.execute("", _make_ctx())

        assert result.success is True
        # 消息中应包含命令名
        assert "cmd_a" in (result.message or "")
        assert "cmd_b" in (result.message or "")

    @pytest.mark.asyncio
    async def test_execute_shows_aliases(self) -> None:
        """/help 应显示命令别名。"""
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        class _Cmd(BaseCommand):
            name = "test"
            description = "测试"
            aliases = ["t"]

            async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
                return CommandResult()

        CommandRegistry.register(_Cmd())

        cmd = HelpCommand()
        result = await cmd.execute("", _make_ctx())

        assert "t" in (result.message or "")
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_commands/test_help.py -v
```

预期：FAIL，`help_cmd.py` 不存在。

- [ ] **Step 3: 实现 `help_cmd.py`**

创建 `src/minicode/commands/help_cmd.py`：

```python
"""/help 命令 —— 显示可用命令列表。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.commands.registry import CommandRegistry


class HelpCommand(BaseCommand):
    """显示所有可用命令及其用法。"""

    name: str = "help"
    description: str = "显示帮助信息"
    usage: str = "/help"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """遍历已注册命令，格式化输出帮助信息。

        通过 CommandRegistry.list_all() 动态获取命令列表，
        新增命令无需修改 /help 代码。

        Args:
            args: 命令参数（忽略）。
            ctx: 命令执行上下文。

        Returns:
            CommandResult，message 包含格式化后的帮助文本。
        """
        commands = sorted(
            CommandRegistry.list_all(), key=lambda c: c.name
        )

        if not commands:
            return CommandResult(message="没有可用的命令。")

        lines: list[str] = []
        lines.append("可用命令：")
        lines.append("")

        for cmd in commands:
            alias_str = f"（{'、'.join('/' + a for a in cmd.aliases)}）" if cmd.aliases else ""
            lines.append(f"  /{cmd.name} {alias_str}")
            lines.append(f"    {cmd.description}")
            if cmd.usage:
                lines.append(f"    用法：{cmd.usage}")
            lines.append("")

        lines.append("输入 /<命令名> 执行命令，或直接输入文本与 AI 对话。")

        return CommandResult(message="\n".join(lines))
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/test_commands/test_help.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/minicode/commands/help_cmd.py tests/test_commands/test_help.py
git commit -m "feat: /help 命令实现"
```

---

### Task 4: `/clear` 命令 + ChatApp 会话重置

**Files:**
- Create: `src/minicode/commands/clear_cmd.py`
- Modify: `src/minicode/cli/app.py` — 新增 `_clear_and_new_session()` 方法
- Create: `tests/test_commands/test_clear.py`

**Interfaces:**
- Consumes: `BaseCommand`, `CommandResult`, `CommandContext` (from Task 1), `SessionManager` (existing)
- Produces: `ClearCommand` 实例（name="clear"）, `ChatApp._clear_and_new_session() -> None`

- [ ] **Step 1: 编写 clear 命令测试**

创建 `tests/test_commands/test_clear.py`：

```python
"""/clear 命令单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from minicode.commands.base import CommandContext
from minicode.commands.clear_cmd import ClearCommand


class _FakeRenderer:
    """测试用假渲染器。"""

    def show_info(self, message: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        pass


class _FakeAgentLoop:
    """测试用假 AgentLoop。"""

    def __init__(self) -> None:
        self.messages: list = [
            {"role": "system", "content": "你是一个 AI 助手。"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
        ]


def _make_fake_session_manager(tmp_path: Path) -> MagicMock:
    """创建一个 Fake SessionManager。"""
    mgr = MagicMock()
    # create 方法返回一个 MagicMock session
    session = MagicMock()
    session.id = "fake-session-id"
    session.name = "2026-07-09 10:00"
    mgr.create.return_value = session
    return mgr


class TestClearCommand:
    """/clear 命令测试。"""

    def test_name(self) -> None:
        """验证命令名。"""
        cmd = ClearCommand()
        assert cmd.name == "clear"

    @pytest.mark.asyncio
    async def test_execute_with_agent_loop(self, tmp_path: Path) -> None:
        """有 AgentLoop 时，/clear 应清空消息并创建新会话。"""
        agent_loop = _FakeAgentLoop()
        session_mgr = _make_fake_session_manager(tmp_path)

        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=agent_loop,  # type: ignore[arg-type]
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = ClearCommand()
        result = await cmd.execute("", ctx)

        assert result.success is True
        # AgentLoop 消息应被清空
        assert len(agent_loop.messages) == 0
        # 应调用了 create 创建新会话
        session_mgr.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_without_agent_loop(self, tmp_path: Path) -> None:
        """无 AgentLoop 时（首轮前），/clear 应创建新会话但不报错。"""
        session_mgr = _make_fake_session_manager(tmp_path)

        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = ClearCommand()
        result = await cmd.execute("", ctx)

        assert result.success is True
        # 无 agent_loop 时仅创建会话
        session_mgr.create.assert_called_once()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_commands/test_clear.py -v
```

预期：FAIL，`clear_cmd.py` 不存在。

- [ ] **Step 3: 在 ChatApp 中添加 `_clear_and_new_session()` 方法**

修改 `src/minicode/cli/app.py`。在 `_auto_save` 方法之后（约 169 行附近）添加：

```python
    async def _clear_and_new_session(self) -> None:
        """清空当前 AgentLoop 消息并创建新会话。

        由 /clear 命令调用，也用于 /session delete 删除当前会话时。
        旧会话已在 _auto_save 中保存，此处仅清理上下文和创建新会话。
        """
        agent_loop = self._agent_loop
        # 清空现有消息历史，AgentLoop 会在下一轮自动注入 system prompt
        if agent_loop is not None:
            agent_loop.messages.clear()

        # 创建新会话
        manager = self._get_session_manager()
        self._current_session = manager.create(
            model=self.config.default_model,
            provider=self.config.default_provider,
            workspace_root=str(self.workspace_root),
        )
        logger.debug(
            "已创建新会话",
            session_id=self._current_session.id,
            reason="clear_command",
        )
```

- [ ] **Step 4: 实现 `clear_cmd.py`**

创建 `src/minicode/commands/clear_cmd.py`：

```python
"""/clear 命令 —— 清除对话上下文并创建新会话。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.utils.log import get_logger

logger = get_logger(__name__)


class ClearCommand(BaseCommand):
    """清除当前对话上下文，保存旧会话并创建新会话。"""

    name: str = "clear"
    description: str = "清除对话上下文并创建新会话"
    usage: str = "/clear"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """清除 AgentLoop 消息历史并创建新会话。

        旧会话已在此前通过 _auto_save 保存到磁盘。
        新会话创建后自动成为当前活跃会话。

        Args:
            args: 命令参数（忽略）。
            ctx: 命令执行上下文，包含 agent_loop 和 session_manager。

        Returns:
            CommandResult 包含操作结果消息。
        """
        # 清空 AgentLoop 消息
        if ctx.agent_loop is not None:
            message_count = len(ctx.agent_loop.messages)
            ctx.agent_loop.messages.clear()
            logger.debug(
                "上下文已清除",
                cleared_messages=message_count,
            )
        else:
            logger.debug("无活跃 AgentLoop，仅创建新会话")

        # 创建新会话
        new_session = ctx.session_manager.create(
            model=ctx.app_config.default_model,
            provider=ctx.app_config.default_provider,
            workspace_root=str(ctx.workspace_root),
        )
        # 注意：ChatApp 需要在调用完命令后，用返回的 session_id
        # 来更新 _current_session。ClearCommand 通过 context 传递
        # 新 session 的引用。
        #
        # 实际更新 _current_session 由 ChatApp._clear_and_new_session()
        # 负责，/clear 命令通过 ChatApp 的方法间接调用。
        #
        # 但此处我们让命令直接操作上下文来减少耦合。
        # ChatApp 会在命令执行前后处理 _current_session 的更新。

        return CommandResult(
            message=f"上下文已清除，新会话已创建。（{new_session.id[:8]}）",
        )
```

- [ ] **Step 5: 运行测试，确认通过**

```bash
uv run pytest tests/test_commands/test_clear.py -v
```

预期：全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add src/minicode/commands/clear_cmd.py src/minicode/cli/app.py tests/test_commands/test_clear.py
git commit -m "feat: /clear 命令实现及 ChatApp._clear_and_new_session()"
```

---

### Task 5: `/config show` 命令

**Files:**
- Create: `src/minicode/commands/config_cmd.py`
- Create: `tests/test_commands/test_config.py`

**Interfaces:**
- Consumes: `BaseCommand`, `CommandResult`, `CommandContext` (from Task 1), `AppConfig` (existing)
- Produces: `ConfigCommand` 实例（name="config"）

- [ ] **Step 1: 编写测试**

创建 `tests/test_commands/test_config.py`：

```python
"""/config 命令单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from minicode.commands.base import CommandContext
from minicode.commands.config_cmd import ConfigCommand
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig


class _FakeRenderer:
    """测试用假渲染器。"""

    def show_info(self, message: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        pass


def _make_config() -> AppConfig:
    """创建一个测试用 AppConfig。"""
    return AppConfig(
        default_provider="deepseek",
        default_model="deepseek-v4-flash",
        max_tokens=16384,
        agent=AgentConfig(max_rounds=20, stream=True),
        permissions=PermissionsConfig(trust_mode=False),
        providers={
            "openai": ProviderConfig(
                api_key="sk-test1234",
                base_url="https://api.openai.com/v1",
                models=["gpt-4o", "gpt-4o-mini"],
            ),
            "deepseek": ProviderConfig(
                api_key="sk-ds5678abcd",
                base_url="https://api.deepseek.com",
                models=["deepseek-v4-flash"],
            ),
        },
    )


class TestConfigCommand:
    """/config 命令测试。"""

    def test_name(self) -> None:
        """验证命令名。"""
        cmd = ConfigCommand()
        assert cmd.name == "config"

    @pytest.mark.asyncio
    async def test_execute_show_default(self) -> None:
        """无参数时默认显示配置。"""
        config = _make_config()
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path.cwd(),
            session_manager=MagicMock(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = ConfigCommand()
        result = await cmd.execute("", ctx)

        assert result.success is True
        msg = result.message or ""
        # 验证关键配置项出现在输出中
        assert "deepseek" in msg
        assert "deepseek-v4-flash" in msg
        assert "16384" in msg

    @pytest.mark.asyncio
    async def test_execute_show_hides_api_key(self) -> None:
        """/config show 应脱敏 API key。"""
        config = _make_config()
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path.cwd(),
            session_manager=MagicMock(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = ConfigCommand()
        result = await cmd.execute("show", ctx)

        msg = result.message or ""
        # API key 完整值不应出现在输出中
        assert "sk-test1234" not in msg
        assert "sk-ds5678abcd" not in msg

    @pytest.mark.asyncio
    async def test_execute_unknown_subcommand(self) -> None:
        """未知子命令应返回提示。"""
        config = _make_config()
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path.cwd(),
            session_manager=MagicMock(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = ConfigCommand()
        result = await cmd.execute("unknown", ctx)

        assert result.success is False
        assert "show" in (result.message or "")
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_commands/test_config.py -v
```

预期：FAIL，`config_cmd.py` 不存在。

- [ ] **Step 3: 实现 `config_cmd.py`**

创建 `src/minicode/commands/config_cmd.py`：

```python
"""/config 命令 —— 查看当前配置。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult


def _mask_api_key(key: str) -> str:
    """对 API key 进行脱敏处理。

    显示前 4 位 + **** + 后 4 位。
    长度不足 8 位的 key 全部脱敏。

    Args:
        key: 原始 API key。

    Returns:
        脱敏后的字符串。
    """
    if len(key) < 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


class ConfigCommand(BaseCommand):
    """查看 MiniCode 当前配置。"""

    name: str = "config"
    description: str = "查看当前配置"
    usage: str = "/config show"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """显示当前配置信息。

        Args:
            args: 子命令。支持 "show"，空字符串等同于 "show"。
            ctx: 命令执行上下文。

        Returns:
            CommandResult，message 包含格式化后的配置文本。
        """
        subcmd = args.strip().lower()

        if subcmd not in ("", "show"):
            return CommandResult(
                success=False,
                message=f"未知的 config 子命令：{subcmd}。\n用法：/config show — 查看当前配置",
            )

        config = ctx.app_config

        lines: list[str] = []
        lines.append("当前配置：")
        lines.append("")
        lines.append(f"  默认 Provider : {config.default_provider}")
        lines.append(f"  默认 Model    : {config.default_model}")
        lines.append(f"  Max Tokens    : {config.max_tokens}")
        lines.append(f"  Agent 最大轮次 : {config.agent.max_rounds}")
        lines.append(f"  流式输出       : {'启用' if config.agent.stream else '关闭'}")
        lines.append(f"  Trust 模式     : {'启用' if config.permissions.trust_mode else '关闭'}")
        lines.append("")

        # 显示已配置的 Provider 列表
        if config.providers:
            lines.append("已配置的 Providers：")
            lines.append("")
            for name, provider in config.providers.items():
                is_default = "*" if name == config.default_provider else " "
                masked_key = _mask_api_key(provider.api_key) if provider.api_key else "（未设置）"
                models_str = "、".join(provider.models) if provider.models else "（无）"
                lines.append(f"  [{is_default}] {name}")
                lines.append(f"      Base URL: {provider.base_url}")
                lines.append(f"      API Key : {masked_key}")
                lines.append(f"      Models  : {models_str}")
                lines.append("")

        return CommandResult(message="\n".join(lines))
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/test_commands/test_config.py -v
```

预期：全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/minicode/commands/config_cmd.py tests/test_commands/test_config.py
git commit -m "feat: /config show 命令实现"
```

---

### Task 6: `/session` 命令 + 交互式选择器 + ChatApp.switch_session()

**Files:**
- Create: `src/minicode/commands/session_cmd.py`
- Modify: `src/minicode/cli/app.py` — 新增 `switch_session()` 方法
- Create: `tests/test_commands/test_session.py`

**Interfaces:**
- Consumes: `BaseCommand`, `CommandResult`, `CommandContext` (from Task 1), `SessionManager` (existing), `Rich Console`
- Produces: `SessionCommand` 实例（name="session"）, `ChatApp.switch_session(session_id: str) -> bool`

- [ ] **Step 1: 编写 session 命令测试**

创建 `tests/test_commands/test_session.py`：

```python
"""/session 命令单元测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from minicode.commands.base import CommandContext
from minicode.commands.session_cmd import SessionCommand


class _FakeRenderer:
    """测试用假渲染器。"""

    def show_info(self, message: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        pass


class _FakeAgentLoop:
    """测试用假 AgentLoop。"""

    def __init__(self) -> None:
        self.messages: list = [{"role": "user", "content": "test"}]


def _make_fake_session_manager(sessions: list[dict] | None = None) -> MagicMock:
    """创建一个 Fake SessionManager。"""
    mgr = MagicMock()
    mgr.list_sessions.return_value = sessions or [
        {
            "id": "a" * 32,
            "name": "2026-07-09 10:00",
            "created_at": "2026-07-09T10:00:00+00:00",
            "updated_at": "2026-07-09T10:30:00+00:00",
            "model": "deepseek-v4-flash",
            "provider": "deepseek",
            "message_count": 5,
        },
        {
            "id": "b" * 32,
            "name": "2026-07-08 15:00",
            "created_at": "2026-07-08T15:00:00+00:00",
            "updated_at": "2026-07-08T15:20:00+00:00",
            "model": "gpt-4o",
            "provider": "openai",
            "message_count": 12,
        },
    ]
    loaded_session = MagicMock()
    loaded_session.id = "a" * 32
    loaded_session.name = "2026-07-09 10:00"
    loaded_session.messages = [{"role": "user", "content": "previous"}]
    mgr.load.return_value = loaded_session
    mgr.delete.return_value = True
    return mgr


class TestSessionCommand:
    """/session 命令测试。"""

    def test_name(self) -> None:
        """验证命令名。"""
        cmd = SessionCommand()
        assert cmd.name == "session"

    @pytest.mark.asyncio
    async def test_list(self, tmp_path: Path) -> None:
        """/session list 应列出所有会话摘要。"""
        session_mgr = _make_fake_session_manager()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute("list", ctx)

        assert result.success is True
        msg = result.message or ""
        # 应包含会话 ID 和名称
        assert "a" * 8 in msg  # ID 前 8 位
        assert "2026-07-09" in msg
        assert "5" in msg  # message_count

    @pytest.mark.asyncio
    async def test_switch_valid_id(self, tmp_path: Path) -> None:
        """/session switch <valid_id> 应成功加载会话。"""
        session_mgr = _make_fake_session_manager()
        agent_loop = _FakeAgentLoop()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=agent_loop,  # type: ignore[arg-type]
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute(f"switch {'a' * 32}", ctx)

        assert result.success is True
        session_mgr.load.assert_called_once()
        # AgentLoop 消息应被替换为加载会话的消息
        assert len(agent_loop.messages) > 0

    @pytest.mark.asyncio
    async def test_switch_missing_id(self, tmp_path: Path) -> None:
        """/session switch 缺少 ID 应提示用法。"""
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=_make_fake_session_manager(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute("switch", ctx)

        assert result.success is False
        assert "用法" in (result.message or "")

    @pytest.mark.asyncio
    async def test_switch_nonexistent_id(self, tmp_path: Path) -> None:
        """/session switch 不存在的 ID 应报错。"""
        session_mgr = _make_fake_session_manager()
        session_mgr.load.return_value = None  # 加载失败
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute(f"switch {'c' * 32}", ctx)

        assert result.success is False
        assert "不存在" in (result.message or "")

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path: Path) -> None:
        """/session delete <id> 应删除指定会话。"""
        session_mgr = _make_fake_session_manager()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute(f"delete {'b' * 32}", ctx)

        assert result.success is True
        session_mgr.delete.assert_called_once_with("b" * 32)

    @pytest.mark.asyncio
    async def test_delete_missing_id(self, tmp_path: Path) -> None:
        """/session delete 缺少 ID 应提示用法。"""
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=_make_fake_session_manager(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute("delete", ctx)

        assert result.success is False
        assert "用法" in (result.message or "")

    @pytest.mark.asyncio
    async def test_empty_args_starts_interactive(self, tmp_path: Path) -> None:
        """/session 无参数应启动交互式选择。"""
        session_mgr = _make_fake_session_manager()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        # 使用 patch 模拟交互式选择的返回值
        with patch.object(cmd, "_interactive_select", return_value="a" * 32):
            result = await cmd.execute("", ctx)

        # 交互选择返回了 ID，应尝试加载
        assert result.success is True
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_commands/test_session.py -v
```

预期：FAIL，`session_cmd.py` 不存在。

- [ ] **Step 3: 在 ChatApp 中添加 `switch_session()` 方法**

修改 `src/minicode/cli/app.py`，在 `_clear_and_new_session()` 方法之后添加：

```python
    async def switch_session(self, session_id: str) -> bool:
        """切换到指定会话。

        保存当前会话后，加载目标会话并替换 AgentLoop 的消息历史。

        Args:
            session_id: 目标会话 ID。

        Returns:
            True 表示切换成功，False 表示失败。
        """
        from minicode.session import SessionManager

        manager = self._get_session_manager()

        # 保存当前会话
        if self._current_session is not None and self._agent_loop is not None:
            self._current_session.messages = list(self._agent_loop.messages)
            manager.save(self._current_session)

        # 加载目标会话
        target = manager.load(session_id)
        if target is None:
            return False

        # 替换 AgentLoop 消息
        agent_loop = self._get_agent_loop()
        agent_loop.messages.clear()
        agent_loop.messages.extend(target.messages)

        # 更新当前会话引用
        self._current_session = target

        logger.debug(
            "已切换会话",
            session_id=session_id,
            message_count=target.message_count,
        )
        return True
```

- [ ] **Step 4: 实现 `session_cmd.py`**

创建 `src/minicode/commands/session_cmd.py`：

```python
"""/session 命令 —— 会话列表、切换、删除及交互式键盘选择。"""

from __future__ import annotations

import asyncio

from prompt_toolkit.input import create_input
from prompt_toolkit.keys import Keys
from rich.live import Live
from rich.table import Table

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.utils.log import get_logger

logger = get_logger(__name__)

# 交互式选择器最多显示的会话数
_MAX_INTERACTIVE_SESSIONS = 20


class SessionCommand(BaseCommand):
    """管理 MiniCode 会话：列表、切换、删除。

    用法：
        /session              → 交互式方向键选择会话
        /session list         → 列出最近 20 条会话
        /session switch <id>  → 切换到指定会话
        /session delete <id>  → 删除指定会话
    """

    name: str = "session"
    description: str = "管理会话（列表/切换/删除）"
    usage: str = "/session [list|switch <id>|delete <id>]"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """根据子命令分发到对应处理逻辑。

        Args:
            args: 子命令和参数。
            ctx: 命令执行上下文。

        Returns:
            CommandResult 描述执行结果。
        """
        args = args.strip()

        # 无参数 → 交互式选择
        if not args:
            return await self._handle_interactive(ctx)

        # 解析子命令
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower()
        sub_args = parts[1] if len(parts) > 1 else ""

        if subcmd == "list":
            return await self._handle_list(ctx)
        elif subcmd == "switch":
            return await self._handle_switch(sub_args, ctx)
        elif subcmd == "delete":
            return await self._handle_delete(sub_args, ctx)
        else:
            return CommandResult(
                success=False,
                message=(
                    f"未知的 session 子命令：{subcmd}。\n"
                    f"可用子命令：list、switch <id>、delete <id>\n"
                    f"直接输入 /session 可交互式选择会话。"
                ),
            )

    # ─── 子命令处理 ─────────────────────────────────────────

    async def _handle_list(self, ctx: CommandContext) -> CommandResult:
        """列出最近 20 条会话摘要。"""
        sessions = ctx.session_manager.list_sessions()
        recent = sessions[:_MAX_INTERACTIVE_SESSIONS]

        if not recent:
            return CommandResult(message="没有保存的会话。")

        lines: list[str] = []
        lines.append(f"会话列表（最近 {len(recent)} 条）：")
        lines.append("")

        for i, s in enumerate(recent, 1):
            sid = s.get("id", "")[:8]
            name = s.get("name", "")
            updated = s.get("updated_at", "")[:16]
            msg_count = s.get("message_count", 0)
            model = s.get("model", "")
            lines.append(f"  {i:2d}. [{sid}] {name}  ·  {msg_count} 条消息  ·  {model}  ·  {updated}")

        return CommandResult(message="\n".join(lines))

    async def _handle_switch(self, target_id: str, ctx: CommandContext) -> CommandResult:
        """切换到指定会话。"""
        if not target_id:
            return CommandResult(
                success=False,
                message="用法：/session switch <会话ID>\n"
                        "提示：输入 /session list 查看所有会话，"
                        "或输入 /session 交互式选择。",
            )

        return await self._do_switch(target_id, ctx)

    async def _handle_delete(self, target_id: str, ctx: CommandContext) -> CommandResult:
        """删除指定会话。"""
        if not target_id:
            return CommandResult(
                success=False,
                message="用法：/session delete <会话ID>\n"
                        "提示：输入 /session list 查看所有会话。",
            )

        # 如果删除的是当前活跃会话，需要通知 ChatApp 清理状态
        # 通过检查 agent_loop 是否有消息来判断
        deleted = ctx.session_manager.delete(target_id)
        if not deleted:
            return CommandResult(
                success=False,
                message=f"会话 {target_id[:8]} 不存在，无需删除。",
            )

        return CommandResult(
            message=f"会话 {target_id[:8]} 已删除。",
        )

    # ─── 交互式选择器 ───────────────────────────────────────

    async def _handle_interactive(self, ctx: CommandContext) -> CommandResult:
        """启动交互式方向键会话选择器。"""
        sessions = ctx.session_manager.list_sessions()

        if not sessions:
            return CommandResult(message="没有保存的会话。")

        recent = sessions[:_MAX_INTERACTIVE_SESSIONS]
        selected_id = await self._interactive_select(recent, ctx)

        if selected_id is None:
            return CommandResult(message="已取消。")

        return await self._do_switch(selected_id, ctx)

    async def _interactive_select(
        self, sessions: list[dict], ctx: CommandContext
    ) -> str | None:
        """使用 Rich Live + prompt_toolkit 输入实现方向键交互式选择。

        在终端内渲染会话列表，支持 ↑↓ 导航、Enter 确认、Esc 取消。

        Args:
            sessions: 会话摘要列表。
            ctx: 命令执行上下文。

        Returns:
            选中的 session_id，取消时返回 None。
        """
        selected_idx = 0
        done: asyncio.Event = asyncio.Event()
        result: str | None = None

        def build_table() -> Table:
            """构建当前选择状态的 Rich Table。"""
            table = Table(
                title="会话历史（↑↓ 选择，Enter 加载，Esc 取消）",
                title_style="bold",
                show_header=True,
                header_style="bold",
            )
            table.add_column("#", style="dim", width=4)
            table.add_column("名称", style="cyan")
            table.add_column("时间", style="dim")
            table.add_column("消息数", justify="right")
            table.add_column("模型", style="green")

            for i, s in enumerate(sessions):
                prefix = ">" if i == selected_idx else " "
                style = "reverse" if i == selected_idx else ""
                name = s.get("name", "")
                updated = s.get("updated_at", "")[:16]
                msg_count = str(s.get("message_count", 0))
                model = s.get("model", "")

                if style:
                    # Rich 不支持整行 style，通过在每个 cell 加 markup
                    table.add_row(
                        f"[reverse]{prefix} {i + 1}[/]",
                        f"[reverse]{name}[/]",
                        f"[reverse]{updated}[/]",
                        f"[reverse]{msg_count}[/]",
                        f"[reverse]{model}[/]",
                    )
                else:
                    table.add_row(
                        f"  {i + 1}",
                        name,
                        updated,
                        msg_count,
                        model,
                    )

            return table

        async def listen_keys() -> None:
            """监听键盘事件，更新选中索引。"""
            nonlocal selected_idx, result

            input_obj = create_input()
            try:
                async for key_press in input_obj.read_keys():
                    if key_press.key == Keys.Up:
                        selected_idx = (selected_idx - 1) % len(sessions)
                    elif key_press.key == Keys.Down:
                        selected_idx = (selected_idx + 1) % len(sessions)
                    elif key_press.key in (Keys.Enter, Keys.ControlM):
                        result = sessions[selected_idx].get("id")
                        done.set()
                        return
                    elif key_press.key == Keys.Escape:
                        done.set()
                        return
                    elif key_press.key == Keys.ControlC:
                        done.set()
                        return
            finally:
                done.set()  # 输入流异常时也退出

        # 启动键盘监听任务
        listener_task = asyncio.create_task(listen_keys())

        # 使用 Rich Live 渲染列表
        with Live(
            build_table(),
            console=ctx.console,
            refresh_per_second=10,
            transient=True,
        ) as live:
            while not done.is_set():
                # 等待一小段时间后刷新
                try:
                    await asyncio.wait_for(done.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    pass
                live.update(build_table())

        # 清理
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass

        return result

    # ─── 内部方法 ───────────────────────────────────────────

    async def _do_switch(self, target_id: str, ctx: CommandContext) -> CommandResult:
        """执行会话切换的实际逻辑。

        Args:
            target_id: 目标会话 ID。
            ctx: 命令执行上下文。

        Returns:
            CommandResult 描述切换结果。
        """
        # 验证 session_id 格式
        import re
        if not re.fullmatch(r"[0-9a-f]{32}", target_id):
            return CommandResult(
                success=False,
                message="无效的会话 ID。会话 ID 为 32 位十六进制字符串。",
            )

        # 加载目标会话
        target = ctx.session_manager.load(target_id)
        if target is None:
            return CommandResult(
                success=False,
                message=f"会话 {target_id[:8]} 不存在。",
            )

        # 替换 AgentLoop 消息
        if ctx.agent_loop is not None:
            ctx.agent_loop.messages.clear()
            ctx.agent_loop.messages.extend(target.messages)

        logger.info(
            "会话已切换",
            session_id=target_id[:8],
            message_count=target.message_count,
        )

        return CommandResult(
            message=(
                f"已切换到会话：{target.name}\n"
                f"  ID     : {target.id[:8]}...\n"
                f"  消息数 : {target.message_count}\n"
                f"  模型   : {target.model}"
            ),
        )
```

- [ ] **Step 5: 运行 session 测试（跳过交互式测试）**

```bash
uv run pytest tests/test_commands/test_session.py -v -k "not interactive"
```

先运行非交互式测试，确认基础子命令（list/switch/delete）逻辑正确。

- [ ] **Step 6: 运行全部 session 测试**

```bash
uv run pytest tests/test_commands/test_session.py -v
```

预期：全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add src/minicode/commands/session_cmd.py src/minicode/cli/app.py tests/test_commands/test_session.py
git commit -m "feat: /session 命令实现（列表/切换/删除 + 交互式方向键选择）"
```

---

### Task 7: ChatApp 命令路由集成

**Files:**
- Modify: `src/minicode/cli/app.py` — 新增 `_handle_input()` 和 `_handle_command()`，修改 `run()` 方法
- Modify: `tests/test_cli/test_app.py` — 新增命令路由测试

**Interfaces:**
- Consumes: 所有命令（Task 2-6）, `CommandRegistry` (Task 1)
- Produces: 完整的命令路由功能

- [ ] **Step 1: 修改 ChatApp.run() 和新增命令路由方法**

修改 `src/minicode/cli/app.py`。

**1.1 在文件顶部添加 imports**（在现有 imports 之后）：

```python
from minicode.commands.base import CommandContext
from minicode.commands.registry import CommandRegistry
```

**1.2 修改 `run()` 方法**，将硬编码的 exit/quit 检查替换为 `_handle_input()` 调用。

找到 `run()` 方法中的这段代码（约 65-81 行）：

```python
                user_input = user_input.strip()
                if not user_input:
                    continue

                if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
                    self.renderer.show_info("再见！")
                    break

                await self._handle_message(user_input)
```

替换为：

```python
                user_input = user_input.strip()
                if not user_input:
                    continue

                should_exit = await self._handle_input(user_input)
                if should_exit:
                    break
```

同时移除 `run()` 方法中 `KeyboardInterrupt` 和 `EOFError` 异常处理中的 `self.renderer.show_info("\n再见！")` 前的 `\n` 改为空（保持 "再见！" 显示即可）。

**1.3 在 `_handle_message()` 方法之后添加新方法**：

```python
    async def _handle_input(self, text: str) -> bool:
        """处理用户输入，路由到命令或 AgentLoop。

        Args:
            text: 用户输入文本。

        Returns:
            True 表示应退出程序。
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
        3. 构建 CommandContext
        4. 执行命令
        5. 处理结果

        Args:
            text: 完整的命令文本（含 '/' 前缀）。

        Returns:
            True 表示应退出程序（/quit 命令）。
        """
        # 解析命令名和参数
        cmd_text = text[1:]  # 去掉 '/' 前缀
        parts = cmd_text.split(maxsplit=1)
        cmd_name = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""

        # 向后兼容：保留直接输入 exit/quit 的能力
        if cmd_name in ("exit", "quit", "q"):
            self.renderer.show_info("再见！")
            return True

        # 查找命令
        command = CommandRegistry.find(cmd_name)
        if command is None:
            self.renderer.show_error(
                f"未知命令：/{cmd_name}。输入 /help 查看可用命令。"
            )
            return False

        # 构建上下文
        ctx = self._build_command_context()

        # 执行命令
        try:
            result = await command.execute(cmd_args, ctx)
        except Exception as e:
            logger.debug("命令执行异常", command=cmd_name, error=str(e), exc_info=True)
            self.renderer.show_error(f"命令执行失败：{e}")
            return False

        # 处理结果
        if result.message:
            if result.success:
                self.renderer.show_info(result.message)
            else:
                self.renderer.show_error(result.message)

        return result.should_exit

    def _build_command_context(self) -> CommandContext:
        """构建命令执行上下文。

        Returns:
            CommandContext 实例，包含所有命令需要的依赖。
        """
        # 懒加载 AgentLoop（命令可能不需要）
        try:
            agent_loop = self._agent_loop  # 不创建新的，只获取已有
        except Exception:
            agent_loop = None

        return CommandContext(
            app_config=self.config,
            workspace_root=self.workspace_root,
            session_manager=self._get_session_manager(),
            agent_loop=agent_loop,
            renderer=self.renderer,
            console=self.console,
        )
```

**1.4 修改 `_build_command_context()` 中获取 AgentLoop 的方式**

将 `self._agent_loop` 替换为 try/except 包装，因为首次对话前 `_agent_loop` 为 None：

```python
    def _build_command_context(self) -> CommandContext:
        """构建命令执行上下文。

        Returns:
            CommandContext 实例，包含所有命令需要的依赖。
        """
        return CommandContext(
            app_config=self.config,
            workspace_root=self.workspace_root,
            session_manager=self._get_session_manager(),
            agent_loop=self._agent_loop,  # 可能为 None（首次对话前）
            renderer=self.renderer,
            console=self.console,
        )
```

- [ ] **Step 2: 编写路由集成测试**

在 `tests/test_cli/test_app.py` 末尾追加：

```python
@pytest.mark.asyncio
class TestCommandRouting:
    """命令路由集成测试。"""

    async def test_slash_quit_exits(self, chat_app: ChatApp) -> None:
        """/quit 命令应返回 True（退出）。"""
        should_exit = await chat_app._handle_input("/quit")
        assert should_exit is True

    async def test_slash_exit_exits(self, chat_app: ChatApp) -> None:
        """/exit 命令也应退出。"""
        should_exit = await chat_app._handle_input("/exit")
        assert should_exit is True

    async def test_slash_q_exits(self, chat_app: ChatApp) -> None:
        """/q 命令也应退出。"""
        should_exit = await chat_app._handle_input("/q")
        assert should_exit is True

    async def test_slash_help_does_not_exit(self, chat_app: ChatApp) -> None:
        """/help 不应触发退出。"""
        from minicode.commands.help_cmd import HelpCommand
        from minicode.commands.registry import CommandRegistry
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()
        CommandRegistry.register(HelpCommand())

        should_exit = await chat_app._handle_input("/help")
        assert should_exit is False

    async def test_slash_config_show(self, chat_app: ChatApp) -> None:
        """/config show 应正常执行。"""
        from minicode.commands.config_cmd import ConfigCommand
        from minicode.commands.registry import CommandRegistry
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()
        CommandRegistry.register(ConfigCommand())

        should_exit = await chat_app._handle_input("/config")
        assert should_exit is False

    async def test_unknown_command_shows_error(self, chat_app: ChatApp) -> None:
        """未知命令应显示错误但不退出。"""
        should_exit = await chat_app._handle_input("/nonexistent_cmd_xyz")
        assert should_exit is False

    async def test_normal_text_delegates_to_agent(self, chat_app: ChatApp) -> None:
        """普通文本输入应委托给 AgentLoop。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("模拟回复"),
        ):
            agent_loop = chat_app._get_agent_loop()
            agent_loop.run = AsyncMock()  # type: ignore[method-assign]

            should_exit = await chat_app._handle_input("你好")
            assert should_exit is False
            agent_loop.run.assert_called_once_with("你好")
```

- [ ] **Step 3: 运行路由集成测试**

```bash
uv run pytest tests/test_cli/test_app.py::TestCommandRouting -v
```

预期：全部 PASS。

- [ ] **Step 4: 注册所有命令**

在 `src/minicode/commands/__init__.py` 中添加 `register_all_commands()` 函数：

```python
"""斜杠命令系统。

提供命令抽象、注册、路由的完整基础设施。
所有命令通过 CommandRegistry.register() 注册后，
由 ChatApp 的输入路由自动分发。
"""

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.commands.registry import CommandRegistry

__all__ = [
    "BaseCommand",
    "CommandContext",
    "CommandResult",
    "CommandRegistry",
    "register_all_commands",
]


def register_all_commands() -> None:
    """注册所有 v0.3 斜杠命令（幂等，可多次调用）。

    在 main.py 启动时调用一次。
    后续版本新增命令时在此函数中添加对应注册行。
    """
    # 先清除已有注册，确保幂等
    CommandRegistry._commands.clear()
    CommandRegistry._aliases.clear()

    from minicode.commands.clear_cmd import ClearCommand
    from minicode.commands.config_cmd import ConfigCommand
    from minicode.commands.help_cmd import HelpCommand
    from minicode.commands.quit_cmd import QuitCommand
    from minicode.commands.session_cmd import SessionCommand

    CommandRegistry.register(QuitCommand())
    CommandRegistry.register(HelpCommand())
    CommandRegistry.register(ClearCommand())
    CommandRegistry.register(SessionCommand())
    CommandRegistry.register(ConfigCommand())
```

- [ ] **Step 5: 在 main.py 中调用 register_all_commands()**

修改 `src/minicode/main.py`，在进入对话模式前注册所有命令：

在 `main.py` 顶部添加 import：
```python
from minicode.commands import register_all_commands
```

在 `asyncio.run(ChatApp(...).run())` 之前添加：
```python
        # 注册所有斜杠命令（在创建 ChatApp 之前，避免重复注册）
        register_all_commands()
```

**注意**：不在 `ChatApp.__init__()` 中调用此函数。
这样测试可以独立控制命令注册状态，
避免每次实例化 ChatApp 时重复注册导致 ValueError。

- [ ] **Step 6: 运行全部现有测试，确保无回归**

```bash
uv run pytest tests/ -v --ignore=tests/test_commands
```

预期：全部 PASS（新增代码不影响现有功能）。

- [ ] **Step 7: 提交**

```bash
git add src/minicode/cli/app.py src/minicode/commands/__init__.py tests/test_cli/test_app.py
git commit -m "feat: ChatApp 命令路由集成，连接所有 / 命令"
```

---

### Task 8: 集成测试

**Files:**
- Create: `tests/test_commands/test_integration.py`
- Create: `tests/test_commands/conftest.py`

**Interfaces:**
- Consumes: 全部命令 + ChatApp 路由 (Task 1-7)
- Produces: 端到端覆盖

- [ ] **Step 1: 编写 conftest.py**

创建 `tests/test_commands/conftest.py`：

```python
"""test_commands 共享 fixtures。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from minicode.commands.base import CommandContext
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig


class _FakeRenderer:
    """测试用假渲染器。"""

    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []

    def show_info(self, message: str) -> None:
        self.info_messages.append(message)

    def show_error(self, message: str) -> None:
        self.error_messages.append(message)


class _FakeAgentLoop:
    """测试用假 AgentLoop。"""

    def __init__(self, messages: list[dict] | None = None) -> None:
        self.messages: list[dict] = messages or []


@pytest.fixture
def app_config() -> AppConfig:
    """创建测试用 AppConfig。"""
    return AppConfig(
        default_provider="deepseek",
        default_model="deepseek-v4-flash",
        max_tokens=16384,
        agent=AgentConfig(max_rounds=20, stream=True),
        permissions=PermissionsConfig(trust_mode=False),
        providers={
            "deepseek": ProviderConfig(
                api_key="sk-test-key",
                base_url="https://api.deepseek.com",
                models=["deepseek-v4-flash"],
            ),
        },
    )


@pytest.fixture
def fake_renderer() -> _FakeRenderer:
    """创建测试用渲染器。"""
    return _FakeRenderer()


@pytest.fixture
def fake_session_manager(tmp_path: Path) -> MagicMock:
    """创建测试用 SessionManager。"""
    mgr = MagicMock()
    mgr.list_sessions.return_value = []
    return mgr


@pytest.fixture
def command_ctx(
    app_config: AppConfig,
    fake_renderer: _FakeRenderer,
    fake_session_manager: MagicMock,
    tmp_path: Path,
) -> CommandContext:
    """构建标准 CommandContext。"""
    return CommandContext(
        app_config=app_config,
        workspace_root=tmp_path,
        session_manager=fake_session_manager,
        agent_loop=_FakeAgentLoop(),
        renderer=fake_renderer,
        console=Console(file=None),
    )
```

- [ ] **Step 2: 编写集成测试**

创建 `tests/test_commands/test_integration.py`：

```python
"""斜杠命令系统集成测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minicode.commands import register_all_commands
from minicode.commands.registry import CommandRegistry
from minicode.cli.app import ChatApp
from minicode.config.models import AppConfig
from minicode.providers.registry import MockProvider


class TestFullCommandChain:
    """命令注册 → 路由 → 执行 完整链路测试。"""

    def test_all_commands_registered(self) -> None:
        """register_all_commands 应注册 5 个命令。"""
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        register_all_commands()

        all_cmds = CommandRegistry.list_all()
        names = {c.name for c in all_cmds}
        assert names == {"quit", "help", "clear", "session", "config"}

    def test_quit_aliases_findable(self) -> None:
        """别名的命令应可通过别名查找。"""
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        register_all_commands()

        # /quit 的别名 /exit 和 /q 应能查找到 QuitCommand
        cmd_via_exit = CommandRegistry.find("exit")
        cmd_via_q = CommandRegistry.find("q")
        assert cmd_via_exit is not None
        assert cmd_via_exit.name == "quit"
        assert cmd_via_q is not None
        assert cmd_via_q.name == "quit"

    @pytest.mark.asyncio
    async def test_help_lists_all_registered(self) -> None:
        """/help 应能动态反映注册的命令变更。"""
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        register_all_commands()

        from minicode.commands.help_cmd import HelpCommand
        from tests.test_commands.conftest import _FakeRenderer
        from minicode.commands.base import CommandContext
        from pathlib import Path
        from rich.console import Console
        from unittest.mock import MagicMock

        ctx = CommandContext(
            app_config=MagicMock(),
            workspace_root=Path.cwd(),
            session_manager=MagicMock(),
            agent_loop=None,
            renderer=_FakeRenderer(),
            console=Console(file=None),
        )

        cmd = HelpCommand()
        result = await cmd.execute("", ctx)

        assert "quit" in (result.message or "")
        assert "help" in (result.message or "")
        assert "clear" in (result.message or "")
        assert "session" in (result.message or "")
        assert "config" in (result.message or "")


@pytest.mark.asyncio
class TestChatAppCommandIntegration:
    """ChatApp 命令路由集成测试。"""

    @pytest.fixture
    def configured_app(self) -> ChatApp:
        """创建一个已注册所有命令的 ChatApp。"""
        import minicode.commands as _  # 确保模块已加载
        from minicode.config.models import AgentConfig, PermissionsConfig, ProviderConfig

        config = AppConfig(
            default_provider="mock",
            default_model="mock-model",
            max_tokens=4096,
            agent=AgentConfig(max_rounds=8, stream=True),
            permissions=PermissionsConfig(trust_mode=False),
            providers={
                "mock": ProviderConfig(
                    api_key="sk-test",
                    base_url="https://api.mock.com/v1",
                    models=["mock-model"],
                ),
            },
        )
        return ChatApp(config)

    async def test_help_through_chatapp(self, configured_app: ChatApp) -> None:
        """/help 通过 ChatApp._handle_input 正常工作。"""
        should_exit = await configured_app._handle_input("/help")
        assert should_exit is False

    async def test_quit_through_chatapp(self, configured_app: ChatApp) -> None:
        """/quit 通过 ChatApp 应返回 should_exit。"""
        should_exit = await configured_app._handle_input("/quit")
        assert should_exit is True

    @patch("minicode.cli.app.ProviderRegistry.get")
    async def test_clear_then_chat(
        self, mock_get: MagicMock, configured_app: ChatApp
    ) -> None:
        """/clear 后应能继续对话。"""
        mock_get.return_value = MockProvider("模拟回复")

        # 先进行一次对话
        await configured_app._handle_input("你好")
        agent_loop = configured_app._agent_loop
        assert agent_loop is not None
        msg_count_before = len(agent_loop.messages)
        assert msg_count_before > 0  # 至少有 system + user + assistant

        # 执行 /clear
        should_exit = await configured_app._handle_input("/clear")
        assert should_exit is False
        # AgentLoop 消息应被清空
        assert len(agent_loop.messages) == 0

        # 继续对话应该正常工作
        agent_loop.run = AsyncMock()  # type: ignore[method-assign]
        await configured_app._handle_input("继续对话")
        agent_loop.run.assert_called_once()
```

- [ ] **Step 3: 运行集成测试**

```bash
uv run pytest tests/test_commands/test_integration.py -v
```

预期：全部 PASS。

- [ ] **Step 4: 提交**

```bash
git add tests/test_commands/conftest.py tests/test_commands/test_integration.py
git commit -m "test: 斜杠命令系统集成测试"
```

---

### Task 9: 最终验证

**Files:**
- 无新建文件（验证现有代码）

- [ ] **Step 1: 运行 ruff 检查**

```bash
uv run ruff check .
```

预期：PASS，无新增 lint 错误。

- [ ] **Step 2: 运行 mypy 类型检查**

```bash
uv run mypy src/minicode
```

预期：PASS，无新增类型错误。

- [ ] **Step 3: 运行全部测试 + 覆盖率**

```bash
uv run pytest --cov=src/minicode --cov-report=term
```

预期：全部 PASS，覆盖率不低于 75%（v0.3 发布 Gate）。

- [ ] **Step 4: 检查覆盖率报告**

确认 `src/minicode/commands/` 目录下的覆盖率：
- `base.py` 和 `registry.py` → ≥ 90%
- 各命令文件 → ≥ 80%
- 整体项目覆盖率 → ≥ 75%

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "chore: Phase 6 最终验证通过"
```

---

## 任务依赖关系

```
Task 1 (基础设施)
  ├─→ Task 2 (/quit)
  ├─→ Task 3 (/help)
  ├─→ Task 4 (/clear + ChatApp._clear_and_new_session)
  ├─→ Task 5 (/config)
  └─→ Task 6 (/session + ChatApp.switch_session)
         └─→ Task 7 (ChatApp 路由集成)
                └─→ Task 8 (集成测试)
                       └─→ Task 9 (最终验证)
```

Task 2-6 可并行开发，均依赖 Task 1。
Task 7 依赖 Task 2-6 全部完成。
Task 8 依赖 Task 7。
Task 9 是收尾验证。

---

## 预估工作量

| Task | 内容 | 预估时间 |
|------|------|---------|
| Task 1 | 基础设施 | 30 分钟 |
| Task 2 | /quit | 15 分钟 |
| Task 3 | /help | 20 分钟 |
| Task 4 | /clear | 25 分钟 |
| Task 5 | /config | 20 分钟 |
| Task 6 | /session | 60 分钟 |
| Task 7 | 路由集成 | 40 分钟 |
| Task 8 | 集成测试 | 30 分钟 |
| Task 9 | 最终验证 | 15 分钟 |
| **合计** | | **约 4 小时** |
