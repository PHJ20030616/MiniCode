"""上下文压缩的消息边界、清理与协议校验算法。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from minicode.agent.token_estimator import estimate_messages_tokens
from minicode.providers.base import Message, ToolMessage


@dataclass(frozen=True)
class AtomicMessageGroup:
    """不可拆分的消息区间，end 使用半开区间语义。"""

    start: int
    end: int
    estimated_tokens: int
    has_unconsumed_tool_result: bool


def _has_unconsumed_tool_result(messages: list[Message]) -> bool:
    return any(
        isinstance(message, ToolMessage) and not message.consumed_by_main_model
        for message in messages
    )


def build_atomic_groups(messages: list[Message]) -> list[AtomicMessageGroup]:
    """将消息划分为不可拆分的原子组，不修改输入消息。"""
    groups: list[AtomicMessageGroup] = []
    index = 0

    while index < len(messages):
        end = index + 1
        message = messages[index]

        if message.role == "assistant" and message.tool_calls:
            call_ids = {tool_call.id for tool_call in message.tool_calls}
            # 仅合并紧邻且属于当前 assistant 调用的结果；异常链由协议校验单独报告。
            while end < len(messages):
                candidate = messages[end]
                if candidate.role != "tool" or candidate.tool_call_id not in call_ids:
                    break
                end += 1

        group_messages = messages[index:end]
        groups.append(
            AtomicMessageGroup(
                start=index,
                end=end,
                estimated_tokens=estimate_messages_tokens(group_messages),
                has_unconsumed_tool_result=_has_unconsumed_tool_result(group_messages),
            )
        )
        index = end

    return groups


def select_protected_suffix_start(messages: list[Message], recent_budget: int) -> int:
    """返回需要完整保护的连续消息后缀起点。"""
    groups = build_atomic_groups(messages)
    if not groups:
        return 0

    budget = max(0, recent_budget)
    latest_group = groups[-1]
    protected_start = latest_group.start
    protected_tokens = latest_group.estimated_tokens

    for group in reversed(groups[:-1]):
        if protected_tokens + group.estimated_tokens > budget:
            break
        protected_start = group.start
        protected_tokens += group.estimated_tokens

    # 未消费结果不能被压缩掉，因此保护范围必须覆盖最早的未消费工具组及其后续消息。
    unconsumed_starts = [
        group.start for group in groups if group.has_unconsumed_tool_result
    ]
    if unconsumed_starts:
        protected_start = min(protected_start, min(unconsumed_starts))

    return protected_start


def cleanup_consumed_tool_results(
    messages: list[Message],
    cleanup_tools: Collection[str],
) -> tuple[list[Message], int]:
    """深复制消息，并清理白名单内已经由主模型消费的工具正文。"""
    cleaned_messages: list[Message] = []
    cleared_count = 0

    for message in messages:
        cleaned = message.model_copy(deep=True)
        if (
            isinstance(cleaned, ToolMessage)
            and cleaned.name in cleanup_tools
            and cleaned.consumed_by_main_model
            and isinstance(cleaned.content, str)
        ):
            char_count = len(cleaned.content)
            cleaned.content = (
                f"[上下文压缩：{cleaned.name} 的已消费结果已清除，"
                f"原始内容约 {char_count:,} 字符；必要时请重新读取。]"
            )
            cleared_count += 1
        cleaned_messages.append(cleaned)

    return cleaned_messages, cleared_count


def validate_tool_protocol(messages: list[Message]) -> None:
    """校验 assistant 工具调用与紧随其后的工具结果是否完整匹配。"""
    pending_call_ids: set[str] | None = None

    for index, message in enumerate(messages):
        if message.role == "tool":
            tool_call_id = message.tool_call_id
            if pending_call_ids is None or tool_call_id not in pending_call_ids:
                raise ValueError(
                    f"孤立工具结果：索引 {index} 的 tool_call_id={tool_call_id!r} "
                    "没有待匹配的工具调用。"
                )
            pending_call_ids.remove(tool_call_id)
            continue

        if pending_call_ids:
            missing_ids = ", ".join(sorted(pending_call_ids))
            raise ValueError(
                f"工具调用缺少完整的工具结果：在索引 {index} 前仍缺少 {missing_ids}。"
            )

        pending_call_ids = None
        if message.role == "assistant" and message.tool_calls:
            pending_call_ids = {tool_call.id for tool_call in message.tool_calls}

    if pending_call_ids:
        missing_ids = ", ".join(sorted(pending_call_ids))
        raise ValueError(
            f"工具调用缺少完整的工具结果：消息列表结束时仍缺少 {missing_ids}。"
        )
