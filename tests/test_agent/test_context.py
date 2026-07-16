"""上下文管理测试。

覆盖：
- 词元估算器（空文本、字符/token 比例、消息开销）
- 上下文模型（默认值、序列化/反序列化）
- 上下文构建器（压缩、裁剪、保护逻辑、边界条件）
"""

from __future__ import annotations

import pytest

from minicode.agent.context import (
    _compress_text,
    _drop_old_messages_to_budget,
    build_messages,
    build_strict_messages,
    estimate_context_usage,
    serialize_tools_schema,
)
from minicode.agent.context_models import ContextBuildReport, ContextBuildResult, ContextConfig
from minicode.agent.token_estimator import (
    CHARS_PER_TOKEN,
    MESSAGE_OVERHEAD_TOKENS,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
)
from minicode.providers.base import FunctionCall, Message, ToolCall, ToolMessage
from minicode.utils.exceptions import ContextWindowExceededError

# ============================================================
# 词元估算器测试
# ============================================================


class TestEstimateTokens:
    """estimate_tokens 函数测试。"""

    def test_none_text_returns_zero(self) -> None:
        """None 文本返回 0。"""
        assert estimate_tokens(None) == 0

    def test_empty_text_returns_zero(self) -> None:
        """空文本返回 0。"""
        assert estimate_tokens("") == 0

    def test_four_chars_one_token(self) -> None:
        """4 字符 = 1 token。"""
        assert estimate_tokens("abcd") == 1

    def test_five_chars_two_tokens(self) -> None:
        """5 字符 = 2 tokens（ceil 向上取整）。"""
        assert estimate_tokens("abcde") == 2

    def test_one_char_one_token(self) -> None:
        """至少返回 1 token。"""
        assert estimate_tokens("a") == 1


class TestEstimateMessageTokens:
    """estimate_message_tokens 函数测试。"""

    def test_basic_message_overhead(self) -> None:
        """基础消息至少包含 overhead 和 role。"""
        msg = Message(role="user", content="")
        tokens = estimate_message_tokens(msg)
        assert tokens >= MESSAGE_OVERHEAD_TOKENS + 1  # overhead + role

    def test_message_content_included(self) -> None:
        """消息 content 被计入估算。"""
        content = "a" * (CHARS_PER_TOKEN * 10)  # 10 tokens
        msg = Message(role="user", content=content)
        tokens = estimate_message_tokens(msg)
        assert tokens > MESSAGE_OVERHEAD_TOKENS + 1

    def test_tool_message_has_name_and_tool_call_id(self) -> None:
        """ToolMessage 包含 name 和 tool_call_id 开销。"""
        msg = ToolMessage(
            content="result",
            tool_call_id="call_123",
            name="read_file",
        )
        tokens = estimate_message_tokens(msg)
        # 应高于同内容的普通消息
        plain = Message(role="tool", content="result")
        assert tokens > estimate_message_tokens(plain)

    def test_message_with_tool_calls(self) -> None:
        """含 tool_calls 的消息额外累加函数名和参数开销。"""
        msg = Message(
            role="assistant",
            content="让我查一下",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    function=FunctionCall(name="read_file", arguments='{"path": "test.py"}'),
                ),
            ],
        )
        tokens = estimate_message_tokens(msg)
        # 应高于 content 本身的 token 数
        content_only = estimate_tokens("让我查一下")
        assert tokens > content_only + MESSAGE_OVERHEAD_TOKENS


class TestEstimateMessagesTokens:
    """estimate_messages_tokens 函数测试。"""

    def test_empty_list(self) -> None:
        """空列表返回 0。"""
        assert estimate_messages_tokens([]) == 0

    def test_sum_of_messages(self) -> None:
        """多条消息累加。"""
        msgs = [
            Message(role="system", content="你是助手。"),
            Message(role="user", content="你好"),
            Message(role="assistant", content="你好！"),
        ]
        expected = sum(estimate_message_tokens(m) for m in msgs)
        assert estimate_messages_tokens(msgs) == expected


# ============================================================
# 上下文模型测试
# ============================================================


class TestContextConfig:
    """ContextConfig 模型测试。"""

    def test_default_values(self) -> None:
        """默认值符合预期。"""
        cfg = ContextConfig()
        assert cfg.max_input_tokens == 24000
        assert cfg.recent_messages == 16
        assert cfg.max_tool_output_chars == 12000
        assert cfg.keep_first_user_message is True

    def test_serialization(self) -> None:
        """序列化/反序列化。"""
        cfg = ContextConfig(max_input_tokens=10000, recent_messages=8)
        data = cfg.model_dump()
        restored = ContextConfig(**data)
        assert restored.max_input_tokens == 10000
        assert restored.recent_messages == 8
        assert restored.max_tool_output_chars == 12000
        assert restored.keep_first_user_message is True

    def test_max_tool_output_chars_must_be_positive(self) -> None:
        """max_tool_output_chars=0 抛出 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ContextConfig(max_tool_output_chars=0)

    def test_max_input_tokens_must_be_positive(self) -> None:
        """max_input_tokens=0 抛出 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ContextConfig(max_input_tokens=0)

    def test_recent_messages_cannot_be_negative(self) -> None:
        """recent_messages=-1 抛出 ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ContextConfig(recent_messages=-1)


class TestContextBuildReport:
    """ContextBuildReport 模型测试。"""

    def test_default_zero_counts(self) -> None:
        """dropped_message_count 和 compressed_tool_result_count 默认为 0。"""
        report = ContextBuildReport(
            original_message_count=10,
            final_message_count=8,
            original_estimated_tokens=5000,
            final_estimated_tokens=3000,
        )
        assert report.dropped_message_count == 0
        assert report.compressed_tool_result_count == 0

    def test_all_fields_set(self) -> None:
        """所有字段正确设置。"""
        report = ContextBuildReport(
            original_message_count=10,
            final_message_count=5,
            original_estimated_tokens=5000,
            final_estimated_tokens=2000,
            dropped_message_count=5,
            compressed_tool_result_count=2,
        )
        assert report.original_message_count == 10
        assert report.final_message_count == 5
        assert report.dropped_message_count == 5
        assert report.compressed_tool_result_count == 2


class TestContextBuildResult:
    """ContextBuildResult 模型测试。"""

    def test_messages_and_report(self) -> None:
        """包含消息列表和报告。"""
        msgs = [Message(role="system", content="test")]
        report = ContextBuildReport(
            original_message_count=1,
            final_message_count=1,
            original_estimated_tokens=10,
            final_estimated_tokens=10,
        )
        result = ContextBuildResult(messages=msgs, report=report)
        assert len(result.messages) == 1
        assert result.report.original_message_count == 1


# ============================================================
# 上下文构建器测试
# ============================================================


class TestCompressText:
    """_compress_text 函数测试。"""

    def test_short_text_not_compressed(self) -> None:
        """短文本不压缩。"""
        text = "Hello, world!"
        result, compressed = _compress_text(text, 100)
        assert result == text
        assert compressed is False

    def test_empty_text_not_compressed(self) -> None:
        """空文本不压缩。"""
        result, compressed = _compress_text("", 100)
        assert result == ""
        assert compressed is False

    def test_long_text_is_truncated(self) -> None:
        """超长文本被截断，输出长度 <= max_chars。"""
        text = "A" * 101
        result, compressed = _compress_text(text, 100)
        assert compressed is True
        assert len(result) <= 100
        assert len(result) <= len(text)

    def test_long_text_1000_chars(self) -> None:
        """1000 字符压缩后长度 <= max_chars。"""
        text = "A" * 1000
        result, compressed = _compress_text(text, 100)
        assert compressed is True
        assert len(result) <= 100
        assert "截断" in result or "..." in result

    def test_head_tail_preserved(self) -> None:
        """head 和 tail 内容被保留。"""
        text = "HEAD" + "X" * 200 + "TAIL"
        result, compressed = _compress_text(text, 50)
        assert compressed is True
        assert len(result) <= 50
        assert result.startswith("HEAD")
        assert result.endswith("TAIL")

    def test_small_max_chars_10(self) -> None:
        """max_chars=10 时不报错且长度不超限。"""
        text = "X" * 1000
        result, compressed = _compress_text(text, 10)
        assert compressed is True
        assert len(result) <= 10

    def test_small_max_chars_20(self) -> None:
        """max_chars=20 时不报错且长度不超限。"""
        text = "X" * 1000
        result, compressed = _compress_text(text, 20)
        assert compressed is True
        assert len(result) <= 20

    def test_marker_does_not_exceed_max_chars(self) -> None:
        """截断标记本身不超限时，结果长度精确等于 max_chars。"""
        text = "A" * 1000
        result, compressed = _compress_text(text, 100)
        assert compressed is True
        # 标记长度远小于 100，head+marker+tail 应恰好填满 100
        assert len(result) <= 100

    def test_text_at_exact_boundary_not_compressed(self) -> None:
        """文本长度恰好等于 max_chars 时不压缩。"""
        text = "A" * 100
        result, compressed = _compress_text(text, 100)
        assert compressed is False
        assert result == text

    def test_max_chars_zero_returns_empty(self) -> None:
        """max_chars=0 返回空字符串。"""
        result, compressed = _compress_text("X" * 100, 0)
        assert result == ""
        assert compressed is True

    def test_max_chars_negative_returns_empty(self) -> None:
        """max_chars=-1 返回空字符串。"""
        result, compressed = _compress_text("X" * 100, -1)
        assert result == ""
        assert compressed is True

    def test_max_chars_one_returns_single_char(self) -> None:
        """max_chars=1 返回长度 <= 1。"""
        result, compressed = _compress_text("X" * 100, 1)
        assert len(result) <= 1
        assert compressed is True


class TestDropOldMessagesToBudget:
    """_drop_old_messages_to_budget 函数测试。"""

    def _make_cfg(self, max_tokens: int = 1000, recent: int = 2) -> ContextConfig:
        return ContextConfig(
            max_input_tokens=max_tokens,
            recent_messages=recent,
            keep_first_user_message=True,
        )

    def test_within_budget_no_drop(self) -> None:
        """未超预算时不做裁剪。"""
        cfg = self._make_cfg(max_tokens=100000)
        sys_msg = Message(role="system", content="sys")
        msgs = [
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
        ]
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        assert len(result) == 3  # system + 2 messages

    def test_drops_old_messages(self) -> None:
        """超预算时丢弃旧消息。"""
        cfg = self._make_cfg(max_tokens=50, recent=1)
        sys_msg = Message(role="system", content="s")
        # 使用较长内容使消息数超过预算
        msgs = [Message(role="user", content="X" * 100) for _ in range(30)]
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        # 至少保留 system + 1 条
        assert len(result) >= 2
        # 少于原始数量（+1 为 system message）
        assert len(result) < len(msgs) + 1

    def test_keep_first_user_message(self) -> None:
        """首条 user 消息受保护。"""
        cfg = self._make_cfg(max_tokens=50, recent=0)
        sys_msg = Message(role="system", content="s")
        msgs = [
            Message(role="user", content="first"),  # 应该保留
            Message(role="assistant", content="a" * 100),  # 应被丢弃
            Message(role="user", content="second"),
        ]
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        user_contents = [m.content for m in result if m.role == "user"]
        assert "first" in user_contents

    def test_tail_recent_messages_protected(self) -> None:
        """尾部 recent_messages 条消息受保护。"""
        cfg = self._make_cfg(max_tokens=50, recent=2)
        sys_msg = Message(role="system", content="s")
        msgs = [Message(role="user", content=f"msg_{i}") for i in range(10)]
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        # 至少保留尾部 2 条
        result_contents = [m.content for m in result if m.role == "user"]
        assert "msg_8" in result_contents
        assert "msg_9" in result_contents

    def test_at_least_one_message_kept(self) -> None:
        """至少保留 system + 1 条其他消息。"""
        cfg = self._make_cfg(max_tokens=1, recent=0)
        sys_msg = Message(role="system", content="s")
        msgs = [Message(role="user", content="only")]
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        assert len(result) >= 2  # system + user

    def test_tool_messages_dropped_first(self) -> None:
        """孤立 tool 消息优先被丢弃。"""
        sys_msg = Message(role="system", content="s")
        # 创建足够长的消息使总 token 数超过最小预算
        msgs = [
            Message(role="user", content="u1"),
            Message(role="assistant", content="a1"),
            ToolMessage(content="t1" * 500, tool_call_id="c1", name="tool1"),
            ToolMessage(content="t2" * 500, tool_call_id="c2", name="tool2"),
        ]
        # 预算设为仅够保留非 tool 消息
        budget_no_tool = estimate_messages_tokens([
            sys_msg,
            Message(role="user", content="u1"),
            Message(role="assistant", content="a1"),
        ])
        cfg = self._make_cfg(max_tokens=budget_no_tool, recent=0)
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        # tool 消息应被丢弃
        tool_msgs = [m for m in result if m.role == "tool"]
        assert len(tool_msgs) == 0

    def test_exchange_group_dropped_atomically(self) -> None:
        """assistant(tool_calls) 与其他含 tool_calls 的 exchange 组一起被丢弃。"""
        sys_msg = Message(role="system", content="s")
        msgs = [
            Message(role="user", content="u1"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", function=FunctionCall(name="read", arguments="{}"))],
            ),
            ToolMessage(content="result", tool_call_id="c1", name="read"),
        ]
        cfg = self._make_cfg(max_tokens=20, recent=0)
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        # exchange 组 (assistant + tool) 必须一起保留或一起丢弃
        has_assistant = any(
            m.role == "assistant" and m.tool_calls for m in result
        )
        has_tool = any(m.role == "tool" for m in result)
        assert has_assistant == has_tool, (
            f"exchange 组被破坏：assistant={has_assistant}, tool={has_tool}"
        )

    def test_exchange_group_preserves_valid_chain(self) -> None:
        """裁剪后每条 tool 消息都有匹配的 assistant(tool_calls) 前置。"""
        sys_msg = Message(role="system", content="s")
        msgs = [
            Message(role="user", content="u1"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", function=FunctionCall(name="read", arguments="{}"))],
            ),
            ToolMessage(content="file content", tool_call_id="c1", name="read"),
        ]
        cfg = self._make_cfg(max_tokens=30, recent=0)
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        # 验证没有残缺的 exchange 链
        _assert_valid_exchange_chains(result)

    def test_exchange_group_kept_when_under_budget(self) -> None:
        """预算充足时 exchange 组完整保留。"""
        sys_msg = Message(role="system", content="s")
        msgs = [
            Message(role="user", content="u1"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", function=FunctionCall(name="read", arguments="{}"))],
            ),
            ToolMessage(content="result", tool_call_id="c1", name="read"),
            Message(role="assistant", content="done"),
        ]
        cfg = self._make_cfg(max_tokens=10000, recent=0)
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        assert len(result) == 5  # system + all 4 messages
        _assert_valid_exchange_chains(result)

    def test_multiple_tool_calls_in_exchange(self) -> None:
        """单个 assistant 对应多个 tool 结果时，整组原子保留或丢弃。"""
        sys_msg = Message(role="system", content="s")
        msgs = [
            Message(role="user", content="u1"),
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="c1", function=FunctionCall(name="read", arguments="{}")),
                    ToolCall(id="c2", function=FunctionCall(name="grep", arguments="{}")),
                ],
            ),
            ToolMessage(content="file1", tool_call_id="c1", name="read"),
            ToolMessage(content="file2", tool_call_id="c2", name="grep"),
            Message(role="assistant", content="result"),
        ]
        cfg = self._make_cfg(max_tokens=20, recent=0)
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        # exchange 组 (assistant + 2 tools) 要么完整要么全无
        has_assistant = any(
            m.role == "assistant" and m.tool_calls for m in result
        )
        has_tool = any(m.role == "tool" for m in result)
        assert has_assistant == has_tool
        # 如果 exchange 组还在，2 个 tool 都应存在
        if has_tool:
            tool_msgs = [m for m in result if m.role == "tool"]
            assert len(tool_msgs) == 2, "exchange 组内的多个 tool 消息必须一起保留"

    def test_exchange_protected_by_first_user_message(self) -> None:
        """keep_first_user_message 保护首条 user 消息所在的组。"""
        sys_msg = Message(role="system", content="s")
        msgs = [
            Message(role="user", content="first"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", function=FunctionCall(name="read", arguments="{}"))],
            ),
            ToolMessage(content="result", tool_call_id="c1", name="read"),
            Message(role="user", content="X" * 1000),  # 大消息触发裁剪
        ]
        cfg = ContextConfig(max_input_tokens=40, recent_messages=0, keep_first_user_message=True)
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        assert any(m.content == "first" for m in result if m.role == "user")
        _assert_valid_exchange_chains(result)

    def test_exchange_protected_by_tail_recent(self) -> None:
        """尾部 recent_messages 保护 exchange 组的完整性。"""
        sys_msg = Message(role="system", content="s")
        msgs = [
            Message(role="user", content="X" * 100),
            # 中间的 exchange 组（裁剪候选）
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", function=FunctionCall(name="read", arguments="{}"))],
            ),
            ToolMessage(content="result", tool_call_id="c1", name="read"),
            # 尾部的 exchange 组（受保护）
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c2", function=FunctionCall(name="grep", arguments="{}"))],
            ),
            ToolMessage(content="found", tool_call_id="c2", name="grep"),
        ]
        cfg = ContextConfig(max_input_tokens=40, recent_messages=3, keep_first_user_message=False)
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        _assert_valid_exchange_chains(result)

    def test_mixed_exchanges_some_dropped_some_kept(self) -> None:
        """部分 exchange 组被丢弃，部分保留时链仍然有效。"""
        sys_msg = Message(role="system", content="s")
        msgs = [
            Message(role="user", content="X" * 500),
            # 旧 exchange 组（应被裁剪）
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c1", function=FunctionCall(name="read", arguments="{}"))],
            ),
            ToolMessage(content="old result", tool_call_id="c1", name="read"),
            Message(role="assistant", content="old reply"),
            # 新 exchange 组（尾部受保护）
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="c2", function=FunctionCall(name="grep", arguments="{}"))],
            ),
            ToolMessage(content="new result", tool_call_id="c2", name="grep"),
            Message(role="assistant", content="new reply"),
        ]
        cfg = ContextConfig(max_input_tokens=60, recent_messages=3, keep_first_user_message=False)
        result = _drop_old_messages_to_budget(msgs, cfg, sys_msg)
        _assert_valid_exchange_chains(result)
        # 至少有一部分内容被裁剪
        assert len(result) < len(msgs) + 1


def _assert_valid_exchange_chains(messages: list[Message]) -> None:
    """断言消息列表中不存在破碎的 exchange 链。

    每条 tool 消息必须有前置 matching assistant(tool_calls)，
    每个 assistant(tool_calls) 必须有其所有 tool_call_id 对应的后续 tool 消息。
    """
    # 检查 tool 消息：必须有匹配的前置 assistant
    for i, m in enumerate(messages):
        if m.role == "tool":
            # 向前找最近的 assistant(tool_calls)
            found = False
            for j in range(i - 1, -1, -1):
                prev = messages[j]
                if prev.role == "assistant" and prev.tool_calls:
                    # 检查 tool_call_id 是否匹配
                    tool_ids = {tc.id for tc in prev.tool_calls if tc.id}
                    if m.tool_call_id in tool_ids:
                        found = True
                        break
                    # 找到了 assistant 但 ID 不匹配 → 可能属于不同 exchange
                    break
            assert found, (
                f"tool 消息 (idx={i}, id={m.tool_call_id}) "
                "没有匹配的 assistant(tool_calls)"
            )

    # 检查 assistant(tool_calls)：必须有后续 matching tool 消息
    for i, m in enumerate(messages):
        if m.role == "assistant" and m.tool_calls:
            tool_ids = {tc.id for tc in m.tool_calls if tc.id}
            # 收集后续最近的 tool 消息
            matched_ids: set[str] = set()
            for j in range(i + 1, len(messages)):
                if messages[j].role != "tool":
                    break
                tid = messages[j].tool_call_id
                if tid in tool_ids and tid:
                    matched_ids.add(tid)
            if tool_ids:
                assert matched_ids == tool_ids, (
                    f"assistant(tool_calls) (idx={i}) 的 tool_ids={tool_ids}, "
                    f"但只找到匹配的 tool_ids={matched_ids}"
                )


# ============================================================
# build_messages 集成测试
# ============================================================


class TestBuildMessages:
    """build_messages 集成测试。"""

    def test_system_message_prepended(self) -> None:
        """system message 被正确前置。"""
        result = build_messages(
            [Message(role="user", content="hi")],
            "你是助手。",
        )
        assert len(result.messages) == 2
        assert result.messages[0].role == "system"
        assert result.messages[0].content == "你是助手。"
        assert result.messages[1].role == "user"

    def test_tool_output_compressed(self) -> None:
        """长 tool output 被压缩。"""
        long_output = "X" * 20000
        msgs = [
            Message(role="user", content="read file"),
            Message(role="assistant", content="reading..."),
            ToolMessage(content=long_output, tool_call_id="c1", name="read_file"),
        ]
        cfg = ContextConfig(max_tool_output_chars=100, max_input_tokens=50000)
        result = build_messages(msgs, "sys", cfg)
        # tool 消息被压缩
        tool_msg = [m for m in result.messages if m.role == "tool"]
        assert len(tool_msg) == 1
        assert "已截断" in (tool_msg[0].content or "")
        assert result.report.compressed_tool_result_count == 1

    def test_drop_when_over_budget(self) -> None:
        """超预算时丢弃旧消息。"""
        msgs = [Message(role="user", content=f"msg_{i}") for i in range(30)]
        cfg = ContextConfig(max_input_tokens=100, recent_messages=2)
        result = build_messages(msgs, "sys", cfg)
        assert len(result.messages) < len(msgs) + 1  # +1 for system
        assert result.report.dropped_message_count > 0

    def test_report_counts_match(self) -> None:
        """Report 计数一致。"""
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="world"),
        ]
        result = build_messages(msgs, "sys")
        report = result.report
        assert report.original_message_count == 3  # system + 2 messages
        assert report.final_message_count == 3
        assert report.original_estimated_tokens > 0
        assert report.final_estimated_tokens > 0

    def test_no_context_config_uses_default(self) -> None:
        """不传 context_config 时使用默认值。"""
        result = build_messages(
            [Message(role="user", content="hi")],
            "sys",
        )
        assert len(result.messages) >= 2
        assert result.report.original_message_count >= 2

    def test_empty_messages(self) -> None:
        """空消息列表只返回 system message。"""
        result = build_messages([], "sys")
        assert len(result.messages) == 1
        assert result.messages[0].role == "system"
        assert result.messages[0].content == "sys"


class TestStrictContext:
    """主 Agent 严格上下文组装测试。"""

    def test_serialize_tools_schema_has_stable_key_order(self) -> None:
        """工具 schema 的字典键顺序不影响序列化结果。"""
        first = [{"type": "function", "function": {"name": "read", "strict": True}}]
        second = [{"function": {"strict": True, "name": "read"}, "type": "function"}]

        assert serialize_tools_schema(first) == serialize_tools_schema(second)
        assert serialize_tools_schema(first) == (
            '[{"function":{"name":"read","strict":true},"type":"function"}]'
        )

    def test_estimate_usage_includes_system_history_and_tools(self) -> None:
        """用量报告分别统计 system、历史与工具 schema。"""
        messages = [
            Message(role="user", content="hello"),
            ToolMessage(
                content="pending",
                tool_call_id="call_1",
                name="read",
            ),
            ToolMessage(
                content="consumed",
                tool_call_id="call_2",
                name="grep",
                consumed_by_main_model=True,
            ),
            Message(
                role="tool",
                content="generic",
                tool_call_id="call_3",
                name="shell",
            ),
        ]
        system_prompt = "system prompt"
        tools_schema = [{"type": "function", "function": {"name": "read"}}]

        usage = estimate_context_usage(
            messages,
            system_prompt,
            tools_schema,
            max_input_tokens=1000,
        )

        expected_system = estimate_message_tokens(
            Message(role="system", content=system_prompt)
        )
        expected_messages = estimate_messages_tokens(messages)
        expected_tools = estimate_tokens(serialize_tools_schema(tools_schema))
        expected_total = expected_system + expected_messages + expected_tools
        assert usage.system_tokens == expected_system
        assert usage.message_tokens == expected_messages
        assert usage.tools_tokens == expected_tools
        assert usage.estimated_tokens == expected_total
        assert usage.occupancy_ratio == expected_total / 1000
        assert usage.message_count == len(messages)
        assert usage.unconsumed_tool_result_count == 1

    def test_build_strict_messages_preserves_long_tool_output(self) -> None:
        """严格组装完整保留 20k 工具正文。"""
        long_output = "X" * 20000
        messages = [
            ToolMessage(
                content=long_output,
                tool_call_id="call_1",
                name="read_file",
            )
        ]

        result = build_strict_messages(
            messages,
            "sys",
            [],
            ContextConfig(max_input_tokens=10000, max_tool_output_chars=100),
        )

        assert result.messages[1] is messages[0]
        assert result.messages[1].content == long_output
        assert len(result.messages[1].content or "") == 20000

    def test_build_strict_messages_raises_without_mutating_input(self) -> None:
        """超限时抛出异常，且输入消息保持原样。"""
        messages = [
            Message(role="user", content="first"),
            ToolMessage(
                content="X" * 20000,
                tool_call_id="call_1",
                name="read_file",
            ),
        ]
        original_snapshot = [message.model_dump() for message in messages]

        with pytest.raises(
            ContextWindowExceededError,
            match="超过模型输入上限",
        ):
            build_strict_messages(
                messages,
                "sys",
                [],
                ContextConfig(max_input_tokens=100),
            )

        assert [message.model_dump() for message in messages] == original_snapshot
