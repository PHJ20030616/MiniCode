"""词元估算工具。

提供基于字符比例的轻量级词元估算函数。
使用保守的 4 字符/token 比例（比实际模型更保守，确保不会低估）。
"""

from __future__ import annotations

import math

from minicode.providers.base import ContentBlock, Message

# 保守的字符/词元比例（比大多数模型的 3:1 更保守）
CHARS_PER_TOKEN = 4
# 每条消息的固定结构开销（role、元数据等）
MESSAGE_OVERHEAD_TOKENS = 4


def _get_text_content(content: str | list[ContentBlock] | None) -> str | None:
    """从消息 content 中提取文本内容。

    Args:
        content: 消息 content，可为 str、ContentBlock 列表或 None。

    Returns:
        纯文本内容，None 表示无内容。
    """
    if content is None:
        return None
    if isinstance(content, str):
        return content
    # ContentBlock 列表：拼接各块的文本
    texts = [b.text for b in content if b.text]
    return "".join(texts) if texts else None


def estimate_tokens(text: str | None) -> int:
    """估算单段文本的词元数。

    Args:
        text: 输入文本，可为 None 或空字符串。

    Returns:
        估算的词元数。空/None 文本返回 0。
    """
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def estimate_message_tokens(message: Message) -> int:
    """估算单条消息的词元数。

    包含固定结构开销、content、role、name、tool_call_id
    以及 tool_calls 的 JSON 表示。

    Args:
        message: 消息对象。

    Returns:
        估算的词元数。
    """
    total = MESSAGE_OVERHEAD_TOKENS

    # content 开销（支持 str 和 ContentBlock 列表）
    text_content = _get_text_content(message.content)
    total += estimate_tokens(text_content)

    # role 开销（固定值，估算为 1 token）
    total += 1

    # name 开销（仅 tool 消息有）
    if message.name:
        total += estimate_tokens(message.name)

    # tool_call_id 开销
    if message.tool_call_id:
        total += estimate_tokens(message.tool_call_id)

    # tool_calls JSON 开销
    if message.tool_calls:
        for tc in message.tool_calls:
            total += estimate_tokens(tc.function.name)
            total += estimate_tokens(tc.function.arguments)
            total += 1  # tool_call id 开销
            total += 1  # tool_call type 开销

    return total


def estimate_messages_tokens(messages: list[Message]) -> int:
    """逐条累加估算整个消息列表的词元数。

    Args:
        messages: 消息列表。

    Returns:
        总估算词元数。
    """
    return sum(estimate_message_tokens(m) for m in messages)
