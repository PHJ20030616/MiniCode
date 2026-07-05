"""工具系统基础类定义。

提供 BaseTool 抽象基类和 ToolResult 结果模型。
所有具体工具必须继承 BaseTool 并实现 execute 方法。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel


class ToolResult(BaseModel):
    """工具执行结果。

    Attributes:
        success: 是否执行成功
        output: 执行输出文本（成功或失败时的详细信息）
        error: 错误信息（仅在失败且 output 不足时使用）
    """

    success: bool
    output: str = ""
    error: str | None = None


class BaseTool(ABC):
    """工具基类。

    所有工具必须定义 name、description、parameters，并实现 execute 方法。

    attributes 在子类中以类变量形式定义：
        name: 工具名称，用于模型调用和路由
        description: 工具功能描述，用于模型理解工具用途
        parameters: JSON Schema 格式的参数定义，直接用于构造 OpenAI function calling schema
    """

    name: str
    description: str
    parameters: dict

    def __init__(self, workspace_root: Path | None = None) -> None:
        """初始化工具。

        Args:
            workspace_root: 工作区根路径。为 None 时由 ToolRegistry 在执行时注入。
        """
        self._workspace_root = workspace_root

    @property
    def workspace_root(self) -> Path | None:
        """获取工作区根路径。"""
        return self._workspace_root

    @workspace_root.setter
    def workspace_root(self, value: Path) -> None:
        """设置工作区根路径。"""
        self._workspace_root = value

    @abstractmethod
    async def execute(self, **kwargs: object) -> ToolResult:
        """执行工具。

        Args:
            **kwargs: 工具参数，由模型根据 parameters schema 生成

        Returns:
            工具执行结果
        """
        ...

    def get_tool_schema(self) -> dict:
        """生成 OpenAI function calling 兼容的工具 schema。

        Returns:
            dict: 符合 OpenAI tools 格式的 schema
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
