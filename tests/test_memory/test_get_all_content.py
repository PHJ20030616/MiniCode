"""get_all_content() 测试。

覆盖以下场景：
- 空目录返回空字符串
- 单条记忆格式化正确
- 多条记忆按优先级排序（scope > updated_at > confidence）
- max_chars 截断
- 损坏文件跳过不影响
- frontmatter 元数据损坏的文件跳过
- 合法的 unknown 名称记忆不被误跳过
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from minicode.memory.manager import MemoryManager
from minicode.memory.models import MemoryMetadata, MemoryScope


@pytest.fixture
def manager(tmp_path: Path) -> MemoryManager:
    """创建一个使用临时目录的 MemoryManager 实例。"""
    return MemoryManager(tmp_path)


def _add_memory(
    manager: MemoryManager,
    name: str,
    content: str,
    *,
    scope: MemoryScope = MemoryScope.GLOBAL,
    confidence: float = 0.5,
    days_ago: int = 0,
) -> None:
    """辅助函数：快速添加一条测试记忆。"""
    now = datetime.now() - timedelta(days=days_ago)
    meta = MemoryMetadata(
        name=name,
        description="",
        created_at=now,
        updated_at=now,
        scope=scope,
        confidence=confidence,
    )
    manager.add(meta, content)


class TestGetAllContent:
    """get_all_content 测试。"""

    def test_empty_directory(self, manager: MemoryManager) -> None:
        """验证空目录返回空字符串。"""
        result = manager.get_all_content()
        assert result == ""

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """验证不存在的记忆目录返回空字符串。"""
        # 使用不存在的子目录
        mgr = MemoryManager(tmp_path / "nonexistent")
        result = mgr.get_all_content()
        assert result == ""

    def test_single_memory(self, manager: MemoryManager) -> None:
        """验证单条记忆格式化正确。"""
        _add_memory(manager, "test-note", "这是测试记忆的内容")
        result = manager.get_all_content()
        assert "记忆：test-note" in result
        assert "global" in result
        assert "这是测试记忆的内容" in result

    def test_multiple_memories(self, manager: MemoryManager) -> None:
        """验证多条记忆全部包含在结果中。"""
        _add_memory(manager, "mem-a", "内容 A")
        _add_memory(manager, "mem-b", "内容 B")
        result = manager.get_all_content()
        assert "记忆：mem-a" in result
        assert "记忆：mem-b" in result
        assert "内容 A" in result
        assert "内容 B" in result

    def test_scope_sorting_workspace_first(self, manager: MemoryManager) -> None:
        """验证 workspace 作用域的记忆优先于 global。"""
        _add_memory(manager, "global-mem", "全局记忆", scope=MemoryScope.GLOBAL)
        _add_memory(manager, "ws-mem", "工作区记忆", scope=MemoryScope.WORKSPACE)
        result = manager.get_all_content(workspace="/some/workspace")
        # workspace 记忆应该出现在 global 之前
        ws_idx = result.index("工作区记忆")
        global_idx = result.index("全局记忆")
        assert ws_idx < global_idx

    def test_older_workspace_before_newer_global(self, manager: MemoryManager) -> None:
        """验证旧的 workspace 记忆仍排在新的 global 记忆前。"""
        _add_memory(manager, "old-ws", "旧的工作区记忆", scope=MemoryScope.WORKSPACE, days_ago=10)
        _add_memory(manager, "new-global", "新的全局记忆", scope=MemoryScope.GLOBAL, days_ago=0)
        result = manager.get_all_content(workspace="/some/workspace")
        ws_idx = result.index("旧的工作区记忆")
        global_idx = result.index("新的全局记忆")
        assert ws_idx < global_idx

    def test_max_chars_truncation(self, manager: MemoryManager) -> None:
        """验证 max_chars 截断生效。"""
        _add_memory(manager, "long-mem", "A" * 500)
        result = manager.get_all_content(max_chars=100)
        assert len(result) <= 100 + len("\n\n...（截断）")
        assert "...（截断）" in result

    def test_small_max_chars(self, manager: MemoryManager) -> None:
        """验证很小的 max_chars 仍能工作。"""
        _add_memory(manager, "tiny", "Hello World")
        # max_chars=5 应该截断大部分内容
        result = manager.get_all_content(max_chars=5)
        assert len(result) <= 5 + len("\n\n...（截断）")

    def test_conflict_detection_does_not_crash(self, manager: MemoryManager) -> None:
        """验证同名不同 scope 不崩溃，仅记录日志。"""
        same_name = "conflict-mem"
        _add_memory(manager, same_name, "全局版本", scope=MemoryScope.GLOBAL)
        # 同名的 workspace 记忆（手动写入文件绕过 add 的覆盖行为）
        now = datetime.now()
        meta = MemoryMetadata(
            name=same_name,
            created_at=now,
            updated_at=now,
            scope=MemoryScope.WORKSPACE,
        )
        from minicode.memory.models import Memory

        fpath = manager._memory_dir / f"{same_name}.md"
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(Memory.format_file(meta, "工作区版本"), encoding="utf-8")

        # 不应抛出异常
        result = manager.get_all_content()
        assert "conflict-mem" in result

    def test_truncation_at_newline(self, manager: MemoryManager) -> None:
        """验证截断在换行处进行。"""
        # 添加一条内容包含换行的记忆
        _add_memory(manager, "multi-line", "第一行\n第二行\n第三行\n第四行")
        # max_chars 刚好截断到第三行中间
        max_target = len("--- 记忆：multi-line（global，0.5）---\n第一行\n第二行\n第")
        result = manager.get_all_content(max_chars=max_target)
        # 应保留到上一个完整的换行
        assert "第一行" in result
        assert "第二行" in result
        assert "...（截断）" in result
        # "第三行" 不应该出现（被截掉了）
        assert "第三行" not in result

    def test_empty_content_memories(self, manager: MemoryManager) -> None:
        """验证内容为空的记忆也能正常处理。"""
        now = datetime.now()
        meta = MemoryMetadata(name="empty-mem", created_at=now, updated_at=now)
        manager.add(meta, "")
        result = manager.get_all_content()
        assert "记忆：empty-mem" in result

    def test_confidence_sorting(self, manager: MemoryManager) -> None:
        """验证置信度更高的记忆排前面（同等 scope 和 updated_at 时）。"""
        now = datetime.now()
        meta_low = MemoryMetadata(
            name="low-conf", created_at=now, updated_at=now, confidence=0.3
        )
        meta_high = MemoryMetadata(
            name="high-conf", created_at=now, updated_at=now, confidence=0.9
        )
        manager.add(meta_low, "低置信度")
        manager.add(meta_high, "高置信度")
        result = manager.get_all_content()
        high_idx = result.index("高置信度")
        low_idx = result.index("低置信度")
        assert high_idx < low_idx

    def test_update_time_sorting(self, manager: MemoryManager) -> None:
        """验证更新更近的记忆排前面（同等 scope 时）。"""
        _add_memory(manager, "old-mem", "旧的记忆", days_ago=10)
        _add_memory(manager, "new-mem", "新的记忆", days_ago=0)
        result = manager.get_all_content()
        new_idx = result.index("新的记忆")
        old_idx = result.index("旧的记忆")
        assert new_idx < old_idx

    def test_corrupted_file_skipped(self, manager: MemoryManager) -> None:
        """验证损坏文件被跳过不影响输出，且不包含 unknown 记忆。"""
        # 添加一条正常记忆
        _add_memory(manager, "good-mem", "正常内容")
        # 创建一个损坏的记忆文件（无 frontmatter）
        bad_file = manager._memory_dir / "corrupted.md"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("不是有效的记忆文件格式", encoding="utf-8")
        # 不应抛出异常，且应包含正常记忆
        result = manager.get_all_content()
        assert "正常内容" in result
        assert "unknown" not in result

    def test_corrupted_metadata_file_skipped(self, manager: MemoryManager) -> None:
        """验证 frontmatter 存在但元数据损坏（日期无效）的记忆跳过。"""
        _add_memory(manager, "good-mem", "正常内容")
        # 创建 frontmatter YAML 结构正确但元数据值无效的文件
        bad_file = manager._memory_dir / "bad-metadata.md"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text(
            "---\n"
            "name: poisoned\n"
            "created_at: not-a-date\n"
            "updated_at: also-bad\n"
            "---\n\n"
            "SHOULD_NOT_BE_INJECTED\n",
            encoding="utf-8",
        )
        result = manager.get_all_content()
        # 正常记忆仍在
        assert "正常内容" in result
        # 损坏记忆的内容和名称不应出现
        assert "SHOULD_NOT_BE_INJECTED" not in result
        assert "poisoned" not in result

    def test_valid_unknown_name_not_skipped(self, manager: MemoryManager) -> None:
        """验证合法的 name='unknown' 记忆不被误跳过。"""
        now = datetime.now()
        meta = MemoryMetadata(
            name="unknown",
            description="用户主动创建的 unknown 记忆",
            created_at=now,
            updated_at=now,
        )
        manager.add(meta, "合法 unknown 记忆")
        result = manager.get_all_content()
        assert "合法 unknown 记忆" in result
        assert "unknown" in result

    def test_max_chars_zero_disables_truncation(self, manager: MemoryManager) -> None:
        """验证 max_chars=0 时返回全部内容。"""
        _add_memory(manager, "full-mem", "B" * 500)
        result = manager.get_all_content(max_chars=0)
        assert len(result) > 500
        assert "full-mem" in result
