"""任务规划的数据模型。

规划模式会在 ReAct 执行前生成一份可读计划，并把计划注入会话历史。
这些模型只描述计划本身，不直接依赖终端渲染或 Provider。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanningConfig(BaseModel):
    """Agent 规划模式配置。"""

    enabled: bool = True
    """是否在普通用户任务执行前先生成计划。默认开启以落实先计划再执行。"""
    max_steps: int = Field(default=8, ge=1, le=20)
    """单个计划最多保留的步骤数。"""
    max_tokens: int = Field(default=2048, gt=0)
    """规划阶段允许模型输出的最大 token 数。"""


class PlanStep(BaseModel):
    """执行计划中的单个步骤。"""

    index: int = Field(ge=1)
    """步骤序号，从 1 开始。"""
    title: str = Field(min_length=1)
    """步骤标题，面向用户展示。"""
    description: str = ""
    """步骤说明，补充该步骤的动作和验收点。"""
    status: Literal["pending", "in_progress", "completed", "skipped"] = "pending"
    """步骤状态，预留给后续实时进度更新。"""


class ExecutionPlan(BaseModel):
    """一次用户任务的执行计划。"""

    goal: str = Field(min_length=1)
    """计划要达成的目标。"""
    steps: list[PlanStep] = Field(min_length=1)
    """有序步骤列表。"""
    source: Literal["model", "text_fallback"] = "model"
    """计划来源：结构化模型输出或文本兜底解析。"""

    def to_markdown(self) -> str:
        """把结构化计划渲染成用户可读的中文 Markdown。"""
        lines = ["### 执行计划", "", f"目标：{self.goal}", ""]
        for step in self.steps:
            lines.append(f"{step.index}. {step.title}")
            if step.description:
                lines.append(f"   {step.description}")
        return "\n".join(lines)
