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
from minicode.commands.session_cmd import (
    SessionCommand,
    _compute_scroll_offset,
    _get_display_summary,
)


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


class _EnterInput:
    """Fake input that immediately accepts the highlighted session."""

    @contextmanager
    def raw_mode(self):
        yield

    def read_keys(self) -> list[KeyPress]:
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
        """/session list 显示序号和概要。"""
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
        # 应包含序号和概要（name 降级为概要）
        assert "1." in msg
        assert "2026-07-09" in msg

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
    async def test_interactive_select_renders_summary_only_table(
        self, tmp_path: Path
    ) -> None:
        """/session 交互表格只显示序号和概要。"""
        sessions = [
            {
                "id": "a" * 32,
                "name": "旧名称",
                "summary": "第一条指令",
                "updated_at": "2026-07-09T10:30:00+00:00",
                "model": "deepseek-v4-flash",
                "message_count": 5,
            }
        ]
        ctx = CommandContext(
            app_config=None,  # type: ignore[arg-type]
            workspace_root=tmp_path,
            session_manager=_make_fake_session_manager(sessions),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )
        rendered_tables = []

        class CapturingLive:
            def __init__(self, table, **kwargs) -> None:  # type: ignore[no-untyped-def]
                rendered_tables.append(table)

            def __enter__(self):  # type: ignore[no-untyped-def]
                return self

            def __exit__(self, *args) -> None:  # type: ignore[no-untyped-def]
                return None

            def update(self, table) -> None:  # type: ignore[no-untyped-def]
                rendered_tables.append(table)

        cmd = SessionCommand()
        with (
            patch("minicode.commands.session_cmd.create_input", return_value=_EnterInput()),
            patch("minicode.commands.session_cmd.Live", CapturingLive),
        ):
            selected_id = await cmd._interactive_select(sessions, ctx)

        assert selected_id == "a" * 32
        table = rendered_tables[0]
        assert [column.header for column in table.columns] == ["#", "概要"]
        assert "第一条指令" in table.columns[1]._cells
        assert "旧名称" not in table.columns[1]._cells

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


class TestGetDisplaySummary:
    """_get_display_summary 向后兼容性测试。"""

    def test_uses_summary_when_present(self) -> None:
        """优先使用 summary 字段。"""
        s = {"summary": "帮我修复登录", "name": "2026-07-09 10:00"}
        assert _get_display_summary(s) == "帮我修复登录"

    def test_falls_back_to_name(self) -> None:
        """没有 summary 时降级使用 name。"""
        s = {"name": "2026-07-09 10:00"}
        assert _get_display_summary(s) == "2026-07-09 10:00"

    def test_falls_back_to_default(self) -> None:
        """summary 和 name 都为空时使用 （无概要）。"""
        s: dict = {}
        assert _get_display_summary(s) == "（无概要）"

    def test_empty_summary_falls_back(self) -> None:
        """summary 为空字符串时降级使用 name。"""
        s = {"summary": "", "name": "fallback"}
        assert _get_display_summary(s) == "fallback"


class TestComputeScrollOffset:
    """_compute_scroll_offset 滚动窗口计算测试。"""

    def test_no_change_when_within_window(self) -> None:
        """selected_idx 在窗口内时 scroll_offset 不变。"""
        result = _compute_scroll_offset(
            selected_idx=5,
            scroll_offset=0,
            visible_count=20,
            total_count=50,
        )
        assert result == 0

    def test_scroll_down_when_beyond_bottom(self) -> None:
        """selected_idx 下移超过窗口底部时 scroll_offset 增加。"""
        result = _compute_scroll_offset(
            selected_idx=20,
            scroll_offset=0,
            visible_count=20,
            total_count=50,
        )
        assert result == 1

    def test_scroll_up_when_above_top(self) -> None:
        """selected_idx 上移超过窗口顶部时 scroll_offset 减少。"""
        result = _compute_scroll_offset(
            selected_idx=2,
            scroll_offset=5,
            visible_count=20,
            total_count=50,
        )
        assert result == 2

    def test_scroll_at_edge(self) -> None:
        """selected_idx 在窗口边界时 scroll_offset 不变。"""
        result = _compute_scroll_offset(
            selected_idx=19,
            scroll_offset=0,
            visible_count=20,
            total_count=50,
        )
        assert result == 0

    def test_scroll_one_past_bottom(self) -> None:
        """selected_idx 刚好超出窗口底部 1 时 scroll_offset 增加 1。"""
        result = _compute_scroll_offset(
            selected_idx=20,
            scroll_offset=0,
            visible_count=20,
            total_count=50,
        )
        assert result == 1

    def test_to_end_of_list(self) -> None:
        """滚动到列表末尾时窗口正确对齐。"""
        result = _compute_scroll_offset(
            selected_idx=49,
            scroll_offset=30,
            visible_count=20,
            total_count=50,
        )
        assert result == 30

    def test_total_zero_returns_zero(self) -> None:
        """total_count 为 0 时返回 0。"""
        result = _compute_scroll_offset(
            selected_idx=0,
            scroll_offset=0,
            visible_count=20,
            total_count=0,
        )
        assert result == 0

    def test_visible_count_larger_than_total(self) -> None:
        """可见窗口大于总数时 scroll_offset 应为 0。"""
        result = _compute_scroll_offset(
            selected_idx=3,
            scroll_offset=0,
            visible_count=20,
            total_count=5,
        )
        assert result == 0

    def test_clamps_existing_offset_when_window_grows(self) -> None:
        """窗口变大后，已有 scroll_offset 不能超过最大合法偏移。"""
        result = _compute_scroll_offset(
            selected_idx=49,
            scroll_offset=30,
            visible_count=100,
            total_count=50,
        )
        assert result == 0

    def test_visible_count_zero_returns_zero(self) -> None:
        """visible_count 非法时返回 0，避免负偏移。"""
        result = _compute_scroll_offset(
            selected_idx=0,
            scroll_offset=10,
            visible_count=0,
            total_count=50,
        )
        assert result == 0
