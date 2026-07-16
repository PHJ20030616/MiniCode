"""上下文窗口管理的数据模型。

包含上下文配置、构建报告和构建结果的数据模型。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

from minicode.providers.base import Message

DEFAULT_CLEANUP_TOOLS = ["read_file", "grep", "glob", "shell"]


class CompactionTrigger(StrEnum):
    """上下文压缩的触发方式。"""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class CompactionConfig(BaseModel):
    """上下文压缩配置。"""

    auto_enabled: bool = True
    trigger_ratio: float = Field(default=0.90, gt=0, lt=1)
    target_ratio: float = Field(default=0.60, gt=0, lt=1)
    summary_max_tokens: int = Field(default=2048, gt=0)
    cleanup_tools: list[str] = Field(
        default_factory=lambda: list(DEFAULT_CLEANUP_TOOLS)
    )

    @field_validator("cleanup_tools")
    @classmethod
    def normalize_cleanup_tools(cls, cleanup_tools: list[str]) -> list[str]:
        """清理工具名称去除空白，并保持首次出现顺序去重。"""
        normalized: list[str] = []
        seen: set[str] = set()
        for tool_name in cleanup_tools:
            stripped_name = tool_name.strip()
            if not stripped_name:
                raise ValueError("清理工具名称不能为空")
            if stripped_name not in seen:
                normalized.append(stripped_name)
                seen.add(stripped_name)
        return normalized

    @model_validator(mode="after")
    def validate_target_ratio(self) -> Self:
        """目标占用率必须严格低于触发占用率。"""
        if self.target_ratio >= self.trigger_ratio:
            raise ValueError("目标占用率必须严格小于触发占用率")
        return self


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
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    """上下文压缩配置。"""


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


class ContextUsageReport(BaseModel):
    """严格上下文构建的用量报告。"""

    estimated_tokens: int
    max_input_tokens: int
    occupancy_ratio: float
    message_count: int
    system_tokens: int
    message_tokens: int
    tools_tokens: int
    unconsumed_tool_result_count: int


class CompactionReport(BaseModel):
    """一次上下文压缩的执行报告。"""

    trigger: CompactionTrigger
    created_at: datetime
    before_tokens: int
    after_tokens: int
    before_message_count: int
    after_message_count: int
    summarized_message_count: int
    cleared_tool_result_count: int
    unconsumed_tool_result_count: int
    retry_used: bool
    target_reached: bool
    focus_provided: bool


class CompactionResult(BaseModel):
    """上下文压缩结果。"""

    messages: list[Message]
    report: CompactionReport | None = None
    changed: bool = False


class StrictContextBuildResult(BaseModel):
    """严格上下文构建结果。"""

    messages: list[Message]
    report: ContextUsageReport
