"""记忆系统集成测试。

覆盖端到端场景：
- 完整链路：添加记忆 → 获取内容 → 注入系统提示词 → 验证
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from minicode.agent.system_prompt import build_system_prompt
from minicode.memory.manager import MemoryManager
from minicode.memory.models import MemoryMetadata, MemoryScope, MemoryType
from minicode.tools.base import BaseTool, ToolResult
from minicode.tools.registry import ToolRegistry


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


class TestMemoryIntegration:
    """记忆系统集成测试。"""

    def test_full_pipeline(self, tmp_path: Path, registry_with_tools: ToolRegistry) -> None:
        """完整链路测试：add → get_all_content → build_system_prompt。"""
        # 1. 添加记忆
        manager = MemoryManager(tmp_path)
        now = datetime.now()
        meta = MemoryMetadata(
            name="user-preference",
            description="用户偏好",
            created_at=now,
            updated_at=now,
            scope=MemoryScope.GLOBAL,
            confidence=0.9,
            type=MemoryType.USER,
        )
        manager.add(meta, "用户喜欢使用 Python 和 TypeScript")

        # 2. 获取所有记忆内容
        content = manager.get_all_content()
        assert "user-preference" in content
        assert "Python" in content
        assert "TypeScript" in content

        # 3. 注入到系统提示词
        prompt = build_system_prompt(
            registry_with_tools,
            memory_content=content,
        )
        assert "用户记忆" in prompt
        assert "可能不完整或过期" in prompt
        assert "Python" in prompt

    def test_multiple_memories_pipeline(
        self,
        tmp_path: Path,
        registry_with_tools: ToolRegistry,
    ) -> None:
        """多条记忆的完整链路测试。"""
        manager = MemoryManager(tmp_path)
        now = datetime.now()

        meta1 = MemoryMetadata(
            name="pref-1",
            created_at=now,
            updated_at=now,
            scope=MemoryScope.GLOBAL,
            confidence=0.8,
        )
        manager.add(meta1, "记忆内容1")

        meta2 = MemoryMetadata(
            name="pref-2",
            created_at=now,
            updated_at=now,
            scope=MemoryScope.WORKSPACE,
            confidence=0.9,
        )
        manager.add(meta2, "记忆内容2")

        content = manager.get_all_content(workspace=str(tmp_path))
        assert "记忆内容1" in content
        assert "记忆内容2" in content

        prompt = build_system_prompt(
            registry_with_tools,
            memory_content=content,
        )
        assert "记忆内容1" in prompt
        assert "记忆内容2" in prompt

    def test_disabled_memory_no_injection(
        self,
        tmp_path: Path,
        registry_with_tools: ToolRegistry,
    ) -> None:
        """验证记忆禁用时不会注入。"""
        # 添加记忆
        manager = MemoryManager(tmp_path)
        now = datetime.now()
        meta = MemoryMetadata(name="secret", created_at=now, updated_at=now)
        manager.add(meta, "敏感信息")

        # 禁用记忆后不应注入
        prompt = build_system_prompt(registry_with_tools, memory_content=None)
        assert "敏感信息" not in prompt
        assert "用户记忆" not in prompt
