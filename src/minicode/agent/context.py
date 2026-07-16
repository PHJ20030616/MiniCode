"""对话上下文管理。

负责在对话历史前插入系统提示词，
并在配置的预算内进行工具输出压缩和旧消息裁剪。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from minicode.agent.token_estimator import (
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
)
from minicode.providers.base import Message, ToolMessage
from minicode.utils.exceptions import ContextWindowExceededError

if TYPE_CHECKING:
    from minicode.agent.context_models import (
        ContextBuildResult,
        ContextConfig,
        ContextUsageReport,
        StrictContextBuildResult,
    )

# 固定长度截断标记 —— 不含动态省略计数，确保 len(result) <= max_chars
_TRUNCATION_MARKER = "\n\n[中间内容已截断]\n\n"


def serialize_tools_schema(tools_schema: list[dict[str, object]]) -> str:
    """将工具 schema 序列化为稳定且紧凑的 JSON。"""
    return json.dumps(
        tools_schema,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def estimate_context_usage(
    messages: list[Message],
    system_prompt: str,
    tools_schema: list[dict[str, object]],
    max_input_tokens: int,
) -> ContextUsageReport:
    """估算严格上下文中 system、历史消息与工具 schema 的用量。"""
    from minicode.agent.context_models import ContextUsageReport

    system_message = Message(role="system", content=system_prompt)
    system_tokens = estimate_message_tokens(system_message)
    message_tokens = estimate_messages_tokens(messages)
    tools_tokens = estimate_tokens(serialize_tools_schema(tools_schema))
    estimated_tokens = system_tokens + message_tokens + tools_tokens
    unconsumed_tool_result_count = sum(
        1
        for message in messages
        if isinstance(message, ToolMessage)
        and not message.consumed_by_main_model
    )
    return ContextUsageReport(
        estimated_tokens=estimated_tokens,
        max_input_tokens=max_input_tokens,
        occupancy_ratio=estimated_tokens / max_input_tokens,
        message_count=len(messages),
        system_tokens=system_tokens,
        message_tokens=message_tokens,
        tools_tokens=tools_tokens,
        unconsumed_tool_result_count=unconsumed_tool_result_count,
    )


def build_strict_messages(
    messages: list[Message],
    system_prompt: str,
    tools_schema: list[dict[str, object]],
    context_config: ContextConfig,
) -> StrictContextBuildResult:
    """构建不裁剪、不压缩历史消息的主 Agent 严格上下文。"""
    from minicode.agent.context_models import StrictContextBuildResult

    usage = estimate_context_usage(
        messages,
        system_prompt,
        tools_schema,
        context_config.max_input_tokens,
    )
    if usage.estimated_tokens > usage.max_input_tokens:
        raise ContextWindowExceededError(
            "上下文估算词元数"
            f" {usage.estimated_tokens} 超过模型输入上限"
            f" {usage.max_input_tokens}"
        )

    return StrictContextBuildResult(
        messages=[Message(role="system", content=system_prompt), *messages],
        report=usage,
    )


def _compress_text(text: str, max_chars: int) -> tuple[str, bool]:
    """对超长文本做 head/tail 截断，保证输出长度不超过 max_chars。

    使用固定长度的截断标记，确保 head + marker + tail = max_chars。
    max_chars <= 0 时返回空字符串。max_chars 过小时直接硬截断。

    Args:
        text: 原始文本。
        max_chars: 最大允许字符数。

    Returns:
        (压缩后文本, 是否压缩) 元组。max_chars <= 0 时返回 ("", True)。
    """
    # max_chars <= 0：无法容纳任何内容
    if max_chars <= 0:
        return "", True

    if not text or len(text) <= max_chars:
        return text, False

    # 标记本身已超限 → 硬截断（不使用 max(1, ...)，确保 len(result) <= max_chars）
    if len(_TRUNCATION_MARKER) >= max_chars:
        return text[:max_chars], True

    available = max_chars - len(_TRUNCATION_MARKER)
    head_len = available // 2
    tail_len = available - head_len
    return text[:head_len] + _TRUNCATION_MARKER + text[-tail_len:], True


def _build_atomic_groups(messages: list[Message]) -> list[dict]:
    """将消息列表划分为原子组，同组消息要么一起保留要么一起丢弃。

    assistant(tool_calls) 与紧随其后的 tool 消息组成一个 exchange 原子组，
    其余消息各自独立成组。

    Returns:
        list[dict] — 每项包含：
        - indices: list[int] — 在原 messages 中的索引
        - priority: int — 丢弃优先级（0=最先丢弃, 2=最后丢弃）
    """
    groups: list[dict] = []
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.role == "assistant" and m.tool_calls:
            # 收集紧随其后的所有 tool 消息
            tool_indices: list[int] = []
            j = i + 1
            while j < len(messages) and messages[j].role == "tool":
                tool_indices.append(j)
                j += 1
            groups.append({
                "indices": [i] + tool_indices,
                "priority": 1,  # exchange 组视为 assistant 级别
            })
            i = j
        else:
            if m.role == "tool":
                priority = 0  # 孤立 tool 消息（异常情况）
            elif m.role == "assistant":
                priority = 1
            else:
                priority = 2
            groups.append({
                "indices": [i],
                "priority": priority,
            })
            i += 1
    return groups


def _drop_old_messages_to_budget(
    messages: list[Message],
    config: ContextConfig,
    system_message: Message,
) -> list[Message]:
    """以原子组为单位逐组丢弃旧消息直到满足预算。

    保留规则（按优先级）：
    1. system message 永远受保护
    2. 首条 user 消息（如果 keep_first_user_message=True）
    3. 尾部 recent_messages 条消息
    丢弃优先级：孤立 tool > exchange 组+assistant > user

    Args:
        messages: 原始消息列表（不含 system message）。
        config: 上下文配置。
        system_message: 系统消息。

    Returns:
        裁剪后的消息列表（含 system message）。
    """
    min_budget = max(150, config.max_input_tokens)

    current = [system_message, *messages]
    if estimate_messages_tokens(current) <= min_budget:
        return current

    # 划分原子组
    groups = _build_atomic_groups(messages)

    # 确定受保护的组索引
    protected_groups: set[int] = set()

    # 首条 user 消息所在组受保护
    if config.keep_first_user_message:
        for gi, g in enumerate(groups):
            if any(messages[idx].role == "user" for idx in g["indices"]):
                protected_groups.add(gi)
                break

    # 尾部 recent_messages 条原始消息所在的组整组受保护
    tail_start = max(0, len(messages) - config.recent_messages)
    for gi, g in enumerate(groups):
        if any(idx >= tail_start for idx in g["indices"]):
            protected_groups.add(gi)

    # 构建候选组（未受保护），按 priority + 最旧索引排序
    candidates = [
        (g["priority"], g["indices"][0], gi, g)
        for gi, g in enumerate(groups)
        if gi not in protected_groups
    ]
    candidates.sort(key=lambda x: (x[0], x[1]))

    # 逐组丢弃
    kept = [True] * len(groups)

    for _priority, _first_idx, gi, group in candidates:
        # 至少保留 1 条非 system 消息
        keep_count = sum(len(groups[k]["indices"]) for k in range(len(groups)) if kept[k])
        if keep_count - len(group["indices"]) < 1:
            break

        kept[gi] = False

        # 重新计算 token
        kept_indices: list[int] = []
        for k, keep in enumerate(kept):
            if keep:
                kept_indices.extend(groups[k]["indices"])
        kept_indices.sort()
        kept_messages = [messages[i] for i in kept_indices]

        if estimate_messages_tokens([system_message, *kept_messages]) <= min_budget:
            break

    # 组装最终结果
    kept_indices = []
    for k, keep in enumerate(kept):
        if keep:
            kept_indices.extend(groups[k]["indices"])
    kept_indices.sort()
    return [system_message] + [messages[i] for i in kept_indices]


def build_messages(
    messages: list[Message],
    system_prompt: str,
    context_config: ContextConfig | None = None,
) -> ContextBuildResult:
    """构建带预算控制的 API 消息列表。

    处理流程：
    1. 构造 system_message + messages（原始列表）
    2. 统计原始计数
    3. 对 tool 消息执行文本压缩
    4. 如超预算，执行旧消息裁剪
    5. 返回 ContextBuildResult

    Args:
        messages: 当前对话历史。
        system_prompt: 系统提示词文本。
        context_config: 上下文配置，None 时使用默认值。

    Returns:
        包含构建后消息列表和构建报告的 ContextBuildResult。
    """
    # 延迟导入避免循环依赖
    from minicode.agent.context_models import (
        ContextBuildReport,
        ContextBuildResult,
    )
    from minicode.agent.context_models import (
        ContextConfig as _ContextConfig,
    )

    config = context_config or _ContextConfig()
    system_message = Message(role="system", content=system_prompt)

    original_list = [system_message, *messages]
    original_count = len(original_list)
    original_tokens = estimate_messages_tokens(original_list)

    # 步骤 1：对 tool 消息做文本压缩
    compressed_count = 0
    compressed_messages: list[Message] = [system_message]
    for m in messages:
        if m.role == "tool" and m.content:
            content_str = m.content if isinstance(m.content, str) else ""
            compressed_text, was_compressed = _compress_text(
                content_str, config.max_tool_output_chars
            )
            if was_compressed:
                compressed_count += 1
                compressed_messages.append(
                    Message(
                        role="tool",
                        content=compressed_text,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                    )
                )
            else:
                compressed_messages.append(m)
        else:
            compressed_messages.append(m)

    # 步骤 2：检查是否超预算
    compressed_tokens = estimate_messages_tokens(compressed_messages)
    min_budget = max(150, config.max_input_tokens)

    if compressed_tokens <= min_budget:
        # 未超预算，直接返回
        final = compressed_messages
        final_tokens = compressed_tokens
        dropped_count = 0
    else:
        # 超预算，执行裁剪
        # compressed_messages[0] 是 system_message，messages 部分从 index 1 开始
        dropped = _drop_old_messages_to_budget(
            compressed_messages[1:], config, system_message
        )
        final = dropped
        final_tokens = estimate_messages_tokens(final)
        dropped_count = original_count - len(final)

    report = ContextBuildReport(
        original_message_count=original_count,
        final_message_count=len(final),
        original_estimated_tokens=original_tokens,
        final_estimated_tokens=final_tokens,
        dropped_message_count=dropped_count,
        compressed_tool_result_count=compressed_count,
    )

    return ContextBuildResult(messages=final, report=report)
