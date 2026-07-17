"""Session 数据模型与序列化函数单元测试。"""

from __future__ import annotations

import json
from copy import deepcopy

from minicode.providers.base import FunctionCall, Message, ToolCall, ToolMessage
from minicode.session.models import (
    Session,
    deserialize_messages,
    serialize_messages,
)


class TestSession:
    """Session 模型测试。"""

    def test_default_values(self) -> None:
        """Session 默认值应自动生成。"""
        session = Session()
        assert session.id
        assert len(session.id) == 32  # uuid4().hex 长度为 32
        assert session.messages == []
        assert session.created_at is not None
        assert session.updated_at is not None
        assert session.model == ""
        assert session.provider == ""
        assert session.workspace_root == ""
        assert session.metadata == {}

    def test_message_count(self) -> None:
        """message_count 属性应正确计算消息数。"""
        session = Session()
        assert session.message_count == 0

        session.messages.append(Message(role="user", content="Hello"))
        assert session.message_count == 1

        session.messages.append(Message(role="assistant", content="Hi"))
        assert session.message_count == 2

    def test_model_dump_serializable(self) -> None:
        """Session.model_dump(mode="json") 的输出应可直接 json.dumps。"""
        session = Session(model="test-model", provider="test-provider")
        session.messages.append(Message(role="user", content="Hello"))
        data = session.model_dump(mode="json")
        # 应能无异常地序列化为 JSON
        json_str = json.dumps(data, ensure_ascii=False)
        assert isinstance(json_str, str)
        assert "test-model" in json_str

    def test_session_id_consistent(self) -> None:
        """创建的 Session 应具有唯一 id。"""
        s1 = Session()
        s2 = Session()
        assert s1.id != s2.id


class TestSerializeMessages:
    """序列化函数测试。"""

    def test_with_all_roles(self) -> None:
        """应正确序列化 user/assistant/system/tool 四种角色消息。"""
        messages = [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there!"),
            ToolMessage(content="result", tool_call_id="call_123"),
        ]
        serialized = serialize_messages(messages)
        assert len(serialized) == 4

        # 验证各角色
        roles = [m["role"] for m in serialized]
        assert roles == ["system", "user", "assistant", "tool"]

    def test_with_tool_calls(self) -> None:
        """应正确序列化包含 ToolCall 的消息。"""
        messages = [
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=FunctionCall(name="read_file", arguments='{"path": "test.txt"}'),
                    ),
                ],
            ),
        ]
        serialized = serialize_messages(messages)
        assert serialized[0]["tool_calls"] is not None
        assert serialized[0]["tool_calls"][0]["id"] == "call_1"
        assert serialized[0]["tool_calls"][0]["function"]["name"] == "read_file"

        # 验证可 JSON 序列化
        json.dumps(serialized, ensure_ascii=False)

    def test_with_tool_message(self) -> None:
        """ToolMessage 子类应正确序列化。"""
        messages = [
            ToolMessage(content="文件内容", tool_call_id="call_abc", name="read_file"),
        ]
        serialized = serialize_messages(messages)
        assert serialized[0]["role"] == "tool"
        assert serialized[0]["tool_call_id"] == "call_abc"
        assert serialized[0]["name"] == "read_file"

        # 验证可 JSON 序列化
        json.dumps(serialized, ensure_ascii=False)

    def test_empty_list(self) -> None:
        """空列表应返回空列表。"""
        assert serialize_messages([]) == []

    def test_json_compatible_output(self) -> None:
        """输出应可直接 json.dumps。"""
        messages = [
            Message(role="user", content="Hello"),
            ToolMessage(content="42", tool_call_id="call_1"),
        ]
        serialized = serialize_messages(messages)
        # 不应抛出异常
        json.dumps(serialized, ensure_ascii=False)


class TestDeserializeMessages:
    """反序列化函数测试。"""

    def test_detects_tool_message(self) -> None:
        """role="tool" 时应自动构造 ToolMessage。"""
        data = [
            {"role": "user", "content": "Hello"},
            {"role": "tool", "content": "42", "tool_call_id": "call_1"},
        ]
        messages = deserialize_messages(data)
        assert len(messages) == 2
        assert isinstance(messages[0], Message)
        assert isinstance(messages[1], ToolMessage)
        assert messages[1].tool_call_id == "call_1"

    def test_empty_list(self) -> None:
        """空列表应返回空列表。"""
        assert deserialize_messages([]) == []

    def test_with_tool_calls(self) -> None:
        """应正确反序列化含 tool_calls 的消息。"""
        data = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'},
                    },
                ],
            },
        ]
        messages = deserialize_messages(data)
        assert len(messages) == 1
        assert messages[0].tool_calls is not None
        assert len(messages[0].tool_calls) == 1
        assert messages[0].tool_calls[0].function.name == "read_file"

    def test_legacy_tool_result_with_later_assistant_is_inferred_consumed(self) -> None:
        """旧工具结果后有已提交 assistant 消息时应迁移为已消费。"""
        data = [
            {"role": "tool", "content": "42", "tool_call_id": "call_1"},
            {"role": "assistant", "content": "最终回答"},
        ]

        messages = deserialize_messages(data)

        assert isinstance(messages[0], ToolMessage)
        assert messages[0].consumed_by_main_model is True

    def test_legacy_trailing_tool_result_is_inferred_unconsumed(self) -> None:
        """末尾旧工具结果没有后续 assistant 提交时应保持未消费。"""
        data = [{"role": "tool", "content": "42", "tool_call_id": "call_1"}]

        messages = deserialize_messages(data)

        assert isinstance(messages[0], ToolMessage)
        assert messages[0].consumed_by_main_model is False

    def test_explicit_unconsumed_state_is_not_overridden_by_migration(self) -> None:
        """显式未消费状态优先于旧数据迁移推断。"""
        data = [
            {
                "role": "tool",
                "content": "42",
                "tool_call_id": "call_1",
                "consumed_by_main_model": False,
            },
            {"role": "assistant", "content": "最终回答"},
        ]

        messages = deserialize_messages(data)

        assert isinstance(messages[0], ToolMessage)
        assert messages[0].consumed_by_main_model is False

    def test_explicit_consumed_state_is_not_overridden_by_migration(self) -> None:
        """显式已消费状态在没有后续 assistant 时也必须保留。"""
        data = [
            {
                "role": "tool",
                "content": "42",
                "tool_call_id": "call_1",
                "consumed_by_main_model": True,
            }
        ]

        messages = deserialize_messages(data)

        assert isinstance(messages[0], ToolMessage)
        assert messages[0].consumed_by_main_model is True

    def test_later_empty_assistant_is_not_committed(self) -> None:
        """仅包含空白文本的 assistant 消息不算已提交。"""
        data = [
            {"role": "tool", "content": "42", "tool_call_id": "call_1"},
            {"role": "assistant", "content": "   "},
        ]

        messages = deserialize_messages(data)

        assert isinstance(messages[0], ToolMessage)
        assert messages[0].consumed_by_main_model is False

    def test_later_assistant_tool_calls_is_committed(self) -> None:
        """带 tool_calls 的 assistant 即使没有文本也算已提交。"""
        data = [
            {"role": "tool", "content": "42", "tool_call_id": "call_1"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
        ]

        messages = deserialize_messages(data)

        assert isinstance(messages[0], ToolMessage)
        assert messages[0].consumed_by_main_model is True

    def test_deserialize_does_not_mutate_input_dicts(self) -> None:
        """迁移旧数据时不得原地写入调用方提供的字典。"""
        data = [
            {"role": "tool", "content": "42", "tool_call_id": "call_1"},
            {"role": "assistant", "content": "最终回答"},
        ]
        original = deepcopy(data)

        deserialize_messages(data)

        assert data == original


class TestRoundtrip:
    """序列化→反序列化往返测试。"""

    def test_preserves_data(self) -> None:
        """往返应不丢失数据。"""
        original = [
            Message(role="system", content="System prompt"),
            Message(role="user", content="Hello"),
            Message(
                role="assistant",
                content="Let me check...",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=FunctionCall(name="read_file", arguments='{"path": "x.txt"}'),
                    ),
                ],
            ),
            ToolMessage(content="file content", tool_call_id="call_1", name="read_file"),
        ]

        serialized = serialize_messages(original)
        restored = deserialize_messages(serialized)

        assert len(restored) == len(original)
        assert restored[0].role == "system"
        assert restored[1].role == "user"
        assert restored[2].role == "assistant"
        assert restored[3].role == "tool"
        assert isinstance(restored[3], ToolMessage)
        assert restored[3].tool_call_id == "call_1"
        assert restored[2].tool_calls is not None
        assert restored[2].tool_calls[0].function.name == "read_file"

    def test_preserves_json_serializable(self) -> None:
        """往返后的 messages 应仍然可序列化。"""
        original = [
            Message(role="user", content="你好"),
            ToolMessage(content="结果", tool_call_id="c1"),
        ]
        serialized = serialize_messages(original)
        restored = deserialize_messages(serialized)
        re_serialized = serialize_messages(restored)
        json.dumps(re_serialized, ensure_ascii=False)

    def test_message_internal_fields_round_trip(self) -> None:
        """内部 kind 与工具消费状态必须完整往返。"""
        original = [
            Message(role="user", content="历史摘要", kind="compact_summary"),
            ToolMessage(
                content="工具结果",
                tool_call_id="call_1",
                consumed_by_main_model=True,
            ),
        ]

        restored = deserialize_messages(serialize_messages(original))

        assert restored[0].kind == "compact_summary"
        assert isinstance(restored[1], ToolMessage)
        assert restored[1].consumed_by_main_model is True
