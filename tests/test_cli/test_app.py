"""测试 ChatApp 应用主循环。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minicode.agent import AgentLoop
from minicode.agent.subagents.models import SubagentConfig
from minicode.cli.app import ChatApp
from minicode.commands.base import CommandResult
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig
from minicode.providers.base import Message
from minicode.providers.registry import MockProvider
from minicode.utils.exceptions import ProviderError


def _make_config(**overrides: Any) -> AppConfig:
    """创建一个测试用 AppConfig。"""
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
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


@pytest.fixture
def app_config() -> AppConfig:
    """创建一个测试用 AppConfig。"""
    return _make_config()


@pytest.fixture
def chat_app(app_config: AppConfig) -> ChatApp:
    """创建一个测试用 ChatApp 实例。"""
    return ChatApp(app_config)


class TestGetAgentLoop:
    """测试 AgentLoop 实例创建与缓存。"""

    def test_get_agent_loop_success(self, chat_app: ChatApp) -> None:
        """配置正确的 Provider 应能成功创建 AgentLoop。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("测试回复"),
        ):
            agent_loop = chat_app._get_agent_loop()
            assert agent_loop is not None
            assert isinstance(agent_loop, AgentLoop)

    def test_get_agent_loop_unknown_provider(self, chat_app: ChatApp) -> None:
        """未配置的 Provider 应抛出 ProviderError。"""
        chat_app.config.default_provider = "nonexistent"
        with pytest.raises(ProviderError, match="未在配置中定义"):
            chat_app._get_agent_loop()

    def test_agent_loop_cached(self, chat_app: ChatApp) -> None:
        """多次调用 _get_agent_loop 应返回同一个缓存实例。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("回复"),
        ) as mock_get:
            a1 = chat_app._get_agent_loop()
            a2 = chat_app._get_agent_loop()
            assert a1 is a2
            mock_get.assert_called_once()  # 只创建一次

    def test_agent_loop_has_tools(self, chat_app: ChatApp) -> None:
        """AgentLoop 应包含默认注册的工具。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("回复"),
        ):
            agent_loop = chat_app._get_agent_loop()
            tool_names = agent_loop.tool_registry.tool_names
            assert "read_file" in tool_names
            assert "glob" in tool_names
            assert "grep" in tool_names

    def test_subagent_tool_disabled_by_default(self, chat_app: ChatApp) -> None:
        """默认关闭 subagents 时不应暴露 run_subagent。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("回复"),
        ):
            agent_loop = chat_app._get_agent_loop()
            assert "run_subagent" not in agent_loop.tool_registry.tool_names

    def test_subagent_tool_registered_when_enabled(self, app_config: AppConfig) -> None:
        """启用 subagents 时应向主 Agent 注册 run_subagent。"""
        app_config.agent.subagents = SubagentConfig(enabled=True)
        app = ChatApp(app_config)
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("回复"),
        ):
            agent_loop = app._get_agent_loop()
            tool_names = agent_loop.tool_registry.tool_names
            tool = agent_loop.tool_registry.get_tool("run_subagent")

        assert "run_subagent" in tool_names
        assert tool.name == "run_subagent"

    def test_agent_loop_has_permission_store(self, chat_app: ChatApp) -> None:
        """AgentLoop 应注入 PermissionStore。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("回复"),
        ):
            agent_loop = chat_app._get_agent_loop()
            assert agent_loop.permission_store is not None
            # 验证 store 指向正确的 workspace
            assert agent_loop.permission_store._workspace_root == chat_app.workspace_root.resolve()

    def test_agent_loop_has_permission_confirmer(self, chat_app: ChatApp) -> None:
        """AgentLoop 应注入 PermissionConfirmer。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("回复"),
        ):
            agent_loop = chat_app._get_agent_loop()
            assert agent_loop.permission_confirmer is not None
            assert agent_loop.permission_confirmer.console is chat_app.console


@pytest.mark.asyncio
class TestHandleMessage:
    """测试消息处理逻辑。"""

    async def test_delegates_to_agent_loop(self, chat_app: ChatApp) -> None:
        """_handle_message 应委托给 AgentLoop.run。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("模拟回复"),
        ):
            agent_loop = chat_app._get_agent_loop()
            agent_loop.run = AsyncMock()  # type: ignore[method-assign]

            await chat_app._handle_message("你好")
            agent_loop.run.assert_called_once_with("你好")

    async def test_no_duplicate_user_input_display(self, chat_app: ChatApp) -> None:
        """正常对话不应调用 renderer.show_user_input。"""
        show_input_calls: list[str] = []
        chat_app.renderer.show_user_input = show_input_calls.append  # type: ignore[method-assign]
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("模拟回复"),
        ):
            agent_loop = chat_app._get_agent_loop()
            agent_loop.run = AsyncMock()  # type: ignore[method-assign]
            await chat_app._handle_message("你好")
        assert show_input_calls == []  # 没有调用 show_user_input

    async def test_provider_error_does_not_crash(self, chat_app: ChatApp) -> None:
        """获取 Provider 时的错误不应导致崩溃。"""
        chat_app.config.default_provider = "nonexistent"
        await chat_app._handle_message("你好")  # 不应抛异常

    async def test_provider_error_on_handle_rolls_back(self, chat_app: ChatApp) -> None:
        """AgentLoop.run 中发生 ProviderError 时，用户消息应回滚。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("模拟回复"),
        ):
            agent_loop = chat_app._get_agent_loop()

            async def failing_run(user_input: str) -> str | None:
                raise ProviderError("API 错误")

            agent_loop.run = failing_run  # type: ignore[method-assign]
            history_len = len(agent_loop.messages)
            await chat_app._handle_message("你好")
            assert len(agent_loop.messages) == history_len  # 已回滚

    async def test_auto_save_skipped_when_run_returns_none(
        self, chat_app: ChatApp, tmp_path: Path
    ) -> None:
        """AgentLoop.run 返回 None 时不应自动创建或更新 session 文件。"""
        chat_app.workspace_root = tmp_path
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("模拟回复"),
        ):
            agent_loop = chat_app._get_agent_loop()

            async def none_run(user_input: str) -> str | None:
                return None  # 模拟 AgentLoop 失败（返回 None）

            agent_loop.run = none_run  # type: ignore[method-assign]

            await chat_app._handle_message("你好")

            # 不应创建 session 文件和 .minicode/sessions 目录
            sessions_dir = tmp_path / ".minicode" / "sessions"
            assert not sessions_dir.exists()
            # auto_save 内部的 try/except 也可能吞掉了错误，
            # 但既然没有调用 manager.save，磁盘上就不会有文件


@pytest.mark.asyncio
class TestAppRun:
    """测试主循环。"""

    @pytest.fixture
    def mock_stdout(self) -> MagicMock:
        """Mock patch_stdout 避免终端交互。"""
        with patch("minicode.cli.app.patch_stdout") as mock:
            yield mock

    async def test_exit_command(self, chat_app: ChatApp, mock_stdout: MagicMock) -> None:
        """输入 exit 应退出主循环。"""
        mock_session = AsyncMock(spec=["prompt_async"])
        mock_session.prompt_async = AsyncMock(return_value="exit")
        chat_app._prompt_session = mock_session
        await chat_app.run()

    async def test_quit_command(self, chat_app: ChatApp, mock_stdout: MagicMock) -> None:
        """输入 quit 应退出主循环。"""
        mock_session = AsyncMock(spec=["prompt_async"])
        mock_session.prompt_async = AsyncMock(return_value="quit")
        chat_app._prompt_session = mock_session
        await chat_app.run()

    async def test_empty_input_skipped(self, chat_app: ChatApp, mock_stdout: MagicMock) -> None:
        """空输入应被跳过，不触发处理。"""
        mock_session = AsyncMock(spec=["prompt_async"])
        mock_session.prompt_async = AsyncMock(side_effect=["", "exit"])
        chat_app._prompt_session = mock_session
        await chat_app.run()

    async def test_keyboard_interrupt(self, chat_app: ChatApp, mock_stdout: MagicMock) -> None:
        """Ctrl+C 应优雅退出。"""
        mock_session = AsyncMock(spec=["prompt_async"])
        mock_session.prompt_async = AsyncMock(side_effect=KeyboardInterrupt)
        chat_app._prompt_session = mock_session
        await chat_app.run()

    async def test_eof_error(self, chat_app: ChatApp, mock_stdout: MagicMock) -> None:
        """Ctrl+D 应优雅退出。"""
        mock_session = AsyncMock(spec=["prompt_async"])
        mock_session.prompt_async = AsyncMock(side_effect=EOFError)
        chat_app._prompt_session = mock_session
        await chat_app.run()

    async def test_shutdown_gracefully_with_agent_loop(
        self, chat_app: ChatApp, mock_stdout: MagicMock
    ) -> None:
        """模拟 shutdown 应保存当前会话。"""
        # 先触发 AgentLoop 创建
        with patch("minicode.cli.app.ProviderRegistry.get", return_value=MockProvider("ok")):
            agent_loop = chat_app._get_agent_loop()
            # 填充一些消息
            agent_loop.messages.append(Message(role="user", content="测试"))

        # 模拟会话存在
        session_manager = chat_app._get_session_manager()
        chat_app._current_session = session_manager.create(
            model="test", provider="test", workspace_root="/tmp"
        )

        # 执行优雅关闭
        await chat_app._shutdown_gracefully()

        # 当前会话应被保存到磁盘
        assert chat_app._current_session.id is not None
        saved = session_manager.load(chat_app._current_session.id)
        assert saved is not None

    async def test_shutdown_gracefully_no_session(
        self, chat_app: ChatApp, mock_stdout: MagicMock
    ) -> None:
        """没有活跃会话时 shutdown 不应出错。"""
        chat_app._current_session = None
        chat_app._agent_loop = None
        await chat_app._shutdown_gracefully()  # 不应抛异常

    async def test_normal_exit_saves_session(
        self, chat_app: ChatApp, mock_stdout: MagicMock
    ) -> None:
        """正常 exit 退出应保存当前会话。"""
        # 创建 AgentLoop 并填充消息
        with patch("minicode.cli.app.ProviderRegistry.get", return_value=MockProvider("ok")):
            agent_loop = chat_app._get_agent_loop()
            agent_loop.messages.append(Message(role="user", content="你好"))
            agent_loop.messages.append(Message(role="assistant", content="回复"))

        # 创建当前会话
        session_manager = chat_app._get_session_manager()
        chat_app._current_session = session_manager.create(
            model="test", provider="test", workspace_root="/tmp"
        )

        # 模拟用户输入 exit
        mock_session = AsyncMock(spec=["prompt_async"])
        mock_session.prompt_async = AsyncMock(return_value="exit")
        chat_app._prompt_session = mock_session

        original_shutdown = chat_app._shutdown_gracefully
        shutdown_called = False

        async def tracking_shutdown() -> None:
            nonlocal shutdown_called
            shutdown_called = True
            await original_shutdown()

        chat_app._shutdown_gracefully = tracking_shutdown  # type: ignore[method-assign]

        await chat_app.run()

        assert shutdown_called, "_shutdown_gracefully 应被调用"
        # 会话应包含 AgentLoop 的消息
        assert chat_app._current_session is not None
        assert len(chat_app._current_session.messages) == 2
        # 磁盘上也应有保存的文件
        saved = session_manager.load(chat_app._current_session.id)
        assert saved is not None
        assert len(saved.messages) == 2


@pytest.mark.asyncio
class TestCommandRouting:
    """命令路由集成测试。"""

    async def test_slash_quit_exits(self, chat_app: ChatApp) -> None:
        """/quit 命令应返回 True（退出）。"""
        should_exit = await chat_app._handle_input("/quit")
        assert should_exit is True

    async def test_slash_exit_exits(self, chat_app: ChatApp) -> None:
        """/exit 命令也应退出。"""
        should_exit = await chat_app._handle_input("/exit")
        assert should_exit is True

    async def test_slash_q_exits(self, chat_app: ChatApp) -> None:
        """/q 命令也应退出。"""
        should_exit = await chat_app._handle_input("/q")
        assert should_exit is True

    async def test_slash_help_does_not_exit(self, chat_app: ChatApp) -> None:
        """/help 不应触发退出。"""
        from minicode.commands.help_cmd import HelpCommand
        from minicode.commands.registry import CommandRegistry

        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()
        CommandRegistry.register(HelpCommand())

        should_exit = await chat_app._handle_input("/help")
        assert should_exit is False

    async def test_slash_config_show(self, chat_app: ChatApp) -> None:
        """/config show 应正常执行。"""
        from minicode.commands.config_cmd import ConfigCommand
        from minicode.commands.registry import CommandRegistry

        CommandRegistry._commands.clear()
        CommandRegistry._aliases.clear()
        CommandRegistry.register(ConfigCommand())

        should_exit = await chat_app._handle_input("/config")
        assert should_exit is False

    async def test_unknown_command_shows_error(self, chat_app: ChatApp) -> None:
        """未知命令应显示错误但不退出。"""
        should_exit = await chat_app._handle_input("/nonexistent_cmd_xyz")
        assert should_exit is False

    async def test_normal_text_delegates_to_agent(self, chat_app: ChatApp) -> None:
        """普通文本输入应委托给 AgentLoop。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("模拟回复"),
        ):
            agent_loop = chat_app._get_agent_loop()
            agent_loop.run = AsyncMock()  # type: ignore[method-assign]

            should_exit = await chat_app._handle_input("你好")
            assert should_exit is False
            agent_loop.run.assert_called_once_with("你好")

    async def test_slash_only_shows_error(self, chat_app: ChatApp) -> None:
        """仅输入 '/' 应显示友好错误，不应崩溃。"""
        should_exit = await chat_app._handle_input("/")
        assert should_exit is False

    async def test_history_changed_command_auto_saves_once(
        self,
        chat_app: ChatApp,
    ) -> None:
        """命令修改历史后应恰好自动保存一次。"""
        command = MagicMock()
        command.execute = AsyncMock(
            return_value=CommandResult(
                message="上下文已压缩。",
                history_changed=True,
            )
        )
        agent_loop = MagicMock(spec=AgentLoop)
        chat_app._agent_loop = agent_loop
        chat_app._auto_save = AsyncMock()  # type: ignore[method-assign]

        with patch(
            "minicode.cli.app.CommandRegistry.find",
            return_value=command,
        ):
            should_exit = await chat_app._handle_command("/compact")

        assert should_exit is False
        chat_app._auto_save.assert_awaited_once_with(agent_loop)

    async def test_unchanged_command_does_not_auto_save(
        self,
        chat_app: ChatApp,
    ) -> None:
        """命令未修改历史时不应自动保存。"""
        command = MagicMock()
        command.execute = AsyncMock(
            return_value=CommandResult(
                message="当前没有可压缩的历史上下文。",
                history_changed=False,
            )
        )
        chat_app._agent_loop = MagicMock(spec=AgentLoop)
        chat_app._auto_save = AsyncMock()  # type: ignore[method-assign]

        with patch(
            "minicode.cli.app.CommandRegistry.find",
            return_value=command,
        ):
            await chat_app._handle_command("/compact")

        chat_app._auto_save.assert_not_awaited()

    async def test_history_changed_without_agent_loop_does_not_auto_save(
        self,
        chat_app: ChatApp,
    ) -> None:
        """即使命令误报历史变化，无 AgentLoop 时也不保存。"""
        command = MagicMock()
        command.execute = AsyncMock(
            return_value=CommandResult(history_changed=True)
        )
        chat_app._agent_loop = None
        chat_app._auto_save = AsyncMock()  # type: ignore[method-assign]

        with patch(
            "minicode.cli.app.CommandRegistry.find",
            return_value=command,
        ):
            await chat_app._handle_command("/compact")

        chat_app._auto_save.assert_not_awaited()
