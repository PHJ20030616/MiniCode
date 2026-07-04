"""LLM Provider 适配器。

内部消息模型采用 OpenAI 兼容风格，但设计上避免强依赖 OpenAI 独有字段，
确保可转换为 Anthropic 等其它 Provider 的消息格式。

使用方式：
    from minicode.providers.base import BaseProvider, Message, StreamChunk
    from minicode.providers.registry import ProviderRegistry
"""

from minicode.providers.base import (
    BaseProvider,
    ContentBlock,
    FunctionCall,
    Message,
    PartialToolCall,
    StreamChunk,
    ToolCall,
    ToolMessage,
    UsageInfo,
)
from minicode.providers.registry import MockProvider, ProviderRegistry

__all__ = [
    "BaseProvider",
    "ContentBlock",
    "FunctionCall",
    "Message",
    "MockProvider",
    "PartialToolCall",
    "ProviderRegistry",
    "StreamChunk",
    "ToolCall",
    "ToolMessage",
    "UsageInfo",
]
