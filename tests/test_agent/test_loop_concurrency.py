"""测试 AgentLoop 工具并发执行优化。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minicode.agent.loop import AgentLoop, ToolBlock, ToolCategory
from minicode.config.models import AgentConfig, AppConfig, ContextConfig, PermissionsConfig
from minicode.permissions.models import PermissionDecision, PermissionLevel
from minicode.permissions.store import PermissionStore
from minicode.providers.base import FunctionCall, Message, ToolCall
from minicode.tools.registry import ToolRegistry


@pytest.fixture
def mock_provider():
    """模拟 Provider。"""
    provider = MagicMock()
    provider.chat = AsyncMock()
    return provider


@pytest.fixture
def mock_tool_registry():
    """模拟 ToolRegistry。"""
    registry = MagicMock(spec=ToolRegistry)
    registry.get_tools_schema = MagicMock(return_value=[])
    registry.execute_tool = AsyncMock()
    return registry


@pytest.fixture
def mock_renderer():
    """模拟 StreamingRenderer。"""
    renderer = MagicMock()
    renderer.console = MagicMock()
    renderer.console.print = MagicMock()
    renderer.console.status = MagicMock()
    renderer.show_info = MagicMock()
    renderer.show_error = MagicMock()
    return renderer


@pytest.fixture
def mock_config():
    """模拟 AppConfig。"""
    return AppConfig(
        model="test-model",
        provider="test",
        max_tokens=1000,
        agent=AgentConfig(
            max_rounds=10,
            stream=True,
            context=ContextConfig(max_tokens=4000),
        ),
        permissions=PermissionsConfig(trust_mode=True),
    )


@pytest.fixture
def agent_loop(mock_provider, mock_tool_registry, mock_renderer, mock_config, tmp_path):
    """创建 AgentLoop 实例。"""
    return AgentLoop(
        provider=mock_provider,
        tool_registry=mock_tool_registry,
        renderer=mock_renderer,
        config=mock_config,
        workspace_root=tmp_path,
        permission_store=None,
        permission_confirmer=None,
    )


class TestPartitionToolCalls:
    """测试工具调用分块逻辑。"""

    def test_empty_list(self, agent_loop):
        """空列表应返回空块列表。"""
        blocks = agent_loop._partition_tool_calls([])
        assert blocks == []

    def test_all_read_tools(self, agent_loop):
        """全是读工具应该合并为一个块。"""
        tool_calls = [
            ToolCall(id="1", function=FunctionCall(name="read_file", arguments="{}")),
            ToolCall(id="2", function=FunctionCall(name="grep", arguments="{}")),
            ToolCall(id="3", function=FunctionCall(name="glob", arguments="{}")),
            ToolCall(id="4", function=FunctionCall(name="list_directory", arguments="{}")),
        ]
        blocks = agent_loop._partition_tool_calls(tool_calls)

        assert len(blocks) == 1
        assert blocks[0].category == ToolCategory.READ
        assert len(blocks[0].tool_calls) == 4
        assert [tc.id for tc in blocks[0].tool_calls] == ["1", "2", "3", "4"]

    def test_all_write_tools(self, agent_loop):
        """全是写工具应该每个独占一块。"""
        tool_calls = [
            ToolCall(id="1", function=FunctionCall(name="write_file", arguments="{}")),
            ToolCall(id="2", function=FunctionCall(name="bash", arguments="{}")),
            ToolCall(id="3", function=FunctionCall(name="edit_file", arguments="{}")),
        ]
        blocks = agent_loop._partition_tool_calls(tool_calls)

        assert len(blocks) == 3
        for i, block in enumerate(blocks):
            assert block.category == ToolCategory.WRITE
            assert len(block.tool_calls) == 1
            assert block.tool_calls[0].id == str(i + 1)

    def test_mixed_read_write(self, agent_loop):
        """读写混合应正确分块。"""
        tool_calls = [
            ToolCall(id="1", function=FunctionCall(name="read_file", arguments="{}")),
            ToolCall(id="2", function=FunctionCall(name="grep", arguments="{}")),
            ToolCall(id="3", function=FunctionCall(name="write_file", arguments="{}")),
            ToolCall(id="4", function=FunctionCall(name="glob", arguments="{}")),
            ToolCall(id="5", function=FunctionCall(name="bash", arguments="{}")),
            ToolCall(id="6", function=FunctionCall(name="read_file", arguments="{}")),
        ]
        blocks = agent_loop._partition_tool_calls(tool_calls)

        # 预期分块: [read+grep], [write], [glob], [bash], [read]
        assert len(blocks) == 5

        # Block 0: read_file + grep
        assert blocks[0].category == ToolCategory.READ
        assert len(blocks[0].tool_calls) == 2
        assert [tc.id for tc in blocks[0].tool_calls] == ["1", "2"]

        # Block 1: write_file
        assert blocks[1].category == ToolCategory.WRITE
        assert len(blocks[1].tool_calls) == 1
        assert blocks[1].tool_calls[0].id == "3"

        # Block 2: glob
        assert blocks[2].category == ToolCategory.READ
        assert len(blocks[2].tool_calls) == 1
        assert blocks[2].tool_calls[0].id == "4"

        # Block 3: bash
        assert blocks[3].category == ToolCategory.WRITE
        assert len(blocks[3].tool_calls) == 1
        assert blocks[3].tool_calls[0].id == "5"

        # Block 4: read_file
        assert blocks[4].category == ToolCategory.READ
        assert len(blocks[4].tool_calls) == 1
        assert blocks[4].tool_calls[0].id == "6"

    def test_single_read_tool(self, agent_loop):
        """单个读工具应该独占一个块。"""
        tool_calls = [
            ToolCall(id="1", function=FunctionCall(name="read_file", arguments="{}")),
        ]
        blocks = agent_loop._partition_tool_calls(tool_calls)

        assert len(blocks) == 1
        assert blocks[0].category == ToolCategory.READ
        assert len(blocks[0].tool_calls) == 1

    def test_single_write_tool(self, agent_loop):
        """单个写工具应该独占一个块。"""
        tool_calls = [
            ToolCall(id="1", function=FunctionCall(name="bash", arguments="{}")),
        ]
        blocks = agent_loop._partition_tool_calls(tool_calls)

        assert len(blocks) == 1
        assert blocks[0].category == ToolCategory.WRITE
        assert len(blocks[0].tool_calls) == 1

    def test_run_subagent_treated_as_write(self, agent_loop):
        """run_subagent 应该被视为写工具。"""
        tool_calls = [
            ToolCall(id="1", function=FunctionCall(name="read_file", arguments="{}")),
            ToolCall(id="2", function=FunctionCall(name="run_subagent", arguments="{}")),
            ToolCall(id="3", function=FunctionCall(name="grep", arguments="{}")),
        ]
        blocks = agent_loop._partition_tool_calls(tool_calls)

        # 预期分块: [read], [run_subagent], [grep]
        assert len(blocks) == 3
        assert blocks[0].category == ToolCategory.READ
        assert blocks[1].category == ToolCategory.WRITE
        assert blocks[1].tool_calls[0].function.name == "run_subagent"
        assert blocks[2].category == ToolCategory.READ

    def test_consecutive_run_subagent(self, agent_loop):
        """连续的 run_subagent 应该合并到同一块以支持批量并发。"""
        tool_calls = [
            ToolCall(id="1", function=FunctionCall(name="run_subagent", arguments="{}")),
            ToolCall(id="2", function=FunctionCall(name="run_subagent", arguments="{}")),
        ]
        blocks = agent_loop._partition_tool_calls(tool_calls)

        # 连续的 run_subagent 应该合并到一个块
        assert len(blocks) == 1
        assert blocks[0].category == ToolCategory.WRITE
        assert len(blocks[0].tool_calls) == 2
        assert all(tc.function.name == "run_subagent" for tc in blocks[0].tool_calls)


class TestExecuteReadBlock:
    """测试读工具块并发执行。"""

    @pytest.mark.asyncio
    async def test_execute_single_read_tool(
        self, agent_loop, mock_tool_registry, mock_renderer
    ):
        """单个读工具执行成功。"""
        from minicode.tools.base import ToolResult

        tool_calls = [
            ToolCall(
                id="call_1",
                function=FunctionCall(name="read_file", arguments='{"path": "test.txt"}'),
            ),
        ]

        mock_tool_registry.execute_tool.return_value = ToolResult(
            success=True, output="file content"
        )

        # Mock 权限检查，让所有工具通过
        with patch("minicode.agent.loop.check_permission") as mock_check:
            mock_check.return_value = PermissionDecision(
                level=PermissionLevel.SAFE,
                tool_name="read_file",
                operation="读取文件",
                summary="允许读取 test.txt",
            )

            await agent_loop._execute_read_block(tool_calls)

        # 验证工具被调用
        mock_tool_registry.execute_tool.assert_called_once()
        call_args = mock_tool_registry.execute_tool.call_args
        assert call_args[1]["name"] == "read_file"
        assert call_args[1]["args"] == {"path": "test.txt"}

        # 验证 ToolMessage 被追加
        assert len(agent_loop.messages) == 1
        assert agent_loop.messages[0].role == "tool"
        assert agent_loop.messages[0].content == "file content"
        assert agent_loop.messages[0].tool_call_id == "call_1"
        assert agent_loop.messages[0].name == "read_file"

    @pytest.mark.asyncio
    async def test_execute_multiple_read_tools(
        self, agent_loop, mock_tool_registry, mock_renderer
    ):
        """多个读工具并发执行，结果按原始顺序追加。"""
        from minicode.tools.base import ToolResult

        tool_calls = [
            ToolCall(
                id="call_1",
                function=FunctionCall(name="read_file", arguments='{"path": "a.txt"}'),
            ),
            ToolCall(
                id="call_2", function=FunctionCall(name="grep", arguments='{"pattern": "x"}')
            ),
            ToolCall(
                id="call_3", function=FunctionCall(name="glob", arguments='{"pattern": "*.py"}')
            ),
        ]

        # 模拟不同的执行时间和结果
        async def mock_execute(name, args, workspace_root):
            if name == "read_file":
                await asyncio.sleep(0.02)  # 稍慢
                return ToolResult(success=True, output="content of a.txt")
            elif name == "grep":
                await asyncio.sleep(0.01)  # 最快
                return ToolResult(success=True, output="grep result")
            else:  # glob
                await asyncio.sleep(0.015)  # 中等
                return ToolResult(success=True, output="file1.py\nfile2.py")

        mock_tool_registry.execute_tool.side_effect = mock_execute

        # Mock 权限检查
        with patch("minicode.agent.loop.check_permission") as mock_check:
            mock_check.return_value = PermissionDecision(
                level=PermissionLevel.SAFE,
                tool_name="test_tool",
                operation="测试操作",
                summary="允许执行",
            )

            await agent_loop._execute_read_block(tool_calls)

        # 验证所有工具都被调用
        assert mock_tool_registry.execute_tool.call_count == 3

        # 验证 ToolMessage 按原始顺序追加（即使完成时间不同）
        assert len(agent_loop.messages) == 3
        assert agent_loop.messages[0].tool_call_id == "call_1"
        assert agent_loop.messages[0].content == "content of a.txt"
        assert agent_loop.messages[1].tool_call_id == "call_2"
        assert agent_loop.messages[1].content == "grep result"
        assert agent_loop.messages[2].tool_call_id == "call_3"
        assert agent_loop.messages[2].content == "file1.py\nfile2.py"

    @pytest.mark.asyncio
    async def test_execute_read_tool_with_error(
        self, agent_loop, mock_tool_registry, mock_renderer
    ):
        """读工具执行失败时正确处理错误。"""
        from minicode.tools.base import ToolResult

        tool_calls = [
            ToolCall(
                id="call_1",
                function=FunctionCall(name="read_file", arguments='{"path": "missing.txt"}'),
            ),
        ]

        mock_tool_registry.execute_tool.return_value = ToolResult(
            success=False, output="文件不存在", error="FileNotFoundError"
        )

        # Mock 权限检查
        with patch("minicode.agent.loop.check_permission") as mock_check:
            mock_check.return_value = PermissionDecision(
                level=PermissionLevel.SAFE,
                tool_name="test_tool",
                operation="测试操作",
                summary="允许执行",
            )

            await agent_loop._execute_read_block(tool_calls)

        # 验证错误消息被追加
        assert len(agent_loop.messages) == 1
        assert agent_loop.messages[0].content == "文件不存在"

    @pytest.mark.asyncio
    async def test_execute_read_tool_with_invalid_json(
        self, agent_loop, mock_tool_registry, mock_renderer
    ):
        """参数解析失败时返回错误消息。"""
        tool_calls = [
            ToolCall(
                id="call_1",
                function=FunctionCall(name="read_file", arguments="invalid json"),
            ),
        ]

        await agent_loop._execute_read_block(tool_calls)

        # 验证参数解析错误被追加
        assert len(agent_loop.messages) == 1
        assert "参数解析错误" in agent_loop.messages[0].content


class TestExecuteToolsIntegration:
    """测试完整的 _execute_tools 方法。"""

    @pytest.mark.asyncio
    async def test_execute_tools_with_mixed_blocks(
        self, agent_loop, mock_tool_registry, mock_renderer
    ):
        """测试混合读写工具的完整执行流程。"""
        from minicode.tools.base import ToolResult

        tool_calls = [
            ToolCall(
                id="1",
                function=FunctionCall(name="read_file", arguments='{"path": "a.txt"}'),
            ),
            ToolCall(id="2", function=FunctionCall(name="grep", arguments='{"pattern": "x"}')),
            ToolCall(
                id="3",
                function=FunctionCall(name="write_file", arguments='{"path": "b.txt", "content": "test"}'),
            ),
            ToolCall(id="4", function=FunctionCall(name="glob", arguments='{"pattern": "*"}')),
        ]

        mock_tool_registry.execute_tool.return_value = ToolResult(
            success=True, output="success"
        )

        # Mock 权限检查
        with patch("minicode.agent.loop.check_permission") as mock_check:
            mock_check.return_value = PermissionDecision(
                level=PermissionLevel.SAFE,
                tool_name="test_tool",
                operation="测试操作",
                summary="允许执行",
            )

            await agent_loop._execute_tools(tool_calls)

        # 验证所有工具都被执行
        assert mock_tool_registry.execute_tool.call_count == 4

        # 验证消息按顺序追加
        assert len(agent_loop.messages) == 4
        assert all(msg.content == "success" for msg in agent_loop.messages)
