"""/session 命令单元测试。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from rich.console import Console

from minicode.commands.base import CommandContext
from minicode.commands.session_cmd import SessionCommand


class _FakeRenderer:
    """测试用假渲染器。"""

    def show_info(self, message: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        pass


class _FakeAgentLoop:
    """测试用假 AgentLoop。"""

    def __init__(self) -> None:
        self.messages: list = [{"role": "user", "content": "test"}]


class _RawModeAwareInput:
    """Fake input that exposes arrow keys only while raw mode is active."""

    def __init__(self) -> None:
        self._raw_mode = False
        self._raw_reads = 0

    @contextmanager
    def raw_mode(self):
        self._raw_mode = True
        try:
            yield
        finally:
            self._raw_mode = False

    def read_keys(self) -> list[KeyPress]:
        if not self._raw_mode:
            return [KeyPress(Keys.Enter)]

        self._raw_reads += 1
        if self._raw_reads == 1:
            return [KeyPress(Keys.Down)]
        return [KeyPress(Keys.Enter)]


def _make_fake_session_manager(sessions: list[dict] | None = None) -> MagicMock:
    """创建一个 Fake SessionManager。"""
    mgr = MagicMock()
    mgr.list_sessions.return_value = sessions or [
        {
            "id": "a" * 32,
            "name": "2026-07-09 10:00",
            "created_at": "2026-07-09T10:00:00+00:00",
            "updated_at": "2026-07-09T10:30:00+00:00",
            "model": "deepseek-v4-flash",
            "provider": "deepseek",
            "message_count": 5,
        },
        {
            "id": "b" * 32,
            "name": "2026-07-08 15:00",
            "created_at": "2026-07-08T15:00:00+00:00",
            "updated_at": "2026-07-08T15:20:00+00:00",
            "model": "gpt-4o",
            "provider": "openai",
            "message_count": 12,
        },
    ]
    loaded_session = MagicMock()
    loaded_session.id = "a" * 32
    loaded_session.name = "2026-07-09 10:00"
    loaded_session.messages = [{"role": "user", "content": "previous"}]
    mgr.load.return_value = loaded_session
    mgr.delete.return_value = True
    return mgr


class TestSessionCommand:
    """/session 命令测试。"""

    def test_name(self) -> None:
        """验证命令名。"""
        cmd = SessionCommand()
        assert cmd.name == "session"

    @pytest.mark.asyncio
    async def test_list(self, tmp_path: Path) -> None:
        """/session list 应列出所有会话摘要。"""
        session_mgr = _make_fake_session_manager()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute("list", ctx)

        assert result.success is True
        msg = result.message or ""
        # 应包含会话 ID 和名称
        assert "a" * 8 in msg  # ID 前 8 位
        assert "2026-07-09" in msg
        assert "5" in msg  # message_count

    @pytest.mark.asyncio
    async def test_switch_valid_id(self, tmp_path: Path) -> None:
        """/session switch <valid_id> 应成功加载会话。"""
        session_mgr = _make_fake_session_manager()
        agent_loop = _FakeAgentLoop()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=agent_loop,  # type: ignore[arg-type]
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute(f"switch {'a' * 32}", ctx)

        assert result.success is True
        session_mgr.load.assert_called_once()
        # AgentLoop 消息应被替换为加载会话的消息
        assert len(agent_loop.messages) > 0

    @pytest.mark.asyncio
    async def test_switch_missing_id(self, tmp_path: Path) -> None:
        """/session switch 缺少 ID 应提示用法。"""
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=_make_fake_session_manager(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute("switch", ctx)

        assert result.success is False
        assert "用法" in (result.message or "")

    @pytest.mark.asyncio
    async def test_switch_nonexistent_id(self, tmp_path: Path) -> None:
        """/session switch 不存在的 ID 应报错。"""
        session_mgr = _make_fake_session_manager()
        session_mgr.load.return_value = None  # 加载失败
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute(f"switch {'c' * 32}", ctx)

        assert result.success is False
        assert "不存在" in (result.message or "")

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path: Path) -> None:
        """/session delete <id> 应删除指定会话。"""
        session_mgr = _make_fake_session_manager()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute(f"delete {'b' * 32}", ctx)

        assert result.success is True
        session_mgr.delete.assert_called_once_with("b" * 32)

    @pytest.mark.asyncio
    async def test_delete_missing_id(self, tmp_path: Path) -> None:
        """/session delete 缺少 ID 应提示用法。"""
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=_make_fake_session_manager(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute("delete", ctx)

        assert result.success is False
        assert "用法" in (result.message or "")

    @pytest.mark.asyncio
    async def test_empty_args_starts_interactive(self, tmp_path: Path) -> None:
        """/session 无参数应启动交互式选择。"""
        session_mgr = _make_fake_session_manager()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        # 使用 patch 模拟交互式选择的返回值
        with patch.object(cmd, "_interactive_select", return_value="a" * 32):
            result = await cmd.execute("", ctx)

        # 交互选择返回了 ID，应尝试加载
        assert result.success is True

    # ─── 前缀匹配测试 ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_interactive_select_reads_arrow_keys_in_raw_mode(
        self, tmp_path: Path
    ) -> None:
        """Interactive selector should enable raw mode so arrow keys change selection."""
        sessions = _make_fake_session_manager().list_sessions.return_value
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=_make_fake_session_manager(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        fake_input = _RawModeAwareInput()
        cmd = SessionCommand()

        with patch("minicode.commands.session_cmd.create_input", return_value=fake_input):
            selected_id = await cmd._interactive_select(sessions, ctx)

        assert selected_id == "b" * 32

    @pytest.mark.asyncio
    async def test_switch_with_unique_prefix(self, tmp_path: Path) -> None:
        """使用唯一前缀切换会话应成功。"""
        session_mgr = _make_fake_session_manager()
        agent_loop = _FakeAgentLoop()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=agent_loop,  # type: ignore[arg-type]
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        # 使用前 8 位唯一前缀
        result = await cmd.execute(f"switch {'a' * 8}", ctx)

        assert result.success is True
        # 应通过完整 32 位 ID 加载
        session_mgr.load.assert_called_once_with("a" * 32)

    @pytest.mark.asyncio
    async def test_switch_prefix_no_match(self, tmp_path: Path) -> None:
        """不匹配任何会话的前缀应报错。"""
        session_mgr = _make_fake_session_manager()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        result = await cmd.execute("switch zzz", ctx)

        assert result.success is False
        assert "未找到" in (result.message or "")

    @pytest.mark.asyncio
    async def test_switch_prefix_ambiguous(self, tmp_path: Path) -> None:
        """匹配多个会话的前缀应提示输入更长前缀。"""
        sessions = [
            {
                "id": "a" * 32,
                "name": "会话 A",
                "created_at": "2026-07-09T10:00:00+00:00",
                "updated_at": "2026-07-09T10:30:00+00:00",
                "model": "deepseek-v4-flash",
                "provider": "deepseek",
                "message_count": 5,
            },
            {
                "id": "a" + "b" * 31,  # 共享 "a" 前缀
                "name": "会话 AB",
                "created_at": "2026-07-08T15:00:00+00:00",
                "updated_at": "2026-07-08T15:20:00+00:00",
                "model": "gpt-4o",
                "provider": "openai",
                "message_count": 12,
            },
        ]
        session_mgr = _make_fake_session_manager(sessions)
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        # 单字符 "a" 匹配两个以 "a" 开头的会话
        result = await cmd.execute("switch a", ctx)

        assert result.success is False
        assert "匹配到多个" in (result.message or "")

    @pytest.mark.asyncio
    async def test_delete_with_prefix(self, tmp_path: Path) -> None:
        """使用唯一前缀删除会话应成功。"""
        session_mgr = _make_fake_session_manager()
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=session_mgr,  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = SessionCommand()
        # 使用前 8 位前缀
        result = await cmd.execute(f"delete {'b' * 8}", ctx)

        assert result.success is True
        session_mgr.delete.assert_called_once_with("b" * 32)
