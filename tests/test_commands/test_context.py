"""/context 命令测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from minicode.agent.context_models import (
    CompactionReport,
    CompactionTrigger,
    ContextUsageReport,
)
from minicode.commands.base import CommandContext
from minicode.commands.context_cmd import ContextCommand
from minicode.config.models import AppConfig


def _usage_report() -> ContextUsageReport:
    """创建稳定的实时上下文用量。"""
    return ContextUsageReport(
        estimated_tokens=12_345,
        max_input_tokens=24_000,
        occupancy_ratio=0.514375,
        message_count=7,
        system_tokens=1_000,
        message_tokens=11_000,
        tools_tokens=345,
        unconsumed_tool_result_count=3,
    )


def _command_context(
    *,
    usage: ContextUsageReport | None = None,
    compaction_report: CompactionReport | None = None,
) -> MagicMock:
    """创建带实时用量和压缩状态的命令上下文。"""
    ctx = MagicMock(spec=CommandContext)
    ctx.app_config = AppConfig()
    ctx.agent_loop = MagicMock()
    ctx.agent_loop.get_context_usage.return_value = usage or _usage_report()
    ctx.agent_loop.last_compaction_report = compaction_report
    ctx.agent_loop.config = ctx.app_config
    return ctx


@pytest.mark.asyncio
async def test_context_requires_active_agent_loop() -> None:
    """无 AgentLoop 时显示中文提示。"""
    ctx = MagicMock(spec=CommandContext)
    ctx.agent_loop = None

    result = await ContextCommand().execute("", ctx)

    assert result.success
    assert "尚未开始对话" in (result.message or "")


@pytest.mark.asyncio
async def test_context_without_compaction_report_shows_live_usage() -> None:
    """无压缩报告时仍显示实时占用、阈值和消息数。"""
    ctx = _command_context()

    result = await ContextCommand().execute("", ctx)

    assert result.success
    ctx.agent_loop.get_context_usage.assert_called_once_with()
    message = result.message or ""
    assert "当前占用：12,345 / 24,000 词元（51.4%）" in message
    assert "自动压缩阈值：90.0%" in message
    assert "压缩目标：60.0%" in message
    assert "当前消息数：7" in message
    assert "最近压缩：无" in message
    assert "tokens" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trigger", "trigger_label", "retry_used", "retry_label"),
    [
        (CompactionTrigger.AUTOMATIC, "自动", False, "否"),
        (CompactionTrigger.MANUAL, "手动", True, "是"),
    ],
)
async def test_context_with_compaction_report_shows_all_fields(
    trigger: CompactionTrigger,
    trigger_label: str,
    retry_used: bool,
    retry_label: str,
) -> None:
    """自动或手动报告都显示本地时间和完整压缩统计。"""
    created_at = datetime(2026, 7, 17, 2, 3, 4, tzinfo=UTC)
    report = CompactionReport(
        trigger=trigger,
        created_at=created_at,
        before_tokens=20_000,
        after_tokens=10_000,
        before_message_count=18,
        after_message_count=6,
        summarized_message_count=12,
        cleared_tool_result_count=4,
        unconsumed_tool_result_count=5,
        retry_used=retry_used,
        target_reached=True,
        focus_provided=trigger == CompactionTrigger.MANUAL,
    )
    ctx = _command_context(compaction_report=report)

    result = await ContextCommand().execute("", ctx)

    assert result.success
    message = result.message or ""
    local_time = created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    assert f"最近压缩：{trigger_label}" in message
    assert f"压缩时间：{local_time}" in message
    assert "压缩前消息数：18" in message
    assert "当前消息数：7" in message
    assert "已清理工具结果数：4" in message
    assert "当前未消费工具结果数：3" in message
    assert f"总结重试：{retry_label}" in message
    assert "tokens" not in message
