"""主 Agent 的 subagent 委派工具。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import ValidationError

from minicode.agent.subagents.manager import format_subagent_result
from minicode.agent.subagents.models import SubagentTask
from minicode.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:
    from minicode.agent.subagents.manager import SubagentManager


class RunSubagentTool(BaseTool):
    """启动一个隔离子代理执行明确边界的子任务。"""

    name: str = "run_subagent"
    description: str = (
        "启动一个隔离的子代理执行明确边界的子任务。"
        "适合代码检索、方案评审、测试分析等任务。"
        "子代理会返回结构化中文摘要，而不是完整对话。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "子代理名称，例如：代码检索、测试分析、方案评审",
            },
            "task": {
                "type": "string",
                "description": "交给子代理执行的具体任务，必须包含边界和期望输出",
            },
            "role": {
                "type": "string",
                "enum": ["researcher", "implementer", "reviewer", "tester", "general"],
                "description": "子代理角色",
                "default": "general",
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "允许使用的工具名称。不传则使用角色默认工具集",
            },
            "max_rounds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "最大 ReAct 轮次",
            },
            "output_schema": {
                "type": "string",
                "enum": ["summary_findings", "review_findings", "implementation_report"],
                "description": "期望输出结构",
                "default": "summary_findings",
            },
        },
        "required": ["name", "task"],
        "additionalProperties": False,
    }

    def __init__(self, manager: SubagentManager) -> None:
        super().__init__()
        self.manager = manager

    @classmethod
    def get_static_schema(cls) -> dict:
        """在没有实例时生成工具 schema。"""
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": cls.parameters,
            },
        }

    async def execute(self, **kwargs: object) -> ToolResult:
        """执行一次 subagent 委派。"""
        try:
            task = SubagentTask.model_validate(kwargs)
        except ValidationError as e:
            return ToolResult(success=False, output=f"子代理参数无效：{e}", error=str(e))

        try:
            results = await self.manager.run_many([task])
        except Exception as e:
            return ToolResult(success=False, output=f"子代理执行失败：{e}", error=str(e))

        if not results:
            return ToolResult(success=False, output="子代理未返回结果。")
        result = results[0]
        output = format_subagent_result(
            result,
            self.manager.config.agent.subagents.max_result_chars,
        )
        return ToolResult(success=result.status == "completed", output=output)
