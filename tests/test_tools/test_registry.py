"""工具注册器单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.tools.base import BaseTool, ToolResult
from minicode.tools.registry import ToolRegistry
from minicode.utils.exceptions import ToolError


class HelperTool(BaseTool):
    """用于测试的简单工具。"""

    name: str = "test_tool"
    description: str = "测试工具"
    parameters: dict = {
        "type": "object",
        "properties": {
            "msg": {"type": "string", "description": "消息内容"},
        },
        "required": ["msg"],
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        msg = kwargs.get("msg", "no message")
        return ToolResult(success=True, output=f"executed: {msg}")


class HelperEchoTool(BaseTool):
    """另一个测试工具。"""

    name: str = "echo"
    description: str = "回显工具"
    parameters: dict = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要回显的文本"},
        },
        "required": ["text"],
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        text = kwargs.get("text", "")
        return ToolResult(success=True, output=text)


class HelperFailingTool(BaseTool):
    """执行时总是失败的测试工具。"""

    name: str = "fail_tool"
    description: str = "总是失败的工具"
    parameters: dict = {
        "type": "object",
        "properties": {},
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=False, output="执行失败", error="发生了错误")


class HelperCrashingTool(BaseTool):
    """执行时抛出异常的测试工具。"""

    name: str = "crash_tool"
    description: str = "会崩溃的工具"
    parameters: dict = {
        "type": "object",
        "properties": {},
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        msg = "crash!"
        raise ValueError(msg)


class HelperToolErrorTool(BaseTool):
    """执行时抛出 ToolError 的测试工具。"""

    name: str = "tool_error_tool"
    description: str = "会抛出 ToolError 的工具"
    parameters: dict = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "错误消息"},
        },
        "required": ["message"],
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        msg = str(kwargs.get("message", "default error"))
        raise ToolError(msg)


class TrackingWorkspaceTool(BaseTool):
    """记录实际执行实例的 workspace_root，用于验证工厂化隔离。"""

    name: str = "tracking_workspace"
    description: str = "记录执行工作区"
    parameters: dict = {
        "type": "object",
        "properties": {},
    }
    seen_roots: list[Path | None] = []

    async def execute(self, **kwargs: object) -> ToolResult:
        self.__class__.seen_roots.append(self.workspace_root)
        return ToolResult(success=True, output="ok")


class TestToolRegistry:
    """ToolRegistry 核心功能测试。"""

    def test_register_and_get(self) -> None:
        registry = ToolRegistry()
        registry.register(HelperTool)
        tool = registry.get_tool("test_tool")
        assert isinstance(tool, HelperTool)
        assert tool.name == "test_tool"

    def test_register_duplicate_raises(self) -> None:
        registry = ToolRegistry()
        registry.register(HelperTool)
        with pytest.raises(ToolError, match="已注册"):
            registry.register(HelperTool)

    def test_get_unregistered_tool_raises(self) -> None:
        registry = ToolRegistry()
        with pytest.raises(ToolError, match="未注册"):
            registry.get_tool("nonexistent")

    def test_register_tool_instance(self) -> None:
        registry = ToolRegistry()
        tool = HelperTool()
        registry.register_tool(tool)
        registered = registry.get_tool("test_tool")
        assert isinstance(registered, HelperTool)
        assert registered is not tool
        assert registered.name == tool.name

    def test_get_tool_returns_new_instance_each_time(self) -> None:
        registry = ToolRegistry()
        registry.register(HelperTool)

        first = registry.get_tool("test_tool")
        second = registry.get_tool("test_tool")

        assert isinstance(first, HelperTool)
        assert isinstance(second, HelperTool)
        assert first is not second

    def test_scope_keeps_selected_descriptors(self) -> None:
        registry = ToolRegistry()
        registry.register(HelperTool)
        registry.register(HelperEchoTool)

        scoped = registry.scope(["echo"])

        assert scoped.tool_names == ["echo"]
        assert scoped.has_tool("echo") is True
        assert scoped.has_tool("test_tool") is False

    def test_has_tool(self) -> None:
        registry = ToolRegistry()
        registry.register(HelperTool)
        assert registry.has_tool("test_tool") is True
        assert registry.has_tool("nonexistent") is False

    def test_tool_names(self) -> None:
        registry = ToolRegistry()
        registry.register(HelperTool)
        registry.register(HelperEchoTool)
        names = registry.tool_names
        assert "test_tool" in names
        assert "echo" in names
        assert len(names) == 2

    def test_get_tools_schema(self) -> None:
        registry = ToolRegistry()
        registry.register(HelperTool)
        registry.register(HelperEchoTool)

        schema = registry.get_tools_schema()

        assert len(schema) == 2
        for s in schema:
            assert s["type"] == "function"
            assert "function" in s
            assert s["function"]["name"] in ("test_tool", "echo")
            assert "parameters" in s["function"]

    def test_schema_openai_compatible(self) -> None:
        """验证 schema 可直接传给 OpenAI-compatible API。"""
        registry = ToolRegistry()
        registry.register(HelperTool)

        schema = registry.get_tools_schema()
        # 这只是验证 schema 结构可以被 OpenAI SDK 接受
        tool_call = {
            "id": "call_123",
            "type": "function",
            "function": {
                "name": schema[0]["function"]["name"],
                "arguments": '{"msg": "hello"}',
            },
        }
        assert tool_call["function"]["name"] == "test_tool"
        assert tool_call["type"] == "function"


class TestToolExecution:
    """工具执行功能测试。"""

    @pytest.mark.asyncio
    async def test_execute_success(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        registry.register(HelperTool)

        result = await registry.execute_tool(
            "test_tool", {"msg": "hello"}, tmp_path
        )
        assert result.success is True
        assert result.output == "executed: hello"

    @pytest.mark.asyncio
    async def test_execute_failure(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        registry.register(HelperFailingTool)

        result = await registry.execute_tool("fail_tool", {}, tmp_path)
        assert result.success is False
        assert result.output == "执行失败"

    @pytest.mark.asyncio
    async def test_execute_tool_injects_workspace(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        TrackingWorkspaceTool.seen_roots.clear()
        registry.register(TrackingWorkspaceTool)

        await registry.execute_tool("tracking_workspace", {}, tmp_path)
        fresh_tool = registry.get_tool("tracking_workspace")

        assert TrackingWorkspaceTool.seen_roots == [tmp_path]
        assert fresh_tool.workspace_root is None

    @pytest.mark.asyncio
    async def test_execute_unregistered_raises(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        with pytest.raises(ToolError, match="未注册"):
            await registry.execute_tool("nonexistent", {}, tmp_path)

    @pytest.mark.asyncio
    async def test_execute_crashing_tool_returns_result(self, tmp_path: Path) -> None:
        """工具内部抛出异常时，应返回 ToolResult 而非让异常扩散。"""
        registry = ToolRegistry()
        registry.register(HelperCrashingTool)

        result = await registry.execute_tool("crash_tool", {}, tmp_path)
        assert result.success is False
        assert "crash!" in result.output

    @pytest.mark.asyncio
    async def test_execute_tool_error_returns_result(self, tmp_path: Path) -> None:
        """工具执行时抛出 ToolError（如路径越界），应返回 ToolResult 而非继续传播异常。"""
        registry = ToolRegistry()
        registry.register(HelperToolErrorTool)

        result = await registry.execute_tool(
            "tool_error_tool", {"message": "路径越界：访问被拒绝"}, tmp_path
        )
        assert result.success is False
        assert "路径越界" in result.output


class TestToolSchema:
    """工具 schema 输出测试。"""

    def test_get_tool_schema_format(self) -> None:
        tool = HelperTool()
        schema = tool.get_tool_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "test_tool"
        assert schema["function"]["description"] == "测试工具"
        assert schema["function"]["parameters"]["type"] == "object"
        assert "msg" in schema["function"]["parameters"]["properties"]
        assert schema["function"]["parameters"]["required"] == ["msg"]

    def test_multiple_tools_schema_unique(self) -> None:
        registry = ToolRegistry()
        registry.register(HelperTool)
        registry.register(HelperEchoTool)

        schema_list = registry.get_tools_schema()
        names = [s["function"]["name"] for s in schema_list]
        assert len(names) == len(set(names)), "工具名称必须唯一"


class TestWorkspaceRootProperty:
    """workspace_root 属性的 getter/setter 测试。"""

    def test_default_is_none(self) -> None:
        tool = HelperTool()
        assert tool.workspace_root is None

    def test_setter(self, tmp_path: Path) -> None:
        tool = HelperTool()
        tool.workspace_root = tmp_path
        assert tool.workspace_root == tmp_path

    def test_init_with_root(self, tmp_path: Path) -> None:
        tool = HelperTool(workspace_root=tmp_path)
        assert tool.workspace_root == tmp_path
