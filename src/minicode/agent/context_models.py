"""上下文窗口管理的数据模型。

包含上下文配置、构建报告和构建结果的数据模型。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from minicode.providers.base import Message


class ContextConfig(BaseModel):
    """上下文窗口配置。

    max_input_tokens: 总输入预算（含 system prompt），默认 24000
    recent_messages: 尾部保留的消息数，默认 16
    max_tool_output_chars: 单条工具输出压缩阈值，默认 12000
    keep_first_user_message: 是否保留首条用户消息，默认 True
    """

    max_input_tokens: int = Field(default=24000, gt=0)
    """总输入预算（含 system prompt），必须大于 0。"""
    recent_messages: int = Field(default=16, ge=0)
    """尾部保留的消息数，必须 >= 0。"""
    max_tool_output_chars: int = Field(default=12000, gt=0)
    """单条工具输出压缩阈值，必须大于 0。"""
    keep_first_user_message: bool = True
    """是否保留首条用户消息。"""


class ContextBuildReport(BaseModel):
    """上下文构建报告。

    记录构建前后的消息数量和估算词元数，
    以及裁剪和压缩的统计信息。
    """

    original_message_count: int
    """原始消息数量。"""
    final_message_count: int
    """最终消息数量。"""
    original_estimated_tokens: int
    """原始估算词元数。"""
    final_estimated_tokens: int
    """最终估算词元数。"""
    dropped_message_count: int = 0
    """丢弃的消息数量。"""
    compressed_tool_result_count: int = 0
    """压缩的工具结果数量。"""


class ContextBuildResult(BaseModel):
    """上下文构建结果。

    包含构建后的消息列表和构建报告。
    """

    messages: list[Message]
    """构建后的消息列表（已压缩和裁剪）。"""
    report: ContextBuildReport
    """构建报告。"""
