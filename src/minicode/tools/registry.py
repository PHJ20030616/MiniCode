"""工具注册器。

提供插件式装饰器注册、schema 导出和工具执行路由。
所有工具通过 @ToolRegistry.register 装饰器注册。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from minicode.tools.base import ToolResult
from minicode.utils.exceptions import ToolError

if TYPE_CHECKING:
    from minicode.tools.base import BaseTool


class ToolRegistry:
    """工具注册器。

    管理所有可用工具的生命周期：
    - 注册工具（装饰器模式）
    - 生成 OpenAI 兼容的 tools schema
    - 路由工具执行请求

    用法：
        registry = ToolRegistry()

        @registry.register
        class MyTool(BaseTool):
            name = "my_tool"
            ...
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool_cls: type[BaseTool]) -> type[BaseTool]:
        """装饰器：将工具类注册到注册器。

        工具无需手动实例化，注册器会在注册时创建实例。
        实例化时不传入 workspace_root，执行时再由 execute_tool 注入。

        Args:
            tool_cls: 继承 BaseTool 的工具类

        Returns:
            原工具类（不做包装或替换）
        """
        instance = tool_cls()
        if instance.name in self._tools:
            raise ToolError(
                f"工具名 '{instance.name}' 已注册，"
                f"请检查 {tool_cls.__module__}.{tool_cls.__qualname__}"
            )
        self._tools[instance.name] = instance
        return tool_cls

    def register_tool(self, tool: BaseTool) -> BaseTool:
        """直接注册工具实例。

        当需要传入自定义参数初始化工具时使用此方法。

        Args:
            tool: BaseTool 实例

        Returns:
            传入的工具实例
        """
        if tool.name in self._tools:
            raise ToolError(f"工具名 '{tool.name}' 已注册。")
        self._tools[tool.name] = tool
        return tool

    def get_tool(self, name: str) -> BaseTool:
        """按名称获取已注册的工具。

        Args:
            name: 工具名称

        Returns:
            工具实例

        Raises:
            ToolError: 工具未注册时抛出
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(f"工具 '{name}' 未注册。")
        return tool

    def get_tools_schema(self) -> list[dict]:
        """获取所有注册工具的 OpenAI 兼容 schema 列表。

        Returns:
            list[dict]: 可直接传递给 OpenAI-compatible API 的 tools 参数
        """
        return [tool.get_tool_schema() for tool in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        """检查工具是否已注册。"""
        return name in self._tools

    @property
    def tool_names(self) -> list[str]:
        """获取所有已注册工具的名称列表。"""
        return list(self._tools.keys())

    async def execute_tool(
        self,
        name: str,
        args: dict,
        workspace_root: Path,
    ) -> ToolResult:
        """执行指定工具。

        在执行前会自动设置工具的 workspace_root，确保路径安全。

        Args:
            name: 工具名称
            args: 工具参数字典
            workspace_root: 当前工作区根路径

        Returns:
            工具执行结果

        Raises:
            ToolError: 工具未注册时抛出
        """
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
