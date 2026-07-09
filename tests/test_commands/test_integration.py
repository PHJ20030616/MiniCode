"""斜杠命令系统集成测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from minicode.cli.app import ChatApp
from minicode.commands import register_all_commands
from minicode.commands.base import CommandContext
from minicode.commands.help_cmd import HelpCommand
from minicode.commands.registry import CommandRegistry
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig
from minicode.providers.registry import MockProvider


class TestFullCommandChain:
    """命令注册 → 路由 → 执行 完整链路测试。"""

    def test_all_commands_registered(self) -> None:
        """register_all_commands 应注册 5 个命令。"""
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        register_all_commands()

        all_cmds = CommandRegistry.list_all()
        names = {c.name for c in all_cmds}
        assert names == {"quit", "help", "clear", "session", "config"}

    def test_quit_aliases_findable(self) -> None:
        """别名的命令应可通过别名查找。"""
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        register_all_commands()

        # /quit 的别名 /exit 和 /q 应能查找到 QuitCommand
        cmd_via_exit = CommandRegistry.find("exit")
        cmd_via_q = CommandRegistry.find("q")
        assert cmd_via_exit is not None
        assert cmd_via_exit.name == "quit"
        assert cmd_via_q is not None
        assert cmd_via_q.name == "quit"

    @pytest.mark.asyncio
    async def test_help_lists_all_registered(self) -> None:
        """/help 应能动态反映注册的命令变更。"""
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()

        register_all_commands()

        class _FakeRenderer:
            """测试用假渲染器。"""
            def show_info(self, message: str) -> None:
                pass
            def show_error(self, message: str) -> None:
                pass

        ctx = CommandContext(
            app_config=MagicMock(),
            workspace_root=Path.cwd(),
            session_manager=MagicMock(),
            agent_loop=None,
            renderer=_FakeRenderer(),
            console=Console(file=None),
        )

        cmd = HelpCommand()
        result = await cmd.execute("", ctx)

        assert "quit" in (result.message or "")
        assert "help" in (result.message or "")
        assert "clear" in (result.message or "")
        assert "session" in (result.message or "")
        assert "config" in (result.message or "")


@pytest.mark.asyncio
class TestChatAppCommandIntegration:
    """ChatApp 命令路由集成测试。"""

    @pytest.fixture
    def configured_app(self) -> ChatApp:
        """创建一个已注册所有命令的 ChatApp。"""

        # 显式注册命令，确保测试可独立运行
        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()
        register_all_commands()

        config = AppConfig(
            default_provider="mock",
            default_model="mock-model",
            max_tokens=4096,
            agent=AgentConfig(max_rounds=8, stream=True),
            permissions=PermissionsConfig(trust_mode=False),
            providers={
                "mock": ProviderConfig(
                    api_key="sk-test",
                    base_url="https://api.mock.com/v1",
                    models=["mock-model"],
                ),
            },
        )
        return ChatApp(config)

    async def test_help_through_chatapp(self, configured_app: ChatApp) -> None:
        """/help 通过 ChatApp._handle_input 正常工作。"""
        should_exit = await configured_app._handle_input("/help")
        assert should_exit is False

    async def test_quit_through_chatapp(self, configured_app: ChatApp) -> None:
        """/quit 通过 ChatApp 应返回 should_exit。"""
        should_exit = await configured_app._handle_input("/quit")
        assert should_exit is True

    @patch("minicode.cli.app.ProviderRegistry.get")
    async def test_clear_then_chat(
        self, mock_get: MagicMock, configured_app: ChatApp
    ) -> None:
        """/clear 后应能继续对话。"""
        mock_get.return_value = MockProvider("模拟回复")

        # 先进行一次对话
        await configured_app._handle_input("你好")
        agent_loop = configured_app._agent_loop
        assert agent_loop is not None
        msg_count_before = len(agent_loop.messages)
        assert msg_count_before > 0  # 至少有 system + user + assistant

        # 执行 /clear
        should_exit = await configured_app._handle_input("/clear")
        assert should_exit is False
        # AgentLoop 消息应被清空
        assert len(agent_loop.messages) == 0

        # 继续对话应该正常工作
        agent_loop.run = AsyncMock()  # type: ignore[method-assign]
        await configured_app._handle_input("继续对话")
        agent_loop.run.assert_called_once()
