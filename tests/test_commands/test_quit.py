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
