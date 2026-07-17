"""/context 命令测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from minicode.agent.context_models import ContextUsageReport
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

    @pytest.mark.asyncio
    async def test_with_usage_report_shows_current_context_breakdown(
        self,
        cmd: ContextCommand,
    ) -> None:
        """新用量报告显示当前消息、预算、占用率和词元分项。"""
        report = ContextUsageReport(
            estimated_tokens=12_345,
            max_input_tokens=24_000,
            occupancy_ratio=0.514375,
            message_count=7,
            system_tokens=1_000,
            message_tokens=11_000,
            tools_tokens=345,
            unconsumed_tool_result_count=0,
        )
        ctx = MagicMock(spec=CommandContext)
        ctx.agent_loop = MagicMock()
        ctx.agent_loop.last_context_report = report

        result = await cmd.execute("", ctx)

        assert result.success
        msg = result.message or ""
        assert "当前消息数" in msg
        assert "7" in msg
        assert "估算词元" in msg
        assert "12,345 / 24,000" in msg
        assert "占用率" in msg
        assert "51.4%" in msg
        assert "系统提示词元" in msg
        assert "1,000" in msg
        assert "消息词元" in msg
        assert "11,000" in msg
        assert "工具定义词元" in msg
        assert "345" in msg
