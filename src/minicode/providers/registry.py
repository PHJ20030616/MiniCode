"""Provider 注册与发现。

提供全局 ProviderRegistry，支持按名称注册和获取 Provider 实例。
同时提供 MockProvider 用于测试。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from minicode.providers.base import BaseProvider, Message, StreamChunk
from minicode.utils.exceptions import ProviderError


class MockProvider(BaseProvider):
    """用于测试的 Mock Provider。

    返回预设的文本回复，不进行真实的 API 调用。
    """

    def __init__(self, response_text: str = "模拟回复：你好，我是 MiniCode！") -> None:
        self._response_text = response_text
        self._model = "mock-model"

    @property
    def name(self) -> str:
        return "mock"

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """返回预设的文本回复。"""
        yield StreamChunk(type="text_delta", text=self._response_text)
        yield StreamChunk(type="done")

    async def list_models(self) -> list[str]:
        return [self._model]


class ProviderRegistry:
    """全局 Provider 注册中心。

    支持按名称注册 Provider 类，按名称获取 Provider 实例。
    注册的可以是 Provider 类（而非实例），获取时通过 kwargs 传入配置。
    """

    _providers: dict[str, type[BaseProvider]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: type[BaseProvider]) -> None:
        """注册一个 Provider 类到指定名称。

        Args:
            name: Provider 名称，如 'openai', 'mock'
            provider_cls: BaseProvider 的子类
        """
        cls._providers[name] = provider_cls

    @classmethod
    def get(cls, name: str, **kwargs: object) -> BaseProvider:
        """获取指定名称的 Provider 实例。

        Args:
            name: Provider 名称
            **kwargs: 传递给 Provider 构造函数的参数

        Returns:
            BaseProvider 实例

        Raises:
            ProviderError: 当 Provider 未注册时
        """
        if name not in cls._providers:
            raise ProviderError(f"未知的 Provider: '{name}'，可用 Provider: {cls.list_providers()}")
        provider_cls = cls._providers[name]
        return provider_cls(**kwargs)

    @classmethod
    def list_providers(cls) -> list[str]:
        """返回所有已注册的 Provider 名称列表。"""
        return list(cls._providers.keys())

    @classmethod
    def unregister(cls, name: str) -> None:
        """移除指定名称的 Provider 注册。"""
        cls._providers.pop(name, None)
