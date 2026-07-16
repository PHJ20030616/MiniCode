"""forget 工具测试。"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from minicode.memory.manager import MemoryManager
from minicode.memory.models import MemoryMetadata, MemoryScope, MemorySource, MemoryType
from minicode.tools.forget import Forget


@pytest.fixture
def memory_workspace(tmp_path: Path) -> Path:
    """创建临时记忆工作区。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def sample_memory_metadata() -> MemoryMetadata:
    """创建示例记忆元数据。"""
    now = datetime.now(UTC)
    return MemoryMetadata(
        name="test-memory",
        description="测试记忆",
        created_at=now,
        updated_at=now,
        source=MemorySource.USER,
        scope=MemoryScope.WORKSPACE,
        confidence=0.9,
        type=MemoryType.PROJECT,
    )


async def test_forget_existing_memory(
    memory_workspace: Path,
    sample_memory_metadata: MemoryMetadata,
) -> None:
    """测试删除存在的记忆。"""
    # 创建记忆
    manager = MemoryManager(memory_workspace)
    manager.add(sample_memory_metadata, "这是测试内容")

    # 验证记忆存在
    memory_path = memory_workspace / ".minicode" / "memory" / "test-memory.md"
    assert memory_path.exists()

    # 删除记忆
    tool = Forget(workspace_root=memory_workspace)
    result = await tool.execute(name="test-memory")

    # 验证结果
    assert result.success is True
    assert "已删除" in result.output or "已忘记" in result.output

    # 验证文件已删除
    assert not memory_path.exists()

    # 验证索引已更新
    assert manager.get("test-memory") is None


async def test_forget_nonexistent_memory(memory_workspace: Path) -> None:
    """测试删除不存在的记忆（应返回友好提示）。"""
    tool = Forget(workspace_root=memory_workspace)
    result = await tool.execute(name="nonexistent")

    # 目标已达成，返回 success
    assert result.success is True
    assert "不存在" in result.output or "未找到" in result.output or "已被删除" in result.output


async def test_forget_invalid_name(memory_workspace: Path) -> None:
    """测试非法记忆名称。"""
    tool = Forget(workspace_root=memory_workspace)

    invalid_names = ["../etc/passwd", "test/name", "name.."]
    for invalid_name in invalid_names:
        result = await tool.execute(name=invalid_name)
        assert result.success is False
        assert "非法字符" in result.output or "参数" in result.output


async def test_forget_empty_name(memory_workspace: Path) -> None:
    """测试空名称。"""
    tool = Forget(workspace_root=memory_workspace)

    # 空字符串
    result = await tool.execute(name="")
    assert result.success is False
    assert "参数" in result.output

    # 纯空格
    result = await tool.execute(name="   ")
    assert result.success is False


async def test_forget_no_workspace(sample_memory_metadata: MemoryMetadata) -> None:
    """测试工作区路径未设置。"""
    tool = Forget(workspace_root=None)
    result = await tool.execute(name="test")

    assert result.success is False
    assert "工作区" in result.output


async def test_forget_updates_index(
    memory_workspace: Path,
    sample_memory_metadata: MemoryMetadata,
) -> None:
    """测试删除后索引正确更新。"""
    manager = MemoryManager(memory_workspace)

    # 创建三个记忆
    for i in range(3):
        meta = MemoryMetadata(
            name=f"memory-{i}",
            description=f"记忆 {i}",
            created_at=sample_memory_metadata.created_at,
            updated_at=sample_memory_metadata.updated_at,
            source=MemorySource.USER,
            scope=MemoryScope.WORKSPACE,
            confidence=0.9,
            type=MemoryType.PROJECT,
        )
        manager.add(meta, f"内容 {i}")

    # 验证三个记忆都在索引中
    entries = manager.list_memories()
    assert len(entries) == 3

    # 删除中间的记忆
    tool = Forget(workspace_root=memory_workspace)
    result = await tool.execute(name="memory-1")
    assert result.success is True

    # 验证索引只剩两个
    entries = manager.list_memories()
    assert len(entries) == 2
    assert "memory-1" not in [e["name"] for e in entries]
    assert "memory-0" in [e["name"] for e in entries]
    assert "memory-2" in [e["name"] for e in entries]


async def test_forget_invalid_parameter_types(memory_workspace: Path) -> None:
    """测试无效参数类型。"""
    tool = Forget(workspace_root=memory_workspace)

    # None
    result = await tool.execute(name=None)
    assert result.success is False
    assert "参数" in result.output
