"""上下文压缩边界与工具结果清理测试。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minicode.agent.compaction import (
    AtomicMessageGroup,
    build_atomic_groups,
    cleanup_consumed_tool_results,
    select_protected_suffix_start,
    validate_tool_protocol,
)
from minicode.agent.token_estimator import estimate_messages_tokens
from minicode.providers.base import FunctionCall, Message, ToolCall, ToolMessage


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
