"""测试 ChatApp 应用主循环。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from typing import Any
from unittest.mock import patch

import pytest

from minicode.cli.app import ChatApp
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig
from minicode.providers.base import Message, StreamChunk
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
def chat_app(app_config: AppConfig) -> Generator[ChatApp, None, None]:
    """创建一个测试用 ChatApp 实例，mock 终端相关模块避免环境依赖。"""
    with patch("minicode.cli.app.PromptSession"), patch("minicode.cli.app.patch_stdout"):
        yield ChatApp(app_config)


class TestGetProvider:
    """测试 Provider 实例创建与缓存。"""

    def test_get_provider_success(self, chat_app: ChatApp) -> None:
        """配置正确的 Provider 应能成功创建。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("测试回复"),
        ):
            provider = chat_app._get_provider()
            assert provider is not None
            assert provider.name == "mock"

    def test_get_provider_unknown(self, chat_app: ChatApp) -> None:
        """未配置的 Provider 应抛出 ProviderError。"""
        chat_app.config.default_provider = "nonexistent"
        with pytest.raises(ProviderError, match="未在配置中定义"):
            chat_app._get_provider()

    def test_provider_cached_same_instance(self, chat_app: ChatApp) -> None:
        """多次调用 _get_provider 应返回同一个缓存实例。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            return_value=MockProvider("回复"),
        ) as mock_get:
            p1 = chat_app._get_provider()
            p2 = chat_app._get_provider()
            assert p1 is p2
            mock_get.assert_called_once()  # 只创建一次

    def test_provider_cache_skipped_on_error(self, chat_app: ChatApp) -> None:
        """首次创建失败不缓存，下次应重试。"""
        with patch(
            "minicode.cli.app.ProviderRegistry.get",
            side_effect=[ProviderError("暂时不可用"), MockProvider("回复")],
        ):
            with pytest.raises(ProviderError):
                chat_app._get_provider()
            # 第二次应重试（不命中缓存）
            provider = chat_app._get_provider()
            assert provider is not None


@pytest.mark.asyncio
class TestHandleMessage:
    """测试消息处理逻辑。"""

    async def _mock_provider(self, chat_app: ChatApp, response_text: str = "模拟回复") -> None:
        """将 chat_app 的 _get_provider 替换为返回 MockProvider 的 mock。"""
        mock_provider = MockProvider(response_text)
        chat_app._get_provider = lambda: mock_provider  # type: ignore[method-assign]

    async def test_user_message_appended(self, chat_app: ChatApp) -> None:
        """成功的消息处理应添加用户消息。"""
        await self._mock_provider(chat_app)
        assert len(chat_app.messages) == 0
        await chat_app._handle_message("测试消息")
        assert len(chat_app.messages) >= 1
        assert chat_app.messages[0].role == "user"
        assert chat_app.messages[0].content == "测试消息"

    async def test_assistant_response_appended(self, chat_app: ChatApp) -> None:
        """成功的消息处理应追加 assistant 回复且 content 正确。"""
        response_text = "你好！我是 MiniCode，一个 AI 编程助手。"
        await self._mock_provider(chat_app, response_text=response_text)
        await chat_app._handle_message("你是谁？")
        assert len(chat_app.messages) == 2  # user + assistant
        assert chat_app.messages[0].role == "user"
        assert chat_app.messages[0].content == "你是谁？"
        assert chat_app.messages[1].role == "assistant"
        assert chat_app.messages[1].content == response_text

    async def test_multiple_messages_alternating(self, chat_app: ChatApp) -> None:
        """多轮对话消息顺序应为 user/assistant/user/assistant。"""
        await self._mock_provider(chat_app)
        await chat_app._handle_message("第一条")
        await chat_app._handle_message("第二条")
        assert len(chat_app.messages) == 4
        assert [m.role for m in chat_app.messages] == [
            "user", "assistant", "user", "assistant",
        ]
        assert chat_app.messages[0].content == "第一条"
        assert chat_app.messages[2].content == "第二条"

    async def test_provider_error_rolls_back_user_message(self, chat_app: ChatApp) -> None:
        """ProviderError 导致失败时，用户消息不应留在历史中。"""
        chat_app._get_provider = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            ProviderError("API key 无效")
        )
        await chat_app._handle_message("你好")
        assert len(chat_app.messages) == 0

    async def test_unknown_exception_rolls_back_user_message(self, chat_app: ChatApp) -> None:
        """未知异常导致失败时，用户消息不应留在历史中。"""
        chat_app._get_provider = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            RuntimeError("网络断开")
        )
        await chat_app._handle_message("你好")
        assert len(chat_app.messages) == 0

    async def test_stream_error_rolls_back_user_message(self, chat_app: ChatApp) -> None:
        """流式处理中发生 ProviderError 时，用户消息不应留在历史中。"""
        async def _raising_stream(  # type: ignore[misc]
            messages: list[Message], **kwargs: object
        ) -> AsyncIterator[StreamChunk]:
            raise ProviderError("流中断")
            yield  # pragma: no cover

        mock_provider = MockProvider("...")
        mock_provider.chat = _raising_stream  # type: ignore[method-assign]
        chat_app._get_provider = lambda: mock_provider  # type: ignore[method-assign]
        await chat_app._handle_message("你好")
        assert len(chat_app.messages) == 0

    async def test_empty_response_rolls_back_user_message(self, chat_app: ChatApp) -> None:
        """流式返回空回复（无文本）时，用户消息应回滚。"""
        mock_provider = MockProvider(response_text="")
        chat_app._get_provider = lambda: mock_provider  # type: ignore[method-assign]
        await chat_app._handle_message("你好")
        assert len(chat_app.messages) == 0

    async def test_multiple_rounds_consecutive_success(self, chat_app: ChatApp) -> None:
        """连续多轮成功对话后，历史完整且未回滚多余消息。"""
        await self._mock_provider(chat_app, response_text="收到")
        await chat_app._handle_message("第一轮")
        await chat_app._handle_message("第二轮")
        await chat_app._handle_message("第三轮")
        assert len(chat_app.messages) == 6
        assert [m.role for m in chat_app.messages] == [
            "user", "assistant",
            "user", "assistant",
            "user", "assistant",
        ]

    async def test_agent_stream_config_passed_to_provider(self, chat_app: ChatApp) -> None:
        """agent.stream 配置应传递给 provider.chat 的 stream 参数。"""
        mock_provider = MockProvider("回复")
        chat_app._get_provider = lambda: mock_provider  # type: ignore[method-assign]

        call_kwargs: dict[str, object] = {}

        async def spy_chat(
            messages: list[Message], **kwargs: object
        ) -> AsyncIterator[StreamChunk]:
            call_kwargs.clear()
            call_kwargs.update(kwargs)
            async for chunk in mock_provider.chat(messages=messages, **kwargs):
                yield chunk

        mock_provider.chat = spy_chat  # type: ignore[method-assign]

        # 默认 stream=True
        await chat_app._handle_message("hi")
        assert call_kwargs.get("stream") is True

        # stream=False
        chat_app.config.agent.stream = False
        chat_app.messages.clear()
        call_kwargs.clear()
        await chat_app._handle_message("hi")
        assert call_kwargs.get("stream") is False


@pytest.mark.asyncio
class TestAppRun:
    """测试主循环。"""

    async def test_exit_command(self, chat_app: ChatApp) -> None:
        """输入 exit 应退出主循环。"""
        async def _prompt(_: str = "") -> str:
            return "exit"
        chat_app.session.prompt_async = _prompt  # type: ignore[method-assign]
        await chat_app.run()

    async def test_quit_command(self, chat_app: ChatApp) -> None:
        """输入 quit 应退出主循环。"""
        async def _prompt(_: str = "") -> str:
            return "quit"
        chat_app.session.prompt_async = _prompt  # type: ignore[method-assign]
        await chat_app.run()

    async def test_empty_input_skipped(self, chat_app: ChatApp) -> None:
        """空输入应被跳过，不触发处理。"""
        inputs = iter(["", "exit"])
        async def _prompt(_: str = "") -> str:
            return next(inputs)
        chat_app.session.prompt_async = _prompt  # type: ignore[method-assign]
        await chat_app.run()
        assert len(chat_app.messages) == 0

    async def test_keyboard_interrupt(self, chat_app: ChatApp) -> None:
        """Ctrl+C 应优雅退出。"""
        async def _raise_keyboard_interrupt(_: str = "") -> str:
            raise KeyboardInterrupt

        chat_app.session.prompt_async = _raise_keyboard_interrupt  # type: ignore[method-assign]
        await chat_app.run()

    async def test_eof_error(self, chat_app: ChatApp) -> None:
        """Ctrl+D 应优雅退出。"""
        async def _raise_eof_error(_: str = "") -> str:
            raise EOFError

        chat_app.session.prompt_async = _raise_eof_error  # type: ignore[method-assign]
        await chat_app.run()

    async def test_normal_chat_then_exit(self, chat_app: ChatApp) -> None:
        """先正常对话再退出。"""
        chat_app._get_provider = lambda: MockProvider("正常回复")  # type: ignore[method-assign]
        inputs = iter(["你好", "exit"])
        async def _prompt(_: str = "") -> str:
            return next(inputs)
        chat_app.session.prompt_async = _prompt  # type: ignore[method-assign]
        await chat_app.run()
        assert len(chat_app.messages) > 0

    async def test_normal_chat_does_not_echo_user_input_twice(
        self, chat_app: ChatApp
    ) -> None:
        """prompt_toolkit 已显示输入，正常对话不应再手动渲染一次。"""
        chat_app._get_provider = lambda: MockProvider("正常回复")  # type: ignore[method-assign]
        rendered_inputs: list[str] = []
        chat_app.renderer.show_user_input = rendered_inputs.append  # type: ignore[method-assign]
        inputs = iter(["你好", "exit"])

        async def _prompt(_: str = "") -> str:
            return next(inputs)

        chat_app.session.prompt_async = _prompt  # type: ignore[method-assign]
        await chat_app.run()

        assert rendered_inputs == []

    async def test_all_errors_caught_in_loop(self, chat_app: ChatApp) -> None:
        """主循环中的各种错误不应导致崩溃。"""
        chat_app._get_provider = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
            RuntimeError("意外错误")
        )
        inputs = iter(["你好", "exit"])
        async def _prompt(_: str = "") -> str:
            return next(inputs)
        chat_app.session.prompt_async = _prompt  # type: ignore[method-assign]
        await chat_app.run()


class TestMessageHistory:
    """测试消息历史管理。"""

    def test_initial_empty(self, chat_app: ChatApp) -> None:
        """初始消息列表应为空。"""
        assert chat_app.messages == []

    def test_manual_append(self, chat_app: ChatApp) -> None:
        """手动添加消息应正确。"""
        chat_app.messages.append(Message(role="user", content="测试"))
        assert len(chat_app.messages) == 1

    def test_clear_messages(self, chat_app: ChatApp) -> None:
        """清空消息列表应重置。"""
        chat_app.messages.append(Message(role="user", content="测试"))
        chat_app.messages.clear()
        assert chat_app.messages == []
