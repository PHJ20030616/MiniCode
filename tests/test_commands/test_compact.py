"""/compact 命令与手动上下文压缩测试。"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minicode.agent.context_models import (
    CompactionReport,
    CompactionResult,
    CompactionTrigger,
    ContextUsageReport,
)
from minicode.agent.loop import AgentLoop
from minicode.commands import register_all_commands
from minicode.commands.base import CommandContext
from minicode.commands.registry import CommandRegistry
from minicode.config.models import AppConfig
from minicode.providers.base import Message
from minicode.providers.registry import MockProvider
from minicode.tools import create_default_registry


def _compact_command() -> Any:
    """加载待实现的 CompactCommand。"""
    module = importlib.import_module("minicode.commands.compact_cmd")
    return module.CompactCommand()


def _command_context(agent_loop: Any) -> MagicMock:
    """创建仅注入 AgentLoop 的命令上下文。"""
    ctx = MagicMock(spec=CommandContext)
    ctx.agent_loop = agent_loop
    return ctx


def _compaction_report() -> CompactionReport:
    """创建稳定的压缩报告。"""
    return CompactionReport(
        trigger=CompactionTrigger.MANUAL,
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
        before_tokens=12_345,
        after_tokens=6_789,
        before_message_count=10,
        after_message_count=4,
        summarized_message_count=6,
        cleared_tool_result_count=2,
        unconsumed_tool_result_count=1,
        retry_used=False,
        target_reached=True,
        focus_provided=True,
    )


def _make_loop(tmp_path: Path) -> AgentLoop:
    """创建聚焦手动压缩行为的真实 AgentLoop。"""
    return AgentLoop(
        provider=MockProvider("摘要"),
        tool_registry=create_default_registry(),
        renderer=MagicMock(),
        config=AppConfig(),
        workspace_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_compact_requires_active_agent_loop() -> None:
    """/compact 在尚未对话时返回中文失败提示。"""
    result = await _compact_command().execute("", _command_context(None))

    assert not result.success
    assert "尚未开始对话" in (result.message or "")
    assert result.history_changed is False


@pytest.mark.asyncio
async def test_compact_forwards_focus_and_marks_history_changed() -> None:
    """/compact 去除关注说明首尾空白并标记历史已变化。"""
    agent_loop = MagicMock()
    agent_loop.compact_context = AsyncMock(
        return_value=CompactionResult(
            messages=[Message(role="user", content="摘要")],
            changed=True,
        )
    )

    result = await _compact_command().execute(
        "  保留测试结论  ",
        _command_context(agent_loop),
    )

    agent_loop.compact_context.assert_awaited_once_with("保留测试结论")
    assert result.success
    assert result.history_changed is True


@pytest.mark.asyncio
async def test_compact_empty_focus_becomes_none() -> None:
    """空白关注说明转换为 None。"""
    agent_loop = MagicMock()
    agent_loop.compact_context = AsyncMock(
        return_value=CompactionResult(messages=[], changed=False)
    )

    await _compact_command().execute("   ", _command_context(agent_loop))

    agent_loop.compact_context.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_compact_noop_does_not_request_save() -> None:
    """压缩器未改变历史时返回固定提示且不请求保存。"""
    agent_loop = MagicMock()
    agent_loop.compact_context = AsyncMock(
        return_value=CompactionResult(
            messages=[Message(role="user", content="原历史")],
            changed=False,
        )
    )

    result = await _compact_command().execute("", _command_context(agent_loop))

    assert result.success
    assert result.message == "当前没有可压缩的历史上下文。"
    assert result.history_changed is False


@pytest.mark.asyncio
async def test_compact_changed_without_report_uses_fallback_message() -> None:
    """历史已变化但无报告时显示中文回退文案。"""
    agent_loop = MagicMock()
    agent_loop.compact_context = AsyncMock(
        return_value=CompactionResult(
            messages=[Message(role="user", content="摘要")],
            changed=True,
        )
    )

    result = await _compact_command().execute("", _command_context(agent_loop))

    assert result.message == "上下文已压缩。"
    assert result.history_changed is True


@pytest.mark.asyncio
async def test_compact_changed_with_report_uses_formatter() -> None:
    """有报告时复用统一的压缩报告格式化函数。"""
    report = _compaction_report()
    agent_loop = MagicMock()
    agent_loop.compact_context = AsyncMock(
        return_value=CompactionResult(
            messages=[Message(role="user", content="摘要")],
            report=report,
            changed=True,
        )
    )

    with patch(
        "minicode.commands.compact_cmd.format_compaction_report",
        return_value="格式化压缩报告",
    ) as formatter:
        result = await _compact_command().execute(
            "",
            _command_context(agent_loop),
        )

    formatter.assert_called_once_with(report)
    assert result.message == "格式化压缩报告"
    assert result.history_changed is True


def test_register_all_commands_includes_compact() -> None:
    """/compact 注册为内置命令。"""
    register_all_commands()

    command = CommandRegistry.find("compact")

    assert command is not None
    assert command.name == "compact"


@pytest.mark.asyncio
async def test_agent_loop_compact_context_commits_changed_result(
    tmp_path: Path,
) -> None:
    """手动压缩成功后原地替换历史并更新全部压缩状态。"""
    loop = _make_loop(tmp_path)
    loop.messages.extend(
        [
            Message(role="user", content="旧问题"),
            Message(role="assistant", content="旧回答"),
        ]
    )
    original_messages = loop.messages
    report = _compaction_report()
    compacted_messages = [Message(role="user", content="压缩摘要")]
    loop.context_compactor.compact = AsyncMock(  # type: ignore[method-assign]
        return_value=CompactionResult(
            messages=compacted_messages,
            report=report,
            changed=True,
        )
    )

    result = await loop.compact_context("保留测试结论")

    assert result.changed
    assert loop.messages is original_messages
    assert loop.messages == compacted_messages
    assert loop.last_compaction_report == report
    assert loop.compaction_count == 1
    assert loop.last_context_report is not None
    assert loop.last_context_report.message_count == 1
    loop.context_compactor.compact.assert_awaited_once_with(
        messages=original_messages,
        system_prompt=loop.system_prompt,
        tools_schema=loop._get_tools_schema(),
        trigger=CompactionTrigger.MANUAL,
        focus="保留测试结论",
    )


@pytest.mark.asyncio
async def test_agent_loop_compact_context_preserves_state_when_unchanged(
    tmp_path: Path,
) -> None:
    """手动压缩 no-op 不得改写历史和压缩状态。"""
    loop = _make_loop(tmp_path)
    loop.messages.append(Message(role="user", content="当前历史"))
    original_messages = loop.messages
    previous_usage = ContextUsageReport(
        estimated_tokens=100,
        max_input_tokens=24_000,
        occupancy_ratio=100 / 24_000,
        message_count=1,
        system_tokens=50,
        message_tokens=40,
        tools_tokens=10,
        unconsumed_tool_result_count=0,
    )
    previous_report = _compaction_report()
    loop.last_context_report = previous_usage
    loop.last_compaction_report = previous_report
    loop.compaction_count = 3
    loop.context_compactor.compact = AsyncMock(  # type: ignore[method-assign]
        return_value=CompactionResult(
            messages=[Message(role="user", content="不应采用")],
            changed=False,
        )
    )

    result = await loop.compact_context(None)

    assert not result.changed
    assert loop.messages is original_messages
    assert loop.messages == [Message(role="user", content="当前历史")]
    assert loop.last_context_report is previous_usage
    assert loop.last_compaction_report is previous_report
    assert loop.compaction_count == 3
    loop.context_compactor.compact.assert_awaited_once_with(
        messages=original_messages,
        system_prompt=loop.system_prompt,
        tools_schema=loop._get_tools_schema(),
        trigger=CompactionTrigger.MANUAL,
        focus=None,
    )


def test_agent_loop_get_context_usage_uses_current_inputs(tmp_path: Path) -> None:
    """实时用量估算传入当前历史、提示词、工具定义和输入上限。"""
    loop = _make_loop(tmp_path)
    loop.messages.append(Message(role="user", content="当前问题"))
    tools_schema = [{"type": "function", "function": {"name": "read_file"}}]
    expected = ContextUsageReport(
        estimated_tokens=321,
        max_input_tokens=24_000,
        occupancy_ratio=321 / 24_000,
        message_count=1,
        system_tokens=100,
        message_tokens=200,
        tools_tokens=21,
        unconsumed_tool_result_count=0,
    )

    with (
        patch.object(loop, "_get_tools_schema", return_value=tools_schema),
        patch(
            "minicode.agent.loop.estimate_context_usage",
            return_value=expected,
        ) as estimate,
    ):
        result = loop.get_context_usage()

    assert result is expected
    estimate.assert_called_once_with(
        messages=loop.messages,
        system_prompt=loop.system_prompt,
        tools_schema=tools_schema,
        max_input_tokens=loop.config.agent.context.max_input_tokens,
    )
