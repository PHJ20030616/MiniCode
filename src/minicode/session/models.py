"""会话数据模型与会话消息序列化工具。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from minicode.providers.base import Message, ToolMessage


class Session(BaseModel):
    """会话数据模型。

    存储完整的对话历史，包括所有消息、工具调用和工具执行结果。
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    """会话唯一标识（32 位 hex UUID，无连字符，方便文件名使用）。"""

    name: str = ""
    """会话名称，默认使用创建时间生成。"""

    messages: list[Message] = Field(default_factory=list)
    """完整对话历史，包含 user/assistant/tool 消息及 tool_calls。"""

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    """会话创建时间（UTC）。"""

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    """会话最后更新时间（UTC）。"""

    model: str = ""
    """创建会话时使用的模型名称。"""

    provider: str = ""
    """创建会话时使用的 Provider 名称。"""

    workspace_root: str = ""
    """会话关联的工作区路径（绝对路径字符串）。"""

    metadata: dict = Field(default_factory=dict)
    """扩展元数据字段，预留后续功能使用。"""

    @property
    def message_count(self) -> int:
        """计算消息总数（不含 system prompt）。"""
        return len(self.messages)


def serialize_messages(messages: list[Message]) -> list[dict]:
    """将 Message 列表序列化为 JSON 兼容的字典列表。

    使用 Pydantic model_dump 进行深度序列化，
    ToolMessage 子类会被正确序列化（其额外约束自动包含）。

    Args:
        messages: Message / ToolMessage 列表。

    Returns:
        JSON 兼容的 dict 列表。
    """
    return [msg.model_dump(mode="json") for msg in messages]


def _has_later_committed_assistant(data: list[dict], start: int) -> bool:
    """检查指定位置之后是否存在已提交的 assistant 消息。"""
    for item in data[start:]:
        if item.get("role") != "assistant":
            continue
        content = item.get("content")
        has_text = isinstance(content, str) and bool(content.strip())
        if has_text or bool(item.get("tool_calls")):
            return True
    return False


def deserialize_messages(data: list[dict]) -> list[Message]:
    """将 JSON 字典列表反序列化为 Message 列表。

    根据 role 字段自动选择 Message 或 ToolMessage 类型：
    - role == "tool" → ToolMessage（带必填 tool_call_id）
    - 其他 role → Message

    Args:
        data: JSON 兼容的 dict 列表。

    Returns:
        Message / ToolMessage 实例列表。
    """
    result: list[Message] = []
    for index, item in enumerate(data):
        # 迁移字段写入逐项副本，避免修改调用方持有的原始 JSON 数据。
        item_data = dict(item)
        role = item_data.get("role")
        if role == "tool":
            if "consumed_by_main_model" not in item_data:
                item_data["consumed_by_main_model"] = _has_later_committed_assistant(
                    data,
                    index + 1,
                )
            result.append(ToolMessage(**item_data))
        else:
            result.append(Message(**item_data))
    return result
