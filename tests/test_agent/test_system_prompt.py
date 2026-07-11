"""系统提示词注入测试。

覆盖以下场景：
- 无记忆时行为不变
- 空记忆内容不注入
- 非空记忆内容注入"用户记忆"和"可能不完整或过期"
"""

from __future__ import annotations

import pytest

from minicode.agent.system_prompt import build_system_prompt
from minicode.tools.base import BaseTool, ToolResult
from minicode.tools.registry import ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    """创建一个包含测试工具的空注册器。"""
    reg = ToolRegistry()
    return reg


@pytest.fixture
def registry_with_tools() -> ToolRegistry:
    """创建一个包含测试工具的注册器。"""

    class TestTool(BaseTool):
        name = "test_tool"
        description = "一个测试工具"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(success=True)

    reg = ToolRegistry()
    reg._tools["test_tool"] = TestTool()
    return reg


@pytest.fixture
def registry_with_both() -> ToolRegistry:
    """创建一个同时包含 test_tool 和 remember 的注册器。"""

    class TestTool(BaseTool):
        name = "test_tool"
        description = "一个测试工具"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(success=True)

    class MockRemember(BaseTool):
        name = "remember"
        description = "记忆保存工具"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(success=True)

    reg = ToolRegistry()
    reg._tools["test_tool"] = TestTool()
    reg._tools["remember"] = MockRemember()
    return reg


@pytest.fixture
def registry_with_remember() -> ToolRegistry:
    """创建一个包含 remember 工具的注册器。"""

    class MockRemember(BaseTool):
        name = "remember"
        description = "记忆保存工具"
        parameters = {"type": "object", "properties": {}}

        async def execute(self, **kwargs: object) -> ToolResult:
            return ToolResult(success=True)

    reg = ToolRegistry()
    reg._tools["remember"] = MockRemember()
    return reg


class TestBuildSystemPrompt:
    """build_system_prompt 测试。"""

    def test_no_memory_content(self, registry_with_tools: ToolRegistry) -> None:
        """验证无 memory_content 时不注入记忆部分。"""
        prompt = build_system_prompt(registry_with_tools)
        assert "用户记忆" not in prompt

    def test_empty_memory_content(self, registry_with_tools: ToolRegistry) -> None:
        """验证空的 memory_content 不注入。"""
        prompt = build_system_prompt(registry_with_tools, memory_content="")
        assert "用户记忆" not in prompt

    def test_with_memory_content(self, registry_with_tools: ToolRegistry) -> None:
        """验证非空 memory_content 注入"用户记忆"和免责声明。"""
        prompt = build_system_prompt(
            registry_with_tools,
            memory_content="--- 记忆：test ---\n用户喜欢 Python",
        )
        assert "用户记忆" in prompt
        assert "可能不完整或过期" in prompt
        assert "用户喜欢 Python" in prompt
        assert "---" in prompt

    def test_memory_content_at_end(self, registry_with_tools: ToolRegistry) -> None:
        """验证记忆内容追加在提示词末尾。"""
        prompt = build_system_prompt(
            registry_with_tools,
            memory_content="记忆内容",
        )
        # 记忆部分应在"可用工具"之后
        assert prompt.index("记忆内容") > prompt.index("可用工具")


class TestBuildSystemPromptWithRemember:
    """包含 remember 工具时的系统提示词测试。"""

    def test_remember_instructions_included(self, registry_with_remember: ToolRegistry) -> None:
        """验证 remember 工具使用说明被包含。"""
        prompt = build_system_prompt(registry_with_remember)
        assert "记忆工具使用说明" in prompt
        assert "  - remember:" in prompt
        assert "记住…" in prompt
        assert "不要保存敏感信息" in prompt
        assert "`[a-zA-Z0-9_-]+`" in prompt or "a-zA-Z0-9_-" in prompt
        assert "workspace" in prompt

    def test_remember_instructions_no_injection_without_tool(
        self, registry_with_tools: ToolRegistry
    ) -> None:
        """无 remember 工具时不应包含记忆工具说明。"""
        prompt = build_system_prompt(registry_with_tools)
        assert "记忆工具使用说明" not in prompt

    def test_remember_instructions_with_memory_content(
        self, registry_with_remember: ToolRegistry
    ) -> None:
        """验证同时包含记忆内容和 remember 说明时两者共存。"""
        prompt = build_system_prompt(
            registry_with_remember,
            memory_content="--- 记忆：test ---\n已有记忆内容",
        )
        assert "记忆工具使用说明" in prompt
        assert "已有记忆内容" in prompt
        assert "可能不完整或过期" in prompt

    def test_enabled_remember_in_tool_list(
        self, registry_with_both: ToolRegistry
    ) -> None:
        """memory_enabled=True 时 remember 工具描述仍在工具列表中。"""
        prompt = build_system_prompt(registry_with_both)
        assert "  - remember:" in prompt
        assert "  - test_tool:" in prompt
        assert "记忆工具使用说明" in prompt


class TestBuildSystemPromptMemoryDisabled:
    """memory_enabled=False 时的系统提示词测试。"""

    def test_disabled_no_remember_instructions(
        self, registry_with_remember: ToolRegistry
    ) -> None:
        """memory_enabled=False 时不应包含记忆工具说明。"""
        prompt = build_system_prompt(
            registry_with_remember,
            memory_enabled=False,
        )
        assert "记忆工具使用说明" not in prompt

    def test_disabled_no_memory_content(
        self, registry_with_remember: ToolRegistry
    ) -> None:
        """memory_enabled=False 时不应注入记忆内容。"""
        prompt = build_system_prompt(
            registry_with_remember,
            memory_content="--- 记忆：test ---\n秘密内容",
            memory_enabled=False,
        )
        assert "秘密内容" not in prompt
        assert "用户记忆" not in prompt

    def test_disabled_filters_only_remember_tool(
        self, registry_with_remember: ToolRegistry
    ) -> None:
        """memory_enabled=False 且仅有 remember 工具时，退回基础提示词（无工具列表）。"""
        prompt = build_system_prompt(
            registry_with_remember,
            memory_enabled=False,
        )
        assert "可用工具" not in prompt
        assert "remember" not in prompt
        assert "记忆工具使用说明" not in prompt

    def test_disabled_other_tools_still_listed(
        self, registry_with_both: ToolRegistry
    ) -> None:
        """memory_enabled=False 时过滤掉 remember，但其他工具仍在列表中。"""
        prompt = build_system_prompt(
            registry_with_both,
            memory_enabled=False,
        )
        assert "可用工具" in prompt
        assert "记忆工具使用说明" not in prompt
        assert "  - test_tool:" in prompt
        assert "  - remember:" not in prompt
