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
