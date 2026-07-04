"""Provider 注册与 Mock Provider 的测试。"""

from __future__ import annotations

import pytest

from minicode.providers.base import BaseProvider, Message, StreamChunk
from minicode.providers.registry import MockProvider, ProviderRegistry
from minicode.utils.exceptions import ProviderError


class TestMockProvider:
    """MockProvider 基本功能测试。"""

    @pytest.mark.asyncio
    async def test_mock_provider_text_chat(self) -> None:
        """MockProvider 应返回预设的文本回复。"""
        provider = MockProvider(response_text="你好！")
        chunks: list[StreamChunk] = []
        async for chunk in provider.chat(messages=[Message(role="user", content="Hello")]):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].type == "text_delta"
        assert chunks[0].text == "你好！"
        assert chunks[1].type == "done"

    @pytest.mark.asyncio
    async def test_mock_provider_name(self) -> None:
        """MockProvider 的名称应为 mock。"""
        provider = MockProvider()
        assert provider.name == "mock"

    @pytest.mark.asyncio
    async def test_mock_provider_list_models(self) -> None:
        """MockProvider 应返回模型列表。"""
        provider = MockProvider()
        models = await provider.list_models()
        assert models == ["mock-model"]

    @pytest.mark.asyncio
    async def test_mock_provider_no_tools(self) -> None:
        """MockProvider 在 tools=None 时应正常工作（纯文本对话）。"""
        provider = MockProvider()
        chunks: list[StreamChunk] = []
        async for chunk in provider.chat(
            messages=[Message(role="user", content="Hello")],
            tools=None,
        ):
            chunks.append(chunk)
        assert chunks[0].type == "text_delta"
        assert chunks[0].text is not None

    @pytest.mark.asyncio
    async def test_mock_provider_default_text(self) -> None:
        """MockProvider 应有合理的默认回复文本。"""
        provider = MockProvider()
        chunks: list[StreamChunk] = []
        async for chunk in provider.chat(messages=[Message(role="user", content="Hello")]):
            chunks.append(chunk)
        assert "MiniCode" in (chunks[0].text or "")


class TestProviderRegistry:
    """ProviderRegistry 注册与获取功能测试。"""

    def setup_method(self) -> None:
        """每个测试前注册 MockProvider。"""
        ProviderRegistry.register("mock", MockProvider)

    def teardown_method(self) -> None:
        """每个测试后清理注册。"""
        ProviderRegistry.unregister("mock")

    def test_register_and_get(self) -> None:
        """注册后应能获取 Provider 实例。"""
        provider = ProviderRegistry.get("mock")
        assert isinstance(provider, MockProvider)
        assert provider.name == "mock"

    def test_get_with_kwargs(self) -> None:
        """获取 Provider 时应能传递配置参数。"""
        provider = ProviderRegistry.get("mock", response_text="自定义回复")
        assert isinstance(provider, MockProvider)
        assert provider._response_text == "自定义回复"

    def test_list_providers(self) -> None:
        """应能列出所有已注册的 Provider。"""
        providers = ProviderRegistry.list_providers()
        assert "mock" in providers

    def test_get_unknown_provider(self) -> None:
        """获取未注册的 Provider 应抛出 ProviderError。"""
        with pytest.raises(ProviderError, match="未知的 Provider"):
            ProviderRegistry.get("non_existent")

    def test_register_twice(self) -> None:
        """重复注册同一名称应覆盖。"""
        other_provider = type(
            "OtherMock",
            (MockProvider,),
            {"name": property(lambda self: "other")},
        )
        ProviderRegistry.register("mock", other_provider)
        provider = ProviderRegistry.get("mock")
        assert provider.name == "other"

    def test_unregister(self) -> None:
        """取消注册后不应再能获取。"""
        ProviderRegistry.unregister("mock")
        assert "mock" not in ProviderRegistry.list_providers()


class TestMockProviderIsBaseProvider:
    """验证 MockProvider 满足 BaseProvider 抽象契约。"""

    def test_mock_is_subclass(self) -> None:
        """MockProvider 应是 BaseProvider 的子类。"""
        assert issubclass(MockProvider, BaseProvider)

    def test_mock_can_instantiate(self) -> None:
        """MockProvider 应可直接实例化（非抽象类）。"""
        provider = MockProvider()
        assert isinstance(provider, BaseProvider)
