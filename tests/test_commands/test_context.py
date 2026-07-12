"""/context 命令测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from minicode.commands.base import CommandContext
from minicode.commands.context_cmd import ContextCommand


class TestContextCommand:
    """ContextCommand 测试。"""

    @pytest.fixture
    def cmd(self) -> ContextCommand:
        return ContextCommand()

    @pytest.fixture
    def base_ctx(self) -> CommandContext:
        return MagicMock(spec=CommandContext)

    @pytest.mark.asyncio
    async def test_no_agent_loop_shows_prompt(self, cmd: ContextCommand) -> None:
        """无 AgentLoop 时显示提示。"""
        ctx = MagicMock(spec=CommandContext)
        ctx.agent_loop = None
        result = await cmd.execute("", ctx)
        assert result.success
        assert "尚未开始对话" in (result.message or "")

    @pytest.mark.asyncio
    async def test_no_report_shows_prompt(self, cmd: ContextCommand) -> None:
        """有 AgentLoop 但无报告时显示提示。"""
        ctx = MagicMock(spec=CommandContext)
        ctx.agent_loop = MagicMock()
        ctx.agent_loop.last_context_report = None
        result = await cmd.execute("", ctx)
        assert result.success
        assert "尚未开始对话" in (result.message or "")

    @pytest.mark.asyncio
    async def test_with_report_shows_fields(self, cmd: ContextCommand) -> None:
        """有报告时显示所有字段。"""
        from minicode.agent.context_models import ContextBuildReport

        report = ContextBuildReport(
            original_message_count=10,
            final_message_count=6,
            original_estimated_tokens=5000,
            final_estimated_tokens=2000,
            dropped_message_count=4,
            compressed_tool_result_count=2,
        )
        ctx = MagicMock(spec=CommandContext)
        ctx.agent_loop = MagicMock()
        ctx.agent_loop.last_context_report = report

        result = await cmd.execute("", ctx)
        assert result.success
        msg = result.message or ""
        assert "原始消息数" in msg
        assert "发送消息数" in msg
        assert "原始估算词元数" in msg
        assert "发送估算词元数" in msg
        assert "裁剪消息数" in msg
        assert "压缩工具结果数" in msg
        assert "10" in msg
        assert "6" in msg
        assert "5000" in msg
        assert "2000" in msg
        assert "4" in msg
        assert "2" in msg
