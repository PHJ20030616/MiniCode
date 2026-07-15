"""Subagent 编排的数据模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from minicode.providers.base import Message


class SubagentConfig(BaseModel):
    """Subagent 编排配置。"""

    enabled: bool = False
    """是否向主 Agent 暴露 run_subagent 工具。"""
    max_agents: int = Field(default=3, ge=1, le=8)
    """单个主任务最多允许启动的 subagent 数。"""
    concurrency: int = Field(default=3, ge=1, le=8)
    """单个主任务内允许同时运行的 subagent 数。"""
    max_rounds: int = Field(default=8, ge=1, le=20)
    """单个 subagent 最大 ReAct 轮次。"""
    max_context_tokens: int = Field(default=12000, gt=0)
    """Subagent 独立上下文预算。"""
    max_result_chars: int = Field(default=8000, gt=0)
    """返回给主 Agent 的结果最大字符数。"""
    default_allowed_tools: list[str] = Field(
        default_factory=lambda: ["read_file", "grep", "glob"]
    )
    """默认只读工具集。"""
    allow_write_tools: bool = False
    """是否允许 subagent 使用写操作工具。"""


class SubagentRole(StrEnum):
    """Subagent 角色。"""

    RESEARCHER = "researcher"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    TESTER = "tester"
    GENERAL = "general"


class SubagentTask(BaseModel):
    """主 Agent 委派给 subagent 的任务。"""

    name: str = Field(min_length=1, max_length=80)
    task: str = Field(min_length=1, max_length=8000)
    role: SubagentRole = SubagentRole.GENERAL
    allowed_tools: list[str] | None = None
    max_rounds: int | None = Field(default=None, ge=1, le=20)
    output_schema: Literal[
        "summary_findings", "review_findings", "implementation_report"
    ] = "summary_findings"


class SubagentResult(BaseModel):
    """Subagent 返回给主 Agent 的结构化摘要。"""

    run_id: str
    name: str
    role: SubagentRole
    status: Literal["completed", "failed", "cancelled", "max_rounds"]
    summary: str
    findings: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    tool_call_count: int = 0
    elapsed_ms: int = 0


class SubagentRunRecord(BaseModel):
    """保存到 .minicode/subagents/runs 的精简运行记录。"""

    run_id: str
    parent_session_id: str | None = None
    parent_message_index: int | None = None
    name: str
    role: SubagentRole
    task: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None
    allowed_tools: list[str]
    started_order: int
    completed_order: int | None = None
    result: SubagentResult | None = None
    transcript: list[Message] = Field(default_factory=list)
