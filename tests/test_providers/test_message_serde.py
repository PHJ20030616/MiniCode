"""消息模型序列化/反序列化 round-trip 测试。

验证 Pydantic 模型的 model_dump() / model_validate() 双向转换正确性。
覆盖 user、assistant（含 tool_calls）、tool（ToolMessage）三类消息。
"""

from __future__ import annotations

from minicode.providers.base import (
    ContentBlock,
    FunctionCall,
    Message,
    ToolCall,
    ToolMessage,
)


class TestMessageSerde:
    """Message 模型的序列化 round-trip 测试。"""

    def test_user_message_round_trip(self) -> None:
        """普通 user 消息序列化后应能还原。"""
        original = Message(role="user", content="你好，请帮我读取文件。")
        data = original.model_dump()
        restored = Message.model_validate(data)

        assert restored.role == "user"
        assert restored.content == "你好，请帮我读取文件。"
        assert restored.tool_calls is None
        assert restored.tool_call_id is None
        assert restored.name is None

    def test_system_message_round_trip(self) -> None:
        """system 消息序列化后应能还原。"""
        original = Message(role="system", content="你是一个有用的助手。")
        data = original.model_dump()
        restored = Message.model_validate(data)

        assert restored.role == "system"
        assert restored.content == "你是一个有用的助手。"

    def test_assistant_text_message_round_trip(self) -> None:
        """纯文本 assistant 消息序列化后应能还原。"""
        original = Message(role="assistant", content="这是回复。")
        data = original.model_dump()
        restored = Message.model_validate(data)

        assert restored.role == "assistant"
        assert restored.content == "这是回复。"

    def test_assistant_with_tool_calls_round_trip(self) -> None:
        """含 tool_calls 的 assistant 消息序列化后应能还原。"""
        original = Message(
            role="assistant",
            content="我来查询文件。",
            tool_calls=[
                ToolCall(
                    id="call_001",
                    function=FunctionCall(
                        name="read_file",
                        arguments='{"file_path": "test.txt"}',
                    ),
                ),
            ],
        )
        data = original.model_dump()
        restored = Message.model_validate(data)

        assert restored.role == "assistant"
        assert restored.content == "我来查询文件。"
        assert restored.tool_calls is not None
        assert len(restored.tool_calls) == 1
        assert restored.tool_calls[0].id == "call_001"
        assert restored.tool_calls[0].function.name == "read_file"
        assert restored.tool_calls[0].function.arguments == '{"file_path": "test.txt"}'

    def test_assistant_no_content_only_tool_calls(self) -> None:
        """仅含 tool_calls 无文本的 assistant 消息 round-trip 应正确。"""
        original = Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_002",
                    function=FunctionCall(name="grep", arguments='{"pattern": "TODO"}'),
                ),
            ],
        )
        data = original.model_dump()
        restored = Message.model_validate(data)

        assert restored.role == "assistant"
        assert restored.content is None
        assert restored.tool_calls is not None
        assert restored.tool_calls[0].function.name == "grep"

    def test_message_with_content_blocks_round_trip(self) -> None:
        """含 ContentBlock 列表的消息 round-trip 应正确。"""
        original = Message(
            role="user",
            content=[
                ContentBlock(type="text", text="第一段"),
                ContentBlock(type="text", text="第二段"),
            ],
        )
        data = original.model_dump()
        restored = Message.model_validate(data)

        assert restored.role == "user"
        assert restored.content is not None
        assert isinstance(restored.content, list)
        assert len(restored.content) == 2
        assert restored.content[0].text == "第一段"
        assert restored.content[1].text == "第二段"


class TestToolMessageSerde:
    """ToolMessage 模型的序列化 round-trip 测试。"""

    def test_tool_message_round_trip(self) -> None:
        """ToolMessage 序列化后应能还原。"""
        original = ToolMessage(
            content="文件内容：Hello World",
            tool_call_id="call_abc",
            name="read_file",
        )
        data = original.model_dump()
        restored = ToolMessage.model_validate(data)

        assert restored.role == "tool"
        assert restored.content == "文件内容：Hello World"
        assert restored.tool_call_id == "call_abc"
        assert restored.name == "read_file"

    def test_tool_message_no_name(self) -> None:
        """ToolMessage 不带 name 字段时应正常。"""
        original = ToolMessage(
            content="查询完成",
            tool_call_id="call_xyz",
        )
        data = original.model_dump()
        restored = ToolMessage.model_validate(data)

        assert restored.role == "tool"
        assert restored.content == "查询完成"
        assert restored.tool_call_id == "call_xyz"
        assert restored.name is None

    def test_tool_message_empty_content(self) -> None:
        """ToolMessage content 为空时应保留 None。"""
        original = ToolMessage(
            content=None,
            tool_call_id="call_empty",
        )
        data = original.model_dump()
        restored = ToolMessage.model_validate(data)

        assert restored.content is None

    def test_tool_message_serialized_as_tool_role(self) -> None:
        """ToolMessage 序列化为 dict 后 role 固定为 'tool'。"""
        msg = ToolMessage(content="数据", tool_call_id="call_001")
        data = msg.model_dump()

        assert data["role"] == "tool"
        assert data["tool_call_id"] == "call_001"
        assert "content" in data

    def test_tool_message_extra_fields_filtered(self) -> None:
        """ToolMessage 不允许包含 tool_calls 字段。"""
        msg = ToolMessage(content="结果", tool_call_id="call_001")
        data = msg.model_dump()

        # tool_calls 应为 None（由模型定义固定）
        assert "tool_calls" in data
        assert data["tool_calls"] is None


class TestCrossModelCompatibility:
    """验证 Message 和 ToolMessage 可以混合使用。"""

    def test_message_with_tool_role(self) -> None:
        """通用 Message 使用 role='tool' 仍可工作（向后兼容）。"""
        original = Message(
            role="tool",
            content="回退兼容数据",
            tool_call_id="call_fallback",
        )
        data = original.model_dump()
        restored = Message.model_validate(data)

        assert restored.role == "tool"
        assert restored.content == "回退兼容数据"
        assert restored.tool_call_id == "call_fallback"

    def test_tool_message_in_message_list(self) -> None:
        """ToolMessage 可与 Message 混合在列表中统一处理。"""
        messages: list[Message | ToolMessage] = [
            Message(role="system", content="助手"),
            Message(role="user", content="读取文件"),
            ToolMessage(content="文件内容", tool_call_id="call_001"),
        ]
        # 所有消息都应能独立 round-trip
        for msg in messages:
            data = msg.model_dump()
            if isinstance(msg, ToolMessage):
                restored = ToolMessage.model_validate(data)
                assert isinstance(restored, ToolMessage)
                assert restored.tool_call_id is not None
            else:
                restored = Message.model_validate(data)
                assert isinstance(restored, Message)
