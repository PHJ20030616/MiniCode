"""对话上下文管理。

负责在对话历史前插入系统提示词，
构造发送给 Provider 的完整消息列表。
"""

from __future__ import annotations

from minicode.providers.base import Message


def build_messages(
    messages: list[Message],
    system_prompt: str,
) -> list[Message]:
    """在对话历史前插入系统提示词，构造完整的 API 消息列表。

    Args:
        messages: 当前对话历史（含 user、assistant、tool 等角色消息）。
        system_prompt: 系统提示词文本。

    Returns:
        完整的消息列表：[system, ...history]，可直接传给 provider.chat()。
    """
    return [Message(role="system", content=system_prompt), *messages]
