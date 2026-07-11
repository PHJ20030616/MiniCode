"""MemoryManager 核心 CRUD 测试。

覆盖以下场景：
- add 创建文件并更新索引
- get 正确解析记忆
- get 不存在返回 None
- delete 删除文件
- delete 不存在返回 False
- list 空/非空
- 特殊字符名称验证
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from minicode.memory.manager import MemoryManager
from minicode.memory.models import MemoryMetadata, MemoryScope


@pytest.fixture
def manager(tmp_path: Path) -> MemoryManager:
    """创建一个使用临时目录的 MemoryManager 实例。"""
    return MemoryManager(tmp_path)


class TestMemoryManagerAdd:
    """添加记忆测试。"""

    def test_add_creates_file(self, manager: MemoryManager) -> None:
        """验证 add 创建记忆文件。"""
        now = datetime.now()
        meta = MemoryMetadata(name="test-memory", created_at=now, updated_at=now)
        path = manager.add(meta, "测试正文")
        assert path.exists()
        assert path.name == "test-memory.md"

    def test_add_updates_index(self, manager: MemoryManager) -> None:
        """验证 add 后索引文件已创建。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="indexed-memory",
            description="索引测试",
            created_at=now,
            updated_at=now,
        )
        manager.add(meta, "正文")
        index_path = manager._memory_dir / "MEMORY.md"
        assert index_path.exists()
        content = index_path.read_text("utf-8")
        assert "indexed-memory" in content
        assert "索引测试" in content

    def test_add_invalid_name_raises(self, manager: MemoryManager) -> None:
        """验证非法名称抛出 ValueError。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="../escape-path",
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(ValueError, match="包含非法字符"):
            manager.add(meta, "正文")

    def test_add_name_with_spaces_raises(self, manager: MemoryManager) -> None:
        """验证带空格的名称抛出 ValueError。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="my memory",
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(ValueError, match="包含非法字符"):
            manager.add(meta, "正文")


class TestMemoryManagerGet:
    """获取记忆测试。"""

    def test_get_returns_memory(self, manager: MemoryManager) -> None:
        """验证 get 正确返回 Memory 实例。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="get-test",
            description="获取测试",
            created_at=now,
            updated_at=now,
            confidence=0.9,
        )
        manager.add(meta, "测试正文内容")
        memory = manager.get("get-test")
        assert memory is not None
        assert memory.metadata.name == "get-test"
        assert memory.metadata.description == "获取测试"
        assert memory.metadata.confidence == 0.9
        assert "测试正文内容" in memory.content

    def test_get_nonexistent_returns_none(self, manager: MemoryManager) -> None:
        """验证获取不存在的记忆返回 None。"""
        memory = manager.get("nonexistent")
        assert memory is None

    def test_get_after_add_creates_index(self, manager: MemoryManager) -> None:
        """验证 add 后 get 能正确读取。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="roundtrip-memory",
            created_at=now,
            updated_at=now,
            scope=MemoryScope.WORKSPACE,
        )
        manager.add(meta, "roundtrip 正文")
        memory = manager.get("roundtrip-memory")
        assert memory is not None
        assert memory.metadata.scope == MemoryScope.WORKSPACE
        assert memory.content == "roundtrip 正文"


class TestMemoryManagerDelete:
    """删除记忆测试。"""

    def test_delete_removes_file(self, manager: MemoryManager) -> None:
        """验证 delete 删除记忆文件。"""
        now = datetime.now()
        meta = MemoryMetadata(name="delete-test", created_at=now, updated_at=now)
        path = manager.add(meta, "正文")
        assert path.exists()
        result = manager.delete("delete-test")
        assert result is True
        assert not path.exists()

    def test_delete_removes_from_index(self, manager: MemoryManager) -> None:
        """验证 delete 从索引中移除条目。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="to-delete",
            description="将被删除",
            created_at=now,
            updated_at=now,
        )
        manager.add(meta, "正文")
        assert len(manager.list_memories()) == 1
        manager.delete("to-delete")
        assert len(manager.list_memories()) == 0

    def test_delete_nonexistent_returns_false(self, manager: MemoryManager) -> None:
        """验证删除不存在的记忆返回 False。"""
        result = manager.delete("not-exists")
        assert result is False


class TestMemoryManagerPathTraversal:
    """路径穿越防护测试。"""

    def test_get_path_traversal_raises(self, manager: MemoryManager) -> None:
        """验证 get 拒绝路径穿越名称。"""
        with pytest.raises(ValueError, match="包含非法字符"):
            manager.get("../etc/passwd")

    def test_delete_path_traversal_raises(self, manager: MemoryManager) -> None:
        """验证 delete 拒绝路径穿越名称。"""
        with pytest.raises(ValueError, match="包含非法字符"):
            manager.delete("../../outside")

    def test_get_with_slash_raises(self, manager: MemoryManager) -> None:
        """验证 get 拒绝包含斜杠的名称。"""
        with pytest.raises(ValueError, match="包含非法字符"):
            manager.get("a/b")

    def test_delete_empty_name_raises(self, manager: MemoryManager) -> None:
        """验证 delete 拒绝空名称。"""
        with pytest.raises(ValueError, match="包含非法字符"):
            manager.delete("")


class TestMemoryManagerList:
    """列表记忆测试。"""

    def test_list_empty(self, manager: MemoryManager) -> None:
        """验证空目录返回空列表。"""
        memories = manager.list_memories()
        assert memories == []

    def test_list_non_empty(self, manager: MemoryManager) -> None:
        """验证有记忆时返回非空列表。"""
        now = datetime.now()
        meta1 = MemoryMetadata(name="mem1", description="记忆一", created_at=now, updated_at=now)
        meta2 = MemoryMetadata(name="mem2", description="记忆二", created_at=now, updated_at=now)
        manager.add(meta1, "正文1")
        manager.add(meta2, "正文2")
        memories = manager.list_memories()
        assert len(memories) == 2
        names = [m["name"] for m in memories]
        assert "mem1" in names
        assert "mem2" in names

    def test_list_entry_structure(self, manager: MemoryManager) -> None:
        """验证列表条目包含必要的键。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="structured",
            description="结构化条目",
            created_at=now,
            updated_at=now,
        )
        manager.add(meta, "正文")
        entries = manager.list_memories()
        assert len(entries) == 1
        entry = entries[0]
        assert "name" in entry
        assert "filename" in entry
        assert "description" in entry
        assert entry["name"] == "structured"
        assert entry["filename"] == "structured.md"
        assert entry["description"] == "结构化条目"


class TestMemoryManagerValidation:
    """名称验证测试。"""

    @pytest.mark.parametrize("invalid_name", [
        "../traversal",
        "with space",
        "含有中文",
        "a/b",
        "a.b",
        "",
        "a\nb",
    ])
    def test_invalid_name_patterns(self, manager: MemoryManager, invalid_name: str) -> None:
        """验证各种非法名称模式。"""
        with pytest.raises(ValueError, match="包含非法字符"):
            manager._validate_memory_name(invalid_name)

    @pytest.mark.parametrize("valid_name", [
        "simple",
        "with-hyphen",
        "with_underscore",
        "name123",
        "UPPERCASE",
        "mixed-CASE_123",
    ])
    def test_valid_name_patterns(self, manager: MemoryManager, valid_name: str) -> None:
        """验证各种合法名称模式。"""
        # 不应抛出异常
        manager._validate_memory_name(valid_name)
