"""SessionManager CRUD 与边界处理单元测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from minicode.providers.base import FunctionCall, Message, ToolCall, ToolMessage
from minicode.session.manager import SessionManager

# 一个格式合法的、不存在的 session_id（32 位小写十六进制）
_VALID_NONEXISTENT_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class TestCreate:
    """创建会话测试。"""

    def test_create_session(self, tmp_path: Path) -> None:
        """创建会话，验证各字段。"""
        manager = SessionManager(tmp_path)
        session = manager.create(
            model="test-model", provider="test-provider", workspace_root=str(tmp_path)
        )

        assert session.id
        assert len(session.id) == 32
        assert session.model == "test-model"
        assert session.provider == "test-provider"
        assert session.workspace_root == str(tmp_path)
        assert session.messages == []

    def test_create_session_generates_name(self, tmp_path: Path) -> None:
        """自动生成的 name 非空。"""
        manager = SessionManager(tmp_path)
        session = manager.create()

        assert session.name
        assert len(session.name) > 0


class TestSave:
    """保存会话测试。"""

    def test_save_creates_file(self, tmp_path: Path) -> None:
        """save 后应在磁盘上生成 JSON 文件。"""
        manager = SessionManager(tmp_path)
        session = manager.create()

        manager.save(session)

        session_file = tmp_path / ".minicode" / "sessions" / f"{session.id}.json"
        assert session_file.exists()

    def test_save_creates_directory_if_missing(self, tmp_path: Path) -> None:
        """应自动创建 .minicode/sessions/ 目录。"""
        manager = SessionManager(tmp_path)
        session = manager.create()

        manager.save(session)

        assert (tmp_path / ".minicode" / "sessions").is_dir()

    def test_save_updates_index(self, tmp_path: Path) -> None:
        """save 后 index.json 应包含该会话摘要。"""
        manager = SessionManager(tmp_path)
        session = manager.create(model="m1", provider="p1")

        manager.save(session)

        index = manager._load_index()
        assert len(index) == 1
        assert index[0]["id"] == session.id
        assert index[0]["model"] == "m1"
        assert index[0]["provider"] == "p1"

    def test_save_with_messages(self, tmp_path: Path) -> None:
        """保存含 tool_calls 和 tool_results 的完整 messages。"""
        manager = SessionManager(tmp_path)
        session = manager.create()
        session.messages = [
            Message(role="user", content="Hi"),
            Message(
                role="assistant",
                content="Let me check...",
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        function=FunctionCall(name="read_file", arguments='{"path": "x.txt"}'),
                    ),
                ],
            ),
            ToolMessage(content="file content", tool_call_id="call_1", name="read_file"),
        ]

        manager.save(session)

        # 读取文件验证
        session_file = tmp_path / ".minicode" / "sessions" / f"{session.id}.json"
        raw = json.loads(session_file.read_text(encoding="utf-8"))
        assert raw["model"] == session.model
        assert len(raw["messages"]) == 3
        assert raw["messages"][1]["tool_calls"][0]["function"]["name"] == "read_file"
        assert raw["messages"][2]["role"] == "tool"


class TestLoad:
    """加载会话测试。"""

    def test_load_returns_correct_data(self, tmp_path: Path) -> None:
        """load 返回的 Session 应与保存前一致。"""
        manager = SessionManager(tmp_path)
        original = manager.create(model="m1", provider="p1")
        original.messages.append(Message(role="user", content="Hello"))
        manager.save(original)

        loaded = manager.load(original.id)
        assert loaded is not None
        assert loaded.id == original.id
        assert loaded.model == "m1"
        assert loaded.provider == "p1"
        assert loaded.message_count == 1
        assert loaded.messages[0].content == "Hello"

    def test_load_with_tool_messages(self, tmp_path: Path) -> None:
        """load 后 ToolMessage 子类应正确恢复。"""
        manager = SessionManager(tmp_path)
        original = manager.create()
        original.messages.append(ToolMessage(content="result", tool_call_id="c1"))
        manager.save(original)

        loaded = manager.load(original.id)
        assert loaded is not None
        assert isinstance(loaded.messages[0], ToolMessage)
        assert loaded.messages[0].tool_call_id == "c1"

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """加载不存在的会话应返回 None。"""
        manager = SessionManager(tmp_path)
        result = manager.load(_VALID_NONEXISTENT_ID)
        assert result is None

    def test_load_corrupted_file_returns_none(self, tmp_path: Path) -> None:
        """损坏的 JSON 文件应返回 None。"""
        manager = SessionManager(tmp_path)
        # 创建并保存一个会话，然后手动覆盖文件为损坏内容
        session = manager.create()
        manager.save(session)

        session_file = tmp_path / ".minicode" / "sessions" / f"{session.id}.json"
        session_file.write_text("{invalid json", encoding="utf-8")

        loaded = manager.load(session.id)
        assert loaded is None


class TestList:
    """列出会话测试。"""

    def test_empty(self, tmp_path: Path) -> None:
        """无会话时返回空列表。"""
        manager = SessionManager(tmp_path)
        assert manager.list_sessions() == []

    def test_after_multiple_saves(self, tmp_path: Path) -> None:
        """多次保存后列表应包含全部会话摘要。"""
        manager = SessionManager(tmp_path)
        s1 = manager.create(model="m1", provider="p1")
        s2 = manager.create(model="m2", provider="p2")
        manager.save(s1)
        manager.save(s2)

        sessions = manager.list_sessions()
        assert len(sessions) == 2
        ids = {s["id"] for s in sessions}
        assert ids == {s1.id, s2.id}

    def test_sorted_by_updated_at(self, tmp_path: Path) -> None:
        """列表应按 updated_at 降序排列。"""
        manager = SessionManager(tmp_path)
        s1 = manager.create()
        s2 = manager.create()
        s3 = manager.create()

        # 旧的 updated_at
        s1.updated_at = datetime(2024, 1, 1, tzinfo=UTC)
        s2.updated_at = datetime(2024, 6, 1, tzinfo=UTC)
        s3.updated_at = datetime(2024, 3, 1, tzinfo=UTC)

        manager.save(s1)
        manager.save(s2)
        manager.save(s3)

        sessions = manager.list_sessions()
        assert len(sessions) == 3
        # 第一个更新的 updated_at >= 第二个的
        for i in range(len(sessions) - 1):
            assert sessions[i]["updated_at"] >= sessions[i + 1]["updated_at"]


class TestDelete:
    """删除会话测试。"""

    def test_delete_removes_file(self, tmp_path: Path) -> None:
        """delete 后会话文件应被删除。"""
        manager = SessionManager(tmp_path)
        session = manager.create()
        manager.save(session)

        session_file = tmp_path / ".minicode" / "sessions" / f"{session.id}.json"
        assert session_file.exists()

        result = manager.delete(session.id)
        assert result is True
        assert not session_file.exists()

    def test_delete_removes_from_index(self, tmp_path: Path) -> None:
        """delete 后索引中不应包含该会话。"""
        manager = SessionManager(tmp_path)
        session = manager.create()
        manager.save(session)
        assert len(manager.list_sessions()) == 1

        manager.delete(session.id)
        assert manager.list_sessions() == []

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        """删除不存在的会话应返回 False。"""
        manager = SessionManager(tmp_path)
        result = manager.delete(_VALID_NONEXISTENT_ID)
        # 文件不存在且索引中也没有 → 返回 False
        assert result is False


class TestEdgeCases:
    """边界情况测试。"""

    def test_save_handles_permission_error(self, tmp_path: Path, monkeypatch) -> None:
        """目录创建失败时不应崩溃。"""

        def failing_mkdir(self, *args, **kwargs):
            raise PermissionError("模拟权限不足")

        monkeypatch.setattr(Path, "mkdir", failing_mkdir)

        manager = SessionManager(tmp_path)
        session = manager.create()
        # 不应抛出异常
        manager.save(session)

    def test_index_corrupted_recovery(self, tmp_path: Path) -> None:
        """index.json 损坏时 list 应返回空列表，save 应覆盖。"""
        manager = SessionManager(tmp_path)

        # 写入损坏的索引
        index_path = tmp_path / ".minicode" / "sessions" / "index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("not valid json", encoding="utf-8")

        # list 应返回空列表
        assert manager.list_sessions() == []

        # save 应覆盖损坏的索引
        session = manager.create()
        manager.save(session)
        assert len(manager.list_sessions()) == 1

    def test_session_path_format(self, tmp_path: Path) -> None:
        """会话文件路径格式应为 {id}.json。"""
        manager = SessionManager(tmp_path)
        path = manager._session_path("abcdefabcdefabcdefabcdefabcdefab")
        assert path.name == "abcdefabcdefabcdefabcdefabcdefab.json"
        assert path.suffix == ".json"

    def test_save_write_text_failure(self, tmp_path: Path, monkeypatch) -> None:
        """会话文件写入失败时不应崩溃。"""
        manager = SessionManager(tmp_path)
        session = manager.create()

        def failing_write(*args, **kwargs):
            raise OSError("模拟磁盘错误")

        monkeypatch.setattr(Path, "write_text", failing_write)
        # 不应抛出异常
        manager.save(session)

    def test_load_deserialization_failure(self, tmp_path: Path) -> None:
        """有效的 JSON 但无效的 Session 数据应返回 None。"""
        manager = SessionManager(tmp_path)
        # 创建会话文件，但写入无法通过 Session 验证的数据
        session = manager.create()
        manager.save(session)

        session_file = tmp_path / ".minicode" / "sessions" / f"{session.id}.json"
        # 写入有效的 JSON 但缺少必填字段的类型不匹配
        session_file.write_text(
            json.dumps({"id": 12345, "messages": "invalid"}, ensure_ascii=False),
            encoding="utf-8",
        )

        loaded = manager.load(session.id)
        assert loaded is None

    def test_delete_unlink_failure(self, tmp_path: Path, monkeypatch) -> None:
        """删除文件时发生 OSError 应返回 False。"""
        manager = SessionManager(tmp_path)
        session = manager.create()
        manager.save(session)

        def failing_unlink(*args, **kwargs):
            raise OSError("模拟删除失败")

        monkeypatch.setattr(Path, "unlink", failing_unlink)

        result = manager.delete(session.id)
        assert result is False

    def test_index_not_a_list_recovery(self, tmp_path: Path) -> None:
        """索引文件是有效 JSON 但不是数组时应恢复。"""
        manager = SessionManager(tmp_path)

        index_path = tmp_path / ".minicode" / "sessions" / "index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text('{"not": "a list"}', encoding="utf-8")

        assert manager.list_sessions() == []

    def test_save_same_session_twice_updates_index(self, tmp_path: Path) -> None:
        """两次保存同一会话应更新索引而非添加重复条目。"""
        manager = SessionManager(tmp_path)
        session = manager.create(model="m1", provider="p1")
        session.messages.append(Message(role="user", content="Hello"))

        # 第一次保存
        manager.save(session)
        assert len(manager.list_sessions()) == 1

        # 第二次保存（更新消息）
        session.messages.append(Message(role="assistant", content="Hi"))
        session.updated_at = datetime.now(UTC)
        manager.save(session)

        sessions = manager.list_sessions()
        assert len(sessions) == 1  # 不应有重复条目
        assert sessions[0]["message_count"] == 2

    def test_delete_orphan_index_returns_true(self, tmp_path: Path) -> None:
        """文件不存在但索引中有孤儿条目时 delete 应返回 True。"""
        manager = SessionManager(tmp_path)
        session = manager.create()
        manager.save(session)

        # 先删文件（模拟文件被外部删除），再删除会话
        session_file = manager._session_path(session.id)
        session_file.unlink()
        assert not session_file.exists()

        # 索引中仍有该条目
        assert len(manager.list_sessions()) == 1

        # delete 应清理索引并返回 True
        result = manager.delete(session.id)
        assert result is True
        assert manager.list_sessions() == []


class TestSessionIdValidation:
    """Session ID 安全校验测试。"""

    def test_invalid_length_raises(self, tmp_path: Path) -> None:
        """非 32 位长的 session_id 应拒绝。"""
        manager = SessionManager(tmp_path)
        with pytest.raises(ValueError, match="session_id"):
            manager.load("short")

    def test_non_hex_chars_raises(self, tmp_path: Path) -> None:
        """含非十六进制字符的 session_id 应拒绝。"""
        manager = SessionManager(tmp_path)
        with pytest.raises(ValueError, match="session_id"):
            manager.load("zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")

    def test_empty_string_raises(self, tmp_path: Path) -> None:
        """空字符串应拒绝。"""
        manager = SessionManager(tmp_path)
        with pytest.raises(ValueError, match="session_id"):
            manager.load("")

    def test_path_separator_forward_slash_raises(self, tmp_path: Path) -> None:
        """session_id 含 / 应拒绝。"""
        manager = SessionManager(tmp_path)
        with pytest.raises(ValueError, match="session_id"):
            manager.load("../evil/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    def test_path_separator_backslash_raises(self, tmp_path: Path) -> None:
        """session_id 含反斜杠应拒绝。"""
        manager = SessionManager(tmp_path)
        with pytest.raises(ValueError, match="session_id"):
            manager.load("..\\evil\\aaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    def test_double_dot_raises(self, tmp_path: Path) -> None:
        """session_id 含 .. 应拒绝。"""
        manager = SessionManager(tmp_path)
        with pytest.raises(ValueError, match="session_id"):
            manager.load("....aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    def test_delete_invalid_id_raises(self, tmp_path: Path) -> None:
        """delete 传入非法 id 应抛出 ValueError。"""
        manager = SessionManager(tmp_path)
        with pytest.raises(ValueError, match="session_id"):
            manager.delete("../etc/passwd")

    def test_save_with_valid_session_passes(self, tmp_path: Path) -> None:
        """正常 Session.id 应通过校验（session.id = uuid4().hex）。"""
        manager = SessionManager(tmp_path)
        session = manager.create()
        session_path = manager._session_path(session.id)
        assert session_path.name == f"{session.id}.json"
        assert session_path.suffix == ".json"

    def test_trailing_newline_raises(self, tmp_path: Path) -> None:
        """session_id 末尾带换行符应拒绝。"""
        manager = SessionManager(tmp_path)
        with pytest.raises(ValueError, match="session_id"):
            manager.load("a" * 32 + "\n")

    def test_trailing_crlf_raises(self, tmp_path: Path) -> None:
        """session_id 末尾带 \\r\\n 应拒绝。"""
        manager = SessionManager(tmp_path)
        with pytest.raises(ValueError, match="session_id"):
            manager.load("a" * 32 + "\r\n")
