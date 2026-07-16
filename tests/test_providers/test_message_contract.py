"""消息模型协议一致性测试。

验证内部消息模型可以无损转换为 Anthropic 风格的消息结构，
确保内部模型没有强依赖 OpenAI 独有字段。

覆盖四类消息结构：
1. system prompt → Anthropic system 参数
2. 普通 user/assistant 消息 → Anthropic messages
3. assistant tool call → Anthropic tool_use content block
4. tool result → Anthropic tool_result content block
"""

from __future__ import annotations

from typing import Any

from minicode.providers.base import ContentBlock, FunctionCall, Message, ToolCall, ToolMessage


class TestInternalMessageState:
    """验证上下文压缩相关的内部消息状态。"""

    def test_message_accepts_compact_summary_kind(self) -> None:
        """历史摘要消息应能标记为 compact_summary。"""
        message = Message(role="user", content="历史摘要", kind="compact_summary")

        assert message.kind == "compact_summary"

    def test_tool_message_is_unconsumed_by_default(self) -> None:
        """工具消息默认应尚未被主模型消费。"""
        message = ToolMessage(
            content="文件正文",
            tool_call_id="call_read",
            name="read_file",
        )

        assert message.consumed_by_main_model is False

    def test_tool_message_accepts_consumed_state(self) -> None:
        """工具消息应允许显式标记为已被主模型消费。"""
        message = ToolMessage(
            content="文件正文",
            tool_call_id="call_read",
            name="read_file",
            consumed_by_main_model=True,
        )

        assert message.consumed_by_main_model is True

# ─── 辅助转换函数（仅用于 contract test，非真实 Provider 实现） ───

def _convert_to_anthropic(messages: list[Message]) -> dict[str, Any]:
    """将内部消息列表转换为 Anthropic API 格式。

    注意：此函数仅用于 contract test 验证转换可行性，
    不是真实 Anthropic Provider 的实现。
    """
    system_parts: list[str] = []
    anthropic_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            # system prompt 放入单独的 system 参数
            if msg.content and isinstance(msg.content, str):
                system_parts.append(msg.content)
            continue

        if msg.role == "assistant" and msg.tool_calls:
            # assistant 含工具调用 → 转换为 text + tool_use content blocks
            blocks: list[dict[str, Any]] = []
            if msg.content and isinstance(msg.content, str):
                blocks.append({"type": "text", "text": msg.content})

            for tc in msg.tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": _parse_json_arguments(tc.function.arguments),
                })

            anthropic_messages.append({"role": "assistant", "content": blocks})
            continue

        if msg.role == "tool":
            # tool result → tool_result content block
            anthropic_messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content if isinstance(msg.content, str) else "",
                    }
                ],
            })
            continue

        # 普通 user/assistant 消息
        content: str | list[dict[str, Any]] = ""
        if msg.content is None:
            content = ""
        elif isinstance(msg.content, str):
            content = msg.content
        else:
            content = [{"type": cb.type, "text": cb.text or ""} for cb in msg.content]

        anthropic_messages.append({"role": msg.role, "content": content})

    result: dict[str, Any] = {"messages": anthropic_messages}
    if system_parts:
        result["system"] = "\n\n".join(system_parts)
    return result


def _parse_json_arguments(arguments: str) -> dict[str, Any]:
    """解析 JSON 格式的参数字符串。"""
    import json

    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return {}


# ─── 测试用例 ───


class TestSystemPromptConversion:
    """验证 system prompt 可转换为 Anthropic system 参数。"""

    def test_system_message_extracted(self) -> None:
        """system 消息应从 messages 中提取到单独的 system 字段。"""
        messages = [
            Message(role="system", content="你是一个有用的助手。"),
            Message(role="user", content="你好"),
        ]
        result = _convert_to_anthropic(messages)

        assert result["system"] == "你是一个有用的助手。"
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"

    def test_multiple_system_messages_merged(self) -> None:
        """多条 system 消息应合并为一条 system 参数。"""
        messages = [
            Message(role="system", content="第一条系统指令。"),
            Message(role="system", content="第二条系统指令。"),
            Message(role="user", content="你好"),
        ]
        result = _convert_to_anthropic(messages)

        assert "第一条系统指令。" in result["system"]
        assert "第二条系统指令。" in result["system"]

    def test_no_system_message(self) -> None:
        """没有 system 消息时不应包含 system 字段。"""
        messages = [Message(role="user", content="你好")]
        result = _convert_to_anthropic(messages)

        assert "system" not in result
        assert len(result["messages"]) == 1


class TestNormalMessageConversion:
    """验证普通 user/assistant 消息可正常转换。"""

    def test_user_text_message(self) -> None:
        """用户文本消息应正确转换。"""
        messages = [Message(role="user", content="什么是 Python？")]
        result = _convert_to_anthropic(messages)

        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "什么是 Python？"

    def test_assistant_text_message(self) -> None:
        """助手文本消息应正确转换。"""
        messages = [Message(role="assistant", content="Python 是一种编程语言。")]
        result = _convert_to_anthropic(messages)

        assert result["messages"][0]["role"] == "assistant"
        assert result["messages"][0]["content"] == "Python 是一种编程语言。"

    def test_multiple_messages_sequence(self) -> None:
        """多轮对话的消息序列应正确转换。"""
        messages = [
            Message(role="user", content="你好"),
            Message(role="assistant", content="你好！有什么可以帮助你的？"),
            Message(role="user", content="请解释什么是 AI"),
            Message(role="assistant", content="AI 是人工智能..."),
        ]
        result = _convert_to_anthropic(messages)

        assert len(result["messages"]) == 4
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][1]["role"] == "assistant"
        assert result["messages"][2]["role"] == "user"
        assert result["messages"][3]["role"] == "assistant"

    def test_user_message_with_text_only(self) -> None:
        """用户消息仅支持文本内容块。"""
        messages = [
            Message(
                role="user",
                content=[ContentBlock(type="text", text="这是一条纯文本消息")],
            )
        ]
        result = _convert_to_anthropic(messages)

        assert result["messages"][0]["role"] == "user"
        assert isinstance(result["messages"][0]["content"], list)
        assert result["messages"][0]["content"][0]["type"] == "text"
        assert result["messages"][0]["content"][0]["text"] == "这是一条纯文本消息"


class TestToolCallConversion:
    """验证 assistant 工具调用可转换为 Anthropic tool_use。"""

    def test_assistant_tool_call_converted(self) -> None:
        """含 tool_calls 的 assistant 消息应转换为 content blocks。"""
        messages = [
            Message(
                role="assistant",
                content="我来读取文件。",
                tool_calls=[
                    ToolCall(
                        id="call_123",
                        function=FunctionCall(
                            name="read_file",
                            arguments='{"file_path": "test.txt"}',
                        ),
                    )
                ],
            )
        ]
        result = _convert_to_anthropic(messages)

        msg = result["messages"][0]
        assert msg["role"] == "assistant"
        assert isinstance(msg["content"], list)
        assert len(msg["content"]) == 2  # text + tool_use

        # 验证 text block
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][0]["text"] == "我来读取文件。"

        # 验证 tool_use block
        assert msg["content"][1]["type"] == "tool_use"
        assert msg["content"][1]["id"] == "call_123"
        assert msg["content"][1]["name"] == "read_file"
        assert msg["content"][1]["input"] == {"file_path": "test.txt"}

    def test_assistant_only_tool_calls(self) -> None:
        """assistant 消息可以只有 tool_calls 没有文本。"""
        messages = [
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_456",
                        function=FunctionCall(name="grep", arguments='{"pattern": "TODO"}'),
                    )
                ],
            )
        ]
        result = _convert_to_anthropic(messages)

        msg = result["messages"][0]
        assert isinstance(msg["content"], list)
        assert len(msg["content"]) == 1
        assert msg["content"][0]["type"] == "tool_use"

    def test_multiple_tool_calls(self) -> None:
        """多个工具调用应转换为多个 tool_use blocks。"""
        messages = [
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=FunctionCall(name="read_file", arguments='{"file_path": "a.txt"}'),
                    ),
                    ToolCall(
                        id="call_2",
                        function=FunctionCall(name="glob", arguments='{"pattern": "*.py"}'),
                    ),
                ],
            )
        ]
        result = _convert_to_anthropic(messages)

        msg = result["messages"][0]
        assert len(msg["content"]) == 2
        assert msg["content"][0]["id"] == "call_1"
        assert msg["content"][1]["id"] == "call_2"


class TestToolResultConversion:
    """验证工具执行结果可转换为 Anthropic tool_result。"""

    def test_tool_result_converted(self) -> None:
        """ToolMessage 应转换为 tool_result content block。"""
        messages = [
            ToolMessage(
                content="文件内容：Hello World",
                tool_call_id="call_123",
                name="read_file",
            )
        ]
        result = _convert_to_anthropic(messages)

        msg = result["messages"][0]
        assert msg["role"] == "user"
        assert isinstance(msg["content"], list)
        assert msg["content"][0]["type"] == "tool_result"
        assert msg["content"][0]["tool_use_id"] == "call_123"
        assert msg["content"][0]["content"] == "文件内容：Hello World"

    def test_tool_result_empty_content(self) -> None:
        """工具返回空内容时也应正确转换。"""
        messages = [
            ToolMessage(
                content=None,
                tool_call_id="call_456",
            )
        ]
        result = _convert_to_anthropic(messages)

        msg = result["messages"][0]
        assert msg["content"][0]["type"] == "tool_result"
        assert msg["content"][0]["tool_use_id"] == "call_456"
        assert msg["content"][0]["content"] == ""

    def test_tool_message_role_is_fixed(self) -> None:
        """ToolMessage 的 role 应固定为 "tool"，不允许其他值。"""
        msg = ToolMessage(content="结果", tool_call_id="call_001")
        assert msg.role == "tool"

        # 验证序列化后 role 正确
        data = msg.model_dump()
        assert data["role"] == "tool"


class TestFullRoundTrip:
    """完整的对话轮次转换测试。"""

    def test_full_conversation_round_trip(self) -> None:
        """验证完整的工具调用轮次可转换为 Anthropic 格式。"""
        messages = [
            Message(role="system", content="你是一个文件助手。"),
            Message(role="user", content="帮我读取 test.txt"),
            Message(
                role="assistant",
                content="我来读取文件。",
                tool_calls=[
                    ToolCall(
                        id="call_001",
                        function=FunctionCall(
                            name="read_file",
                            arguments='{"file_path": "test.txt"}',
                        ),
                    )
                ],
            ),
            ToolMessage(
                content="Hello World",
                tool_call_id="call_001",
                name="read_file",
            ),
            Message(
                role="assistant",
                content="文件内容是：Hello World",
            ),
        ]
        result = _convert_to_anthropic(messages)

        # 验证 system
        assert result["system"] == "你是一个文件助手。"

        # 验证消息数量（system 不在 messages 中）
        assert len(result["messages"]) == 4

        # 验证 user 消息
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "帮我读取 test.txt"

        # 验证 assistant 含 tool_use
        assert result["messages"][1]["role"] == "assistant"
        assert result["messages"][1]["content"][0]["type"] == "text"
        assert result["messages"][1]["content"][1]["type"] == "tool_use"
        assert result["messages"][1]["content"][1]["name"] == "read_file"

        # 验证 tool_result
        assert result["messages"][2]["role"] == "user"
        assert result["messages"][2]["content"][0]["type"] == "tool_result"

        # 验证最终回复
        assert result["messages"][3]["role"] == "assistant"
        assert result["messages"][3]["content"] == "文件内容是：Hello World"
