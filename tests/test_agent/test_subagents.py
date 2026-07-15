"""Subagent MVP contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.agent.planning_models import PlanningConfig
from minicode.agent.subagents.manager import SubagentManager
from minicode.agent.subagents.models import (
    SubagentConfig,
    SubagentResult,
    SubagentRole,
    SubagentTask,
)
from minicode.agent.subagents.tool_filter import resolve_allowed_tools
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig
from minicode.tools.base import BaseTool, ToolResult
from minicode.tools.registry import ToolRegistry
from minicode.tools.subagent import RunSubagentTool
from minicode.utils.exceptions import ToolError


class NoopTool(BaseTool):
    """测试用工具，通过不同 name 注册多个工具。"""

    name: str = "noop"
    description: str = "测试工具"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:
        return ToolResult(success=True, output="ok")


def _app_config(subagents: SubagentConfig | None = None) -> AppConfig:
    """创建关闭规划的轻量配置，避免测试触发额外 provider 轮次。"""
    return AppConfig(
        default_provider="mock",
        default_model="mock-model",
        max_tokens=4096,
        agent=AgentConfig(
            max_rounds=8,
            stream=True,
            planning=PlanningConfig(enabled=False),
            subagents=subagents or SubagentConfig(),
        ),
        permissions=PermissionsConfig(trust_mode=False),
        providers={
            "mock": ProviderConfig(
                api_key="sk-test",
                base_url="https://api.mock.com/v1",
                models=["mock-model"],
            )
        },
    )


def _registry_with(names: list[str]) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        tool = NoopTool()
        tool.name = name
        registry.register_tool(tool)
    return registry


class TestResolveAllowedTools:
    """Subagent 工具白名单策略测试。"""

    def test_role_defaults_are_read_only(self) -> None:
        allowed = resolve_allowed_tools(
            requested_tools=None,
            role=SubagentRole.RESEARCHER,
            config=SubagentConfig(),
            available_tools=["read_file", "grep", "glob", "shell"],
        )

        assert allowed == ["read_file", "grep", "glob"]

    def test_tester_role_can_default_to_shell(self) -> None:
        allowed = resolve_allowed_tools(
            requested_tools=None,
            role=SubagentRole.TESTER,
            config=SubagentConfig(),
            available_tools=["read_file", "grep", "glob", "shell"],
        )

        assert allowed == ["read_file", "grep", "glob", "shell"]

    def test_explicit_tools_dedupe_and_preserve_order(self) -> None:
        allowed = resolve_allowed_tools(
            requested_tools=["grep", "read_file", "grep"],
            role=SubagentRole.GENERAL,
            config=SubagentConfig(),
            available_tools=["read_file", "grep", "glob"],
        )

        assert allowed == ["grep", "read_file"]

    def test_unknown_tool_is_rejected(self) -> None:
        with pytest.raises(ToolError, match="不存在"):
            resolve_allowed_tools(
                requested_tools=["missing"],
                role=SubagentRole.GENERAL,
                config=SubagentConfig(),
                available_tools=["read_file"],
            )

    def test_recursive_delegation_is_rejected(self) -> None:
        with pytest.raises(ToolError, match="不能继续调用 run_subagent"):
            resolve_allowed_tools(
                requested_tools=["run_subagent"],
                role=SubagentRole.GENERAL,
                config=SubagentConfig(),
                available_tools=["run_subagent"],
            )

    def test_write_tool_requires_config_flag(self) -> None:
        with pytest.raises(ToolError, match="默认禁止写入工具"):
            resolve_allowed_tools(
                requested_tools=["write_file"],
                role=SubagentRole.IMPLEMENTER,
                config=SubagentConfig(allow_write_tools=False),
                available_tools=["write_file"],
            )

    def test_write_tool_requires_explicit_allowed_tools(self) -> None:
        with pytest.raises(ToolError, match="必须在 allowed_tools 中显式声明"):
            resolve_allowed_tools(
                requested_tools=None,
                role=SubagentRole.GENERAL,
                config=SubagentConfig(
                    default_allowed_tools=["read_file", "write_file"],
                    allow_write_tools=True,
                ),
                available_tools=["read_file", "write_file"],
            )

    def test_explicit_write_tool_allowed_when_configured(self) -> None:
        allowed = resolve_allowed_tools(
            requested_tools=["write_file"],
            role=SubagentRole.IMPLEMENTER,
            config=SubagentConfig(allow_write_tools=True),
            available_tools=["write_file"],
        )

        assert allowed == ["write_file"]


class FakeSubagentManager:
    """RunSubagentTool 测试用 manager。"""

    def __init__(self) -> None:
        self.config = _app_config(SubagentConfig(max_result_chars=4000))
        self.tasks: list[SubagentTask] = []

    async def run_many(self, tasks: list[SubagentTask]) -> list[SubagentResult]:
        self.tasks.extend(tasks)
        return [
            SubagentResult(
                run_id="sub_test",
                name=tasks[0].name,
                role=tasks[0].role,
                status="completed",
                summary="已完成测试任务。",
                findings=["发现 A"],
                tool_call_count=1,
                elapsed_ms=12,
            )
        ]


class TestRunSubagentTool:
    """run_subagent 工具包装层测试。"""

    @pytest.mark.asyncio
    async def test_execute_validates_and_formats_result(self) -> None:
        manager = FakeSubagentManager()
        tool = RunSubagentTool(manager=manager)  # type: ignore[arg-type]

        result = await tool.execute(
            name="代码检索",
            task="查找关键入口。",
            role="researcher",
            allowed_tools=["read_file"],
        )

        assert result.success is True
        assert "子代理「代码检索」状态：completed" in result.output
        assert "已完成测试任务" in result.output
        assert manager.tasks[0].role == SubagentRole.RESEARCHER
        assert manager.tasks[0].allowed_tools == ["read_file"]

    @pytest.mark.asyncio
    async def test_invalid_arguments_return_chinese_error(self) -> None:
        tool = RunSubagentTool(manager=FakeSubagentManager())  # type: ignore[arg-type]

        result = await tool.execute(name="缺少任务")

        assert result.success is False
        assert "子代理参数无效" in result.output


class TestSubagentManager:
    """SubagentManager 调度边界测试。"""

    @pytest.mark.asyncio
    async def test_run_many_rejects_too_many_tasks(self, tmp_path: Path) -> None:
        manager = SubagentManager(
            provider=None,  # type: ignore[arg-type]
            parent_registry=_registry_with(["read_file"]),
            config=_app_config(SubagentConfig(max_agents=1)),
            workspace_root=tmp_path,
        )

        with pytest.raises(ToolError, match="最多允许启动 1 个子代理"):
            await manager.run_many([
                SubagentTask(name="A", task="任务 A"),
                SubagentTask(name="B", task="任务 B"),
            ])

    @pytest.mark.asyncio
    async def test_failed_task_returns_result_and_saves_record(self, tmp_path: Path) -> None:
        manager = SubagentManager(
            provider=None,  # type: ignore[arg-type]
            parent_registry=_registry_with(["read_file"]),
            config=_app_config(),
            workspace_root=tmp_path,
        )

        results = await manager.run_many([
            SubagentTask(name="坏任务", task="请求不存在工具", allowed_tools=["missing"])
        ])

        assert len(results) == 1
        assert results[0].status == "failed"
        assert "不存在" in results[0].errors[0]
        assert list((tmp_path / ".minicode" / "subagents" / "runs").glob("*/*.json"))
