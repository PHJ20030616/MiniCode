"""工具定义与注册器。

提供工具基类、注册器和路径安全检查。
"""

from minicode.tools.base import BaseTool, ToolResult
from minicode.tools.file_read import ReadFile
from minicode.tools.file_write import WriteFile
from minicode.tools.glob import GlobFiles
from minicode.tools.grep import GrepFiles
from minicode.tools.registry import ToolRegistry


def register_builtin_tools(registry: ToolRegistry) -> None:
    """将所有内置工具注册到给定的注册器中。"""
    registry.register(ReadFile)
    registry.register(GlobFiles)
    registry.register(GrepFiles)
    registry.register(WriteFile)


def create_default_registry() -> ToolRegistry:
    """创建并返回一个包含所有内置工具的注册器。"""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


__all__ = [
    "BaseTool",
    "GlobFiles",
    "GrepFiles",
    "ReadFile",
    "ToolRegistry",
    "ToolResult",
    "WriteFile",
    "create_default_registry",
    "register_builtin_tools",
]
