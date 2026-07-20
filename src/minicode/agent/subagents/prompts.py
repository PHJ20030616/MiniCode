"""Subagent 系统提示词。"""

from __future__ import annotations

from minicode.agent.subagents.models import SubagentTask
from minicode.prompts.subagent import (
    RESULT_JSON_INSTRUCTION,
    build_subagent_prompt,
)

__all__ = ["RESULT_JSON_INSTRUCTION", "build_subagent_system_prompt"]


def build_subagent_system_prompt(task: SubagentTask, allowed_tools: list[str]) -> str:
    """构建 subagent 独立系统提示词。"""
    return build_subagent_prompt(
        name=task.name,
        role=task.role.value,
        allowed_tools=allowed_tools,
        output_schema=task.output_schema,
        task=task.task,
    )
