"""测试 ChatApp 应用主循环。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minicode.agent import AgentLoop
from minicode.cli.app import ChatApp
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig
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
