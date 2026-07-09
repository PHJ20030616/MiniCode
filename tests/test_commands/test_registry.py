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
        with pytest.raises(ValueError):
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
