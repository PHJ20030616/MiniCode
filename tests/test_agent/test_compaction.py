"""上下文压缩边界与工具结果清理测试。"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Coroutine
from dataclasses import FrozenInstanceError, dataclass
from datetime import timedelta
from typing import Any

import pytest

import minicode.agent.compaction as compaction
import minicode.utils.exceptions as exceptions
from minicode.agent.compaction import (
    AtomicMessageGroup,
    build_atomic_groups,
    cleanup_consumed_tool_results,
    select_protected_suffix_start,
    validate_tool_protocol,
)
from minicode.agent.context_models import (
    CompactionConfig,
    CompactionTrigger,
    ContextConfig,
)
from minicode.agent.token_estimator import estimate_messages_tokens
from minicode.providers.base import (
    BaseProvider,
    FunctionCall,
    Message,
    StreamChunk,
    ToolCall,
    ToolMessage,
)


@dataclass(frozen=True)
class SummaryChatCall:
    """一次确定性摘要请求的完整调用参数。"""

    messages: list[Message]
    tools: list[dict] | None
    stream: bool
    max_tokens: int | None


class DeterministicSummaryProvider(BaseProvider):
    """按预置响应返回摘要流，并保存每次 chat 调用的测试 Provider。"""

    def __init__(
        self,
        responses: list[list[StreamChunk]],
        *,
        return_coroutine: bool = False,
    ) -> None:
        self._responses = copy.deepcopy(responses)
        self._return_coroutine = return_coroutine
        self.calls: list[SummaryChatCall] = []

    @property
    def name(self) -> str:
        return "deterministic-summary"

    def chat(  # type: ignore[override]
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk] | Coroutine[
        Any, Any, AsyncIterator[StreamChunk]
    ]:
        """记录参数，并返回直接或协程包装的异步迭代器。"""
        self.calls.append(
            SummaryChatCall(
                messages=[message.model_copy(deep=True) for message in messages],
                tools=copy.deepcopy(tools),
                stream=stream,
                max_tokens=max_tokens,
            )
        )
        if not self._responses:
            raise AssertionError("没有预置可用的摘要响应")
        chunks = self._responses.pop(0)

        async def iterate() -> AsyncIterator[StreamChunk]:
            for chunk in chunks:
                yield chunk.model_copy(deep=True)

        if not self._return_coroutine:
            return iterate()

        async def resolve() -> AsyncIterator[StreamChunk]:
            return iterate()

        return resolve()

    async def list_models(self) -> list[str]:
        return ["deterministic-summary"]


def _tool_call(call_id: str, name: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        function=FunctionCall(name=name, arguments="{}"),
    )


def _assistant_with_tools(*calls: tuple[str, str]) -> Message:
    return Message(
        role="assistant",
        content=None,
        tool_calls=[_tool_call(call_id, name) for call_id, name in calls],
    )


def _summary_chunks(text: str) -> list[StreamChunk]:
    return [
        StreamChunk(type="text_delta", text=text),
        StreamChunk(type="done"),
    ]


def _context_config(
    *,
    max_input_tokens: int = 1000,
    summary_max_tokens: int = 64,
) -> ContextConfig:
    return ContextConfig(
        max_input_tokens=max_input_tokens,
        compaction=CompactionConfig(
            trigger_ratio=0.9,
            target_ratio=0.6,
            summary_max_tokens=summary_max_tokens,
            cleanup_tools=["read_file", "grep"],
        ),
    )


def _message_snapshot(messages: list[Message]) -> list[dict[str, object]]:
    return [message.model_dump(mode="json") for message in messages]


def test_history_snapshot_only_serializes_summary_fields() -> None:
    messages: list[Message] = [
        _assistant_with_tools(("call_read", "read_file")),
        ToolMessage(
            content="文件正文",
            tool_call_id="call_read",
            name="read_file",
            consumed_by_main_model=True,
        ),
    ]

    snapshot = compaction._history_snapshot(messages)
    payload = json.loads(snapshot)

    assert all(
        set(message) == {
            "content",
            "kind",
            "name",
            "role",
            "tool_call_id",
            "tool_calls",
        }
        for message in payload
    )
    assert payload[0]["tool_calls"][0]["function"]["name"] == "read_file"
    assert "consumed_by_main_model" not in snapshot
    assert snapshot == json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_summary_request_uses_fixed_rules_and_default_focus() -> None:
    request = compaction._summary_request(
        [Message(role="user", content="历史事实")],
        "   ",
    )

    assert len(request) == 2
    assert request[0].role == "system"
    assert request[0].content == compaction.SUMMARY_SYSTEM_PROMPT
    assert request[1].role == "user"
    assert isinstance(request[1].content, str)
    assert "无额外关注说明" in request[1].content
    assert "固定规则优先" in request[1].content
    assert "不能删除约束、失败或待办" in request[1].content
    assert "<history_snapshot>" in request[1].content
    assert "</history_snapshot>" in request[1].content


@pytest.mark.parametrize(
    "section",
    [
        "## 当前任务与最终目标",
        "## 用户明确要求和限制",
        "## 已确认的决策",
        "## 已完成工作与代码变更",
        "## 关键文件、符号和配置",
        "## 工具执行得到的有效结论",
        "## 错误、失败与未验证事项",
        "## 测试和检查结果",
        "## 尚未完成的工作",
    ],
)
def test_summary_system_prompt_contains_locked_markdown_section(
    section: str,
) -> None:
    assert section in compaction.SUMMARY_SYSTEM_PROMPT.splitlines()


@pytest.mark.parametrize("return_coroutine", [False, True])
async def test_collect_summary_accepts_both_provider_return_shapes(
    return_coroutine: bool,
) -> None:
    provider = DeterministicSummaryProvider(
        [[
            StreamChunk(type="text_delta", text="第一段"),
            StreamChunk(type="text_delta", text="第二段"),
            StreamChunk(type="done"),
            StreamChunk(type="text_delta", text="不应收集"),
        ]],
        return_coroutine=return_coroutine,
    )

    summary = await compaction._collect_summary(
        provider,
        [Message(role="system", content="摘要系统提示")],
        max_tokens=32,
    )

    assert summary == "第一段第二段"
    assert provider.calls[0].tools is None
    assert provider.calls[0].stream is False
    assert provider.calls[0].max_tokens == 32


async def test_manual_compaction_summarizes_prefix_and_keeps_latest_suffix() -> None:
    summary = "## 当前任务与最终目标\n完成上下文压缩。"
    provider = DeterministicSummaryProvider([_summary_chunks(summary)])
    context_config = _context_config()
    messages = [
        Message(role="user", content="旧需求：" + "A" * 3000),
        Message(role="assistant", content="旧实现：" + "B" * 3000),
        Message(role="user", content="最新后缀原文"),
    ]
    tools_schema = [{"type": "function", "function": {"name": "read_file"}}]

    result = await compaction.ContextCompactor(provider, context_config).compact(
        messages,
        "主系统提示",
        tools_schema,
        CompactionTrigger.MANUAL,
        focus="  关注数据库迁移  ",
    )

    assert result.changed is True
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call.tools is None
    assert call.stream is False
    assert call.max_tokens == context_config.compaction.summary_max_tokens
    assert [message.role for message in call.messages] == ["system", "user"]
    assert call.messages[0].content == compaction.SUMMARY_SYSTEM_PROMPT
    assert isinstance(call.messages[1].content, str)
    assert "<history_snapshot>" in call.messages[1].content
    assert "关注数据库迁移" in call.messages[1].content
    assert result.messages[0].kind == "compact_summary"
    assert result.messages[0].content == compaction.SUMMARY_WRAPPER_PREFIX + summary
    assert result.messages[1].model_dump() == messages[-1].model_dump()
    assert result.messages[1] is not messages[-1]
    assert result.report is not None
    assert result.report.trigger == CompactionTrigger.MANUAL
    assert result.report.before_message_count == 3
    assert result.report.after_message_count == 2
    assert result.report.summarized_message_count == 2
    assert result.report.retry_used is False
    assert result.report.focus_provided is True
    assert result.report.created_at.utcoffset() == timedelta(0)
    assert result.report.target_reached == (
        result.report.after_tokens
        <= int(context_config.max_input_tokens * context_config.compaction.target_ratio)
    )


@pytest.mark.parametrize(
    "first_response",
    [
        pytest.param(
            [
                StreamChunk(type="text_delta", text=" \n "),
                StreamChunk(type="done"),
            ],
            id="empty-summary",
        ),
        pytest.param(
            [StreamChunk(type="error", text="首次总结失败")],
            id="error-chunk",
        ),
    ],
)
async def test_failed_first_summary_retries_with_cleaned_prefix(
    first_response: list[StreamChunk],
) -> None:
    original_tool_content = "原始工具正文" * 400
    provider = DeterministicSummaryProvider(
        [first_response, _summary_chunks("## 已完成工作与代码变更\n已清理后重试。")]
    )
    messages = [
        _assistant_with_tools(("call_read", "read_file")),
        ToolMessage(
            content=original_tool_content,
            tool_call_id="call_read",
            name="read_file",
            consumed_by_main_model=True,
        ),
        Message(role="user", content="最新消息"),
    ]

    result = await compaction.ContextCompactor(
        provider,
        _context_config(max_input_tokens=500, summary_max_tokens=32),
    ).compact(
        messages,
        "主系统提示",
        [],
        CompactionTrigger.AUTOMATIC,
    )

    assert len(provider.calls) == 2
    first_request = provider.calls[0].messages[1].content
    second_request = provider.calls[1].messages[1].content
    assert isinstance(first_request, str)
    assert isinstance(second_request, str)
    assert original_tool_content in first_request
    assert original_tool_content not in second_request
    assert "已消费结果已清除" in second_request
    assert "consumed_by_main_model" not in first_request
    assert "consumed_by_main_model" not in second_request
    assert result.report is not None
    assert result.report.retry_used is True


async def test_two_summary_failures_raise_without_mutating_input() -> None:
    provider = DeterministicSummaryProvider(
        [
            [StreamChunk(type="error", text="第一次失败")],
            [
                StreamChunk(type="text_delta", text="  "),
                StreamChunk(type="done"),
            ],
        ]
    )
    messages = [
        _assistant_with_tools(("call_read", "read_file")),
        ToolMessage(
            content="原始工具正文" * 400,
            tool_call_id="call_read",
            name="read_file",
            consumed_by_main_model=True,
        ),
        Message(role="user", content="最新消息"),
    ]
    original_snapshot = _message_snapshot(messages)
    original_tool_calls = messages[0].tool_calls
    assert original_tool_calls is not None
    original_tool_call = original_tool_calls[0]

    with pytest.raises(exceptions.ContextCompactionError, match="两次尝试"):
        await compaction.ContextCompactor(
            provider,
            _context_config(max_input_tokens=500, summary_max_tokens=32),
        ).compact(
            messages,
            "主系统提示",
            [],
            CompactionTrigger.MANUAL,
        )

    assert len(provider.calls) == 2
    assert _message_snapshot(messages) == original_snapshot
    assert messages[0].tool_calls is original_tool_calls
    assert messages[0].tool_calls[0] is original_tool_call
    assert isinstance(messages[1], ToolMessage)
    assert messages[1].consumed_by_main_model is True


async def test_recompaction_rolls_existing_summary_into_single_new_summary() -> None:
    old_summary_content = (
        compaction.SUMMARY_WRAPPER_PREFIX + "旧摘要事实、限制与待办。" * 200
    )
    provider = DeterministicSummaryProvider(
        [_summary_chunks("## 尚未完成工作\n继续完成滚动摘要。")]
    )
    messages = [
        Message(
            role="user",
            kind="compact_summary",
            content=old_summary_content,
        ),
        Message(role="assistant", content="旧后续工作：" + "X" * 2000),
        Message(role="user", content="最新真实用户消息"),
    ]

    result = await compaction.ContextCompactor(
        provider,
        _context_config(max_input_tokens=800),
    ).compact(
        messages,
        "主系统提示",
        [],
        CompactionTrigger.AUTOMATIC,
    )

    request_content = provider.calls[0].messages[1].content
    assert isinstance(request_content, str)
    snapshot_json = request_content.split(
        "<history_snapshot>\n",
        maxsplit=1,
    )[1].split(
        "\n</history_snapshot>",
        maxsplit=1,
    )[0]
    snapshot_payload = json.loads(snapshot_json)
    assert snapshot_payload[0]["content"] == old_summary_content
    assert snapshot_payload[0]["kind"] == "compact_summary"
    summaries = [message for message in result.messages if message.kind == "compact_summary"]
    assert len(summaries) == 1
    assert summaries[0] is result.messages[0]
    assert summaries[0].content != old_summary_content
    assert result.report is not None
    assert result.report.summarized_message_count == 2


async def test_no_prefix_or_cleanup_returns_unchanged_deep_copy() -> None:
    provider = DeterministicSummaryProvider([])
    messages = [
        _assistant_with_tools(("call_write", "write_file")),
        ToolMessage(
            content="写入成功",
            tool_call_id="call_write",
            name="write_file",
            consumed_by_main_model=True,
        ),
        Message(role="user", content="继续"),
    ]
    original_snapshot = _message_snapshot(messages)
    original_tool_calls = messages[0].tool_calls
    assert original_tool_calls is not None

    result = await compaction.ContextCompactor(
        provider,
        _context_config(max_input_tokens=10000),
    ).compact(
        messages,
        "主系统提示",
        [],
        CompactionTrigger.MANUAL,
    )

    assert result.changed is False
    assert result.report is None
    assert provider.calls == []
    assert _message_snapshot(result.messages) == original_snapshot
    assert _message_snapshot(messages) == original_snapshot
    assert result.messages is not messages
    assert all(
        copied is not original
        for copied, original in zip(result.messages, messages, strict=True)
    )
    assert result.messages[0].tool_calls is not original_tool_calls
    assert result.messages[0].tool_calls is not None
    assert result.messages[0].tool_calls[0] is not original_tool_calls[0]


async def test_oversized_candidate_raises_without_mutating_input() -> None:
    provider = DeterministicSummaryProvider([_summary_chunks("超长摘要" * 1000)])
    messages = [
        Message(role="user", content="旧历史" + "X" * 2000),
        Message(role="user", content="最新消息"),
    ]
    original_snapshot = _message_snapshot(messages)

    with pytest.raises(
        exceptions.ContextCompactionError,
        match="超过模型输入上限",
    ):
        await compaction.ContextCompactor(
            provider,
            _context_config(max_input_tokens=120, summary_max_tokens=32),
        ).compact(
            messages,
            "主系统提示",
            [],
            CompactionTrigger.MANUAL,
        )

    assert _message_snapshot(messages) == original_snapshot


def test_atomic_message_group_is_frozen() -> None:
    group = AtomicMessageGroup(
        start=0,
        end=1,
        estimated_tokens=5,
        has_unconsumed_tool_result=False,
    )

    with pytest.raises(FrozenInstanceError):
        group.start = 1  # type: ignore[misc]


def test_build_atomic_groups_keeps_multi_tool_exchange_together() -> None:
    messages = [
        _assistant_with_tools(("call_read", "read_file"), ("call_grep", "grep")),
        ToolMessage(
            content="file body",
            tool_call_id="call_read",
            name="read_file",
            consumed_by_main_model=True,
        ),
        ToolMessage(
            content="match",
            tool_call_id="call_grep",
            name="grep",
            consumed_by_main_model=True,
        ),
        Message(role="assistant", content="处理完成"),
        Message(role="user", content="继续"),
    ]
    original_snapshot = [message.model_dump() for message in messages]

    groups = build_atomic_groups(messages)

    assert groups == [
        AtomicMessageGroup(
            start=0,
            end=3,
            estimated_tokens=estimate_messages_tokens(messages[0:3]),
            has_unconsumed_tool_result=False,
        ),
        AtomicMessageGroup(
            start=3,
            end=4,
            estimated_tokens=estimate_messages_tokens(messages[3:4]),
            has_unconsumed_tool_result=False,
        ),
        AtomicMessageGroup(
            start=4,
            end=5,
            estimated_tokens=estimate_messages_tokens(messages[4:5]),
            has_unconsumed_tool_result=False,
        ),
    ]
    assert [message.model_dump() for message in messages] == original_snapshot


def test_build_atomic_groups_marks_unconsumed_tool_result() -> None:
    messages = [
        _assistant_with_tools(("call_read", "read_file")),
        ToolMessage(
            content="pending",
            tool_call_id="call_read",
            name="read_file",
        ),
    ]

    assert build_atomic_groups(messages)[0].has_unconsumed_tool_result is True


def test_select_protected_suffix_keeps_latest_tool_group_over_budget() -> None:
    messages = [
        Message(role="user", content="旧消息"),
        _assistant_with_tools(("call_read", "read_file"), ("call_grep", "grep")),
        ToolMessage(
            content="file body",
            tool_call_id="call_read",
            name="read_file",
            consumed_by_main_model=True,
        ),
        ToolMessage(
            content="match",
            tool_call_id="call_grep",
            name="grep",
            consumed_by_main_model=True,
        ),
    ]

    assert select_protected_suffix_start(messages, recent_budget=1) == 1


def test_select_protected_suffix_expands_by_complete_groups_within_budget() -> None:
    messages = [
        Message(role="user", content="较早消息"),
        Message(role="assistant", content="应纳入预算"),
        Message(role="user", content="最新消息"),
    ]
    latest_two_tokens = estimate_messages_tokens(messages[1:])

    assert (
        select_protected_suffix_start(messages, recent_budget=latest_two_tokens)
        == 1
    )
    assert (
        select_protected_suffix_start(messages, recent_budget=latest_two_tokens - 1)
        == 2
    )


def test_select_protected_suffix_expands_to_earliest_unconsumed_group() -> None:
    messages = [
        Message(role="user", content="可丢弃"),
        _assistant_with_tools(("call_pending", "read_file")),
        ToolMessage(
            content="pending",
            tool_call_id="call_pending",
            name="read_file",
        ),
        Message(role="assistant", content="中间回复"),
        Message(role="user", content="最新消息"),
    ]

    assert select_protected_suffix_start(messages, recent_budget=1) == 1


def test_select_protected_suffix_treats_generic_tool_message_as_unconsumed() -> None:
    tool_result = Message(
        role="tool",
        content="消费状态未知",
        tool_call_id="call_pending",
        name="read_file",
    )
    messages = [
        Message(role="user", content="可丢弃"),
        _assistant_with_tools(("call_pending", "read_file")),
        tool_result,
        Message(role="assistant", content="中间回复"),
        Message(role="user", content="最新消息"),
    ]
    original_snapshot = [message.model_dump() for message in messages]

    assert select_protected_suffix_start(messages, recent_budget=1) == 1
    assert not hasattr(tool_result, "consumed_by_main_model")
    assert [message.model_dump() for message in messages] == original_snapshot


def test_select_protected_suffix_empty_messages_returns_zero() -> None:
    assert select_protected_suffix_start([], recent_budget=100) == 0


def test_cleanup_only_replaces_consumed_allowlisted_tool_results() -> None:
    messages = [
        ToolMessage(
            content="A" * 1234,
            tool_call_id="call_read",
            name="read_file",
            consumed_by_main_model=True,
        ),
        ToolMessage(
            content="未消费结果",
            tool_call_id="call_grep",
            name="grep",
        ),
        ToolMessage(
            content="写入成功",
            tool_call_id="call_write",
            name="write_file",
            consumed_by_main_model=True,
        ),
        _assistant_with_tools(("call_nested", "nested_tool")),
    ]
    original_snapshot = [message.model_dump() for message in messages]

    cleaned, cleared_count = cleanup_consumed_tool_results(
        messages,
        cleanup_tools={"read_file", "grep"},
    )

    assert cleared_count == 1
    assert cleaned[0].content == (
        "[上下文压缩：read_file 的已消费结果已清除，原始内容约 1,234 字符；"
        "必要时请重新读取。]"
    )
    assert isinstance(cleaned[0], ToolMessage)
    assert cleaned[0].tool_call_id == "call_read"
    assert cleaned[0].name == "read_file"
    assert cleaned[0].consumed_by_main_model is True
    assert cleaned[1].model_dump() == messages[1].model_dump()
    assert cleaned[2].model_dump() == messages[2].model_dump()
    assert cleaned[3].model_dump() == messages[3].model_dump()
    assert all(copy is not original for copy, original in zip(cleaned, messages, strict=True))
    assert cleaned[3].tool_calls is not messages[3].tool_calls
    assert cleaned[3].tool_calls is not None
    assert messages[3].tool_calls is not None
    assert cleaned[3].tool_calls[0] is not messages[3].tool_calls[0]
    assert [message.model_dump() for message in messages] == original_snapshot


def test_cleanup_preserves_non_string_content() -> None:
    message = ToolMessage(
        content=None,
        tool_call_id="call_read",
        name="read_file",
        consumed_by_main_model=True,
    )

    cleaned, cleared_count = cleanup_consumed_tool_results(
        [message],
        cleanup_tools={"read_file"},
    )

    assert cleared_count == 0
    assert cleaned[0].model_dump() == message.model_dump()
    assert cleaned[0] is not message


def test_validate_tool_protocol_accepts_complete_multi_tool_exchange() -> None:
    messages = [
        _assistant_with_tools(("call_read", "read_file"), ("call_grep", "grep")),
        ToolMessage(content="file body", tool_call_id="call_read", name="read_file"),
        ToolMessage(content="match", tool_call_id="call_grep", name="grep"),
        Message(role="assistant", content="处理完成"),
    ]

    validate_tool_protocol(messages)


def test_validate_tool_protocol_rejects_duplicate_call_ids() -> None:
    messages = [
        _assistant_with_tools(
            ("call_duplicate", "read_file"),
            ("call_duplicate", "grep"),
        ),
        ToolMessage(
            content="single result",
            tool_call_id="call_duplicate",
            name="read_file",
        ),
    ]

    with pytest.raises(ValueError, match="工具调用 ID 重复"):
        validate_tool_protocol(messages)


@pytest.mark.parametrize("call_id", ["", "   "])
def test_validate_tool_protocol_rejects_empty_call_id(call_id: str) -> None:
    messages = [
        _assistant_with_tools((call_id, "read_file")),
        ToolMessage(content="result", tool_call_id=call_id, name="read_file"),
    ]

    with pytest.raises(ValueError, match="工具调用 ID 不能为空"):
        validate_tool_protocol(messages)


def test_validate_tool_protocol_rejects_orphan_tool_result() -> None:
    messages: list[Message] = [
        ToolMessage(content="orphan", tool_call_id="call_read", name="read_file"),
    ]

    with pytest.raises(ValueError, match="孤立工具结果"):
        validate_tool_protocol(messages)


@pytest.mark.parametrize(
    "messages",
    [
        [
            _assistant_with_tools(("call_read", "read_file")),
            Message(role="assistant", content="过早回复"),
        ],
        [
            _assistant_with_tools(("call_read", "read_file")),
        ],
    ],
)
def test_validate_tool_protocol_rejects_missing_results(messages: list[Message]) -> None:
    with pytest.raises(ValueError, match="工具调用缺少完整的工具结果"):
        validate_tool_protocol(messages)


@pytest.mark.parametrize(
    "tool_results",
    [
        [
            ToolMessage(content="first", tool_call_id="call_read", name="read_file"),
            ToolMessage(content="duplicate", tool_call_id="call_read", name="read_file"),
        ],
        [
            ToolMessage(content="unknown", tool_call_id="call_other", name="read_file"),
        ],
    ],
)
def test_validate_tool_protocol_rejects_duplicate_or_unknown_results(
    tool_results: list[ToolMessage],
) -> None:
    messages = [
        _assistant_with_tools(("call_read", "read_file")),
        *tool_results,
    ]

    with pytest.raises(ValueError, match="孤立工具结果"):
        validate_tool_protocol(messages)
