"""/clear 命令单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from minicode.commands.base import CommandContext
from minicode.commands.clear_cmd import ClearCommand


class _FakeRenderer:
    """测试用假渲染器。"""

    def show_info(self, message: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        pass


class _FakeAgentLoop:
    """测试用假 AgentLoop。"""

    def __init__(self) -> None:
        self.messages: list = [
            {"role": "system", "content": "你是一个 AI 助手。"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
        ]


def _make_fake_session_manager(tmp_path: Path) -> MagicMock:
    """创建一个 Fake SessionManager。"""
    mgr = MagicMock()
    # create 方法返回一个 MagicMock session
    session = MagicMock()
    session.id = "fake-session-id"
    session.name = "2026-07-09 10:00"
    mgr.create.return_value = session
    return mgr


def _make_config() -> MagicMock:
    """创建带基本配置属性的 Mock。"""
    config = MagicMock()
    config.default_model = "deepseek-v4-flash"
    config.default_provider = "deepseek"
    return config


class TestClearCommand:
    """/clear 命令测试。"""

    def test_name(self) -> None:
        """验证命令名。"""
        cmd = ClearCommand()
        assert cmd.name == "clear"

    @pytest.mark.asyncio
    async def test_execute_with_agent_loop(self, tmp_path: Path) -> None:
        """有 AgentLoop 时，/clear 应清空消息并创建新会话。"""
        agent_loop = _FakeAgentLoop()
        session_mgr = _make_fake_session_manager(tmp_path)

        ctx = CommandContext(
            app_config=_make_config(),
            workspace_root=tmp_path,
            session_manager=session_mgr,
            agent_loop=agent_loop,
            renderer=_FakeRenderer(),
            console=Console(file=None),
        )

        cmd = ClearCommand()
        result = await cmd.execute("", ctx)

        assert result.success is True
        # AgentLoop 消息应被清空
        assert len(agent_loop.messages) == 0
        # 应调用了 create 创建新会话
        session_mgr.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_without_agent_loop(self, tmp_path: Path) -> None:
        """无 AgentLoop 时（首轮前），/clear 应创建新会话但不报错。"""
        session_mgr = _make_fake_session_manager(tmp_path)

        ctx = CommandContext(
            app_config=_make_config(),
            workspace_root=tmp_path,
            session_manager=session_mgr,
            agent_loop=None,
            renderer=_FakeRenderer(),
            console=Console(file=None),
        )

        cmd = ClearCommand()
        result = await cmd.execute("", ctx)

        assert result.success is True
        # 无 agent_loop 时仅创建会话
        session_mgr.create.assert_called_once()
