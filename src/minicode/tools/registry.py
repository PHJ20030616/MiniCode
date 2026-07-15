"""工具注册器。

提供插件式装饰器注册、schema 导出和工具执行路由。
注册器保存工具创建工厂，而不是长期复用同一个工具实例；这样并行
subagent 执行时不会共享 workspace_root 或其它运行时状态。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from minicode.tools.base import BaseTool, ToolResult
from minicode.utils.exceptions import ToolError


class ToolDescriptor(BaseModel):
    """工具注册描述。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    tool_schema: dict = Field(default_factory=dict)
    factory: Callable[[], BaseTool]
    source: str = "builtin"


class ToolRegistry:
    """工具注册器。

    管理所有可用工具的生命周期：
    - 注册工具（装饰器模式）
    - 生成 OpenAI 兼容的 tools schema
    - 路由工具执行请求

    get_tool() 每次都会返回新实例，不保证对象身份稳定。
    """

    def __init__(self) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}

    def register(self, tool_cls: type[BaseTool]) -> type[BaseTool]:
        """装饰器：将工具类注册到注册器。"""
        probe = tool_cls()
        self.register_factory(
            name=probe.name,
            factory=tool_cls,
            schema=probe.get_tool_schema(),
            description=probe.description,
            source=f"{tool_cls.__module__}.{tool_cls.__qualname__}",
        )
        return tool_cls

    def register_tool(self, tool: BaseTool) -> BaseTool:
        """直接注册工具实例。

        为保持并行安全，注册器会用 deepcopy 在每次调用时创建独立副本。
        如果工具包含不可复制的运行时依赖，应改用 register_factory()。
        """
        self.register_factory(
            name=tool.name,
            factory=lambda: copy.deepcopy(tool),
            schema=tool.get_tool_schema(),
            description=tool.description,
            source=f"instance.{tool.__class__.__module__}.{tool.__class__.__qualname__}",
        )
        return tool

    def register_factory(
        self,
        *,
        name: str,
        factory: Callable[[], BaseTool],
        schema: dict,
        description: str | None = None,
        source: str = "runtime",
    ) -> None:
        """注册工具工厂。"""
        if name in self._descriptors:
            raise ToolError(f"工具名 '{name}' 已注册。")
        desc = description
        if desc is None:
            desc = str(schema.get("function", {}).get("description", ""))
        self._descriptors[name] = ToolDescriptor(
            name=name,
            description=desc,
            tool_schema=copy.deepcopy(schema),
            factory=factory,
            source=source,
        )

    def get_tool(self, name: str) -> BaseTool:
        """按名称创建新的工具实例。"""
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            raise ToolError(f"工具 '{name}' 未注册。")
        return descriptor.factory()

    def get_descriptor(self, name: str) -> ToolDescriptor:
        """获取工具描述符。"""
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            raise ToolError(f"工具 '{name}' 未注册。")
        return descriptor

    def get_tools_schema(self) -> list[dict]:
        """获取所有注册工具的 OpenAI 兼容 schema 列表。"""
        return [copy.deepcopy(d.tool_schema) for d in self._descriptors.values()]

    def has_tool(self, name: str) -> bool:
        """检查工具是否已注册。"""
        return name in self._descriptors

    @property
    def tool_names(self) -> list[str]:
        """获取所有已注册工具的名称列表。"""
        return list(self._descriptors.keys())

    def scope(self, allowed_tools: list[str] | set[str] | tuple[str, ...]) -> ToolRegistry:
        """创建只包含指定工具的子注册器。"""
        scoped = ToolRegistry()
        for name in allowed_tools:
            if name not in self._descriptors:
                raise ToolError(f"工具 '{name}' 未注册。")
            scoped._descriptors[name] = self._descriptors[name]
        return scoped

    async def execute_tool(
        self,
        name: str,
        args: dict,
        workspace_root: Path,
    ) -> ToolResult:
        """执行指定工具。"""
        tool = self.get_tool(name)
        tool.workspace_root = workspace_root
        try:
            return await tool.execute(**args)
        except ToolError as e:
            return ToolResult(
                success=False,
                output=str(e),
                error=str(e),
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"工具 '{name}' 执行出错：{e}",
                error=str(e),
            )
