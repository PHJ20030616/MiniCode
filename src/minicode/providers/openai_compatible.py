"""OpenAI-compatible API Provider。

使用 openai.AsyncOpenAI SDK 实现兼容 OpenAI API 格式的 LLM 适配器。
适用于 DeepSeek、OpenAI、通义千问、智谱等所有兼容 OpenAI 接口的提供商。

使用方式：
    provider = OpenAICompatibleProvider(
        model="deepseek-chat",
        api_key="sk-xxx",
        base_url="https://api.deepseek.com/v1",
    )
    async for chunk in provider.chat(messages=[...]):
        ...
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import openai
from openai import AsyncOpenAI

from minicode.providers.base import (
    BaseProvider,
    Message,
    PartialToolCall,
    StreamChunk,
    UsageInfo,
)
from minicode.providers.registry import ProviderRegistry
from minicode.utils.exceptions import ProviderError, RetryExhaustedError
from minicode.utils.retry import RetryConfig, async_retry


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI-compatible API Provider。

    使用 openai.AsyncOpenAI SDK 与兼容 OpenAI API 格式的后端通信。
    通过 api_key/base_url 支持不同提供商切换，model 参数指定使用的模型。
    """

    def __init__(
        self,
        model: str,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        provider_name: str = "openai",
    ) -> None:
        """初始化 Provider。

        Args:
            model: 模型名称，如 "deepseek-chat"、"gpt-4o"
            api_key: API 密钥
            base_url: API 基础地址，默认 OpenAI 官方地址
            timeout: 请求超时时间（秒），默认 60 秒
            provider_name: 标识当前实例的提供商名称，用于日志/错误信息
        """
        self._model = model
        self._provider_name = provider_name
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    @property
    def name(self) -> str:
        return self._provider_name

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """发送对话请求并返回流式响应。

        transient 错误（超时/断网/429/5xx）自动重试最多 3 次。
        重试耗尽后流式模式 yield error chunk，非流式模式抛出 ProviderError。
        """
        kwargs = self._build_request_kwargs(messages, tools, stream, max_tokens)

        try:
            if stream:
                async for chunk in self._stream_chat(**kwargs):
                    yield chunk
            else:
                async for chunk in self._non_stream_chat(**kwargs):
                    yield chunk
        except RetryExhaustedError as e:
            error_msg = f"请求在 {e.attempts} 次重试后仍然失败。{e.last_error}"
            if stream:
                yield StreamChunk(type="error", text=error_msg)
            else:
                raise ProviderError(error_msg) from None
        except openai.APITimeoutError:
            raise ProviderError(
                f"请求超时（{self._client._timeout}s）。"
                "请检查网络连接或增加超时时间。"
            ) from None
        except openai.APIConnectionError:
            raise ProviderError(
                f"无法连接到 {self._client.base_url}。"
                "请检查：1) 网络连接 2) API 地址是否正确 3) 是否需要代理"
            ) from None
        except openai.AuthenticationError as e:
            raise ProviderError(
                f"API key 认证失败（401）。"
                f"请检查：1) api_key 配置是否正确 2) base_url 是否指向正确的 API 地址\n"
                f"详情：{e}"
            ) from e
        except openai.RateLimitError as e:
            raise ProviderError(
                f"请求频率过高（429），请稍后重试或检查用量配额。\n详情：{e}"
            ) from e
        except openai.InternalServerError as e:
            raise ProviderError(
                f"服务端暂时不可用（{e.status_code}），请稍后重试。\n详情：{e}"
            ) from e
        except openai.APIStatusError as e:
            raise ProviderError(f"API 返回异常状态码 {e.status_code}：{e}") from e

    async def list_models(self) -> list[str]:
        """获取当前 Provider 可用的模型列表。

        transient 错误自动重试最多 3 次。
        """
        try:
            retry_config = RetryConfig()
            response = await async_retry(
                self._client.models.list,
                config=retry_config,
            )
            return [model.id for model in response.data]
        except RetryExhaustedError as e:
            raise ProviderError(
                f"获取模型列表在 {e.attempts} 次重试后仍然失败：{e.last_error}"
            ) from None
        except openai.APITimeoutError:
            raise ProviderError("获取模型列表超时，请检查网络连接。") from None
        except openai.APIConnectionError:
            raise ProviderError(
                "获取模型列表失败：网络连接失败，请检查网络连接或 API 地址是否正确。"
            ) from None
        except openai.AuthenticationError as e:
            raise ProviderError(f"获取模型列表失败：API key 认证失败。\n详情：{e}") from e
        except openai.APIStatusError as e:
            raise ProviderError(
                f"获取模型列表失败：API 返回异常状态码 {e.status_code}。\n详情：{e}"
            ) from e

    def _build_request_kwargs(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """构建 OpenAI API 请求参数。"""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": _convert_messages(messages),
            "stream": stream,
        }
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        if tools is not None:
            kwargs["tools"] = tools
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return kwargs

    async def _stream_chat(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        """处理流式响应的内部方法。

        使用 async_retry 包裹 API 调用，transient 错误自动重试。
        """
        retry_config = RetryConfig()
        stream = await async_retry(
            self._client.chat.completions.create,
            **kwargs,
            config=retry_config,
        )
        finished = False

        async for chunk in stream:
            # choices=[] + usage：stream_options 模式的最终 usage chunk
            if not chunk.choices:
                if chunk.usage and not finished:
                    yield StreamChunk(type="done", usage=self._extract_usage(chunk))
                    finished = True
                continue

            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            # 文本 delta
            if delta.content:
                yield StreamChunk(type="text_delta", text=delta.content)

            # 工具调用流式 delta（Phase 2 启用）
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    yield StreamChunk(
                        type="tool_call_delta",
                        tool_call=PartialToolCall(
                            id=getattr(tc_delta, "id", None),
                            index=getattr(tc_delta, "index", 0),
                            name=tc_delta.function.name if tc_delta.function else None,
                            arguments=tc_delta.function.arguments if tc_delta.function else "",
                        ),
                    )

            # 流结束处理
            if finish_reason and not finished:
                usage = self._extract_usage(chunk)
                if usage:
                    # finish_reason 自带 usage，立即结束
                    yield StreamChunk(type="done", usage=usage)
                    finished = True
                # 无 usage：不 yield done，等待后续 usage-only chunk

        # 流自然结束后 fallback：从未收到 usage 也未 yield done
        if not finished:
            yield StreamChunk(type="done", usage=None)

    async def _non_stream_chat(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        """处理非流式响应的内部方法。

        使用 async_retry 包裹 API 调用，transient 错误自动重试。
        """
        kwargs.pop("stream", None)
        retry_config = RetryConfig()
        response = await async_retry(
            self._client.chat.completions.create,
            **kwargs,
            stream=False,
            config=retry_config,
        )
        choice = response.choices[0]

        # 处理文本内容
        if choice.message.content:
            yield StreamChunk(type="text_delta", text=choice.message.content)

        # 处理工具调用（非流式 = 完整 tool_calls）
        if choice.message.tool_calls:
            for idx, tc in enumerate(choice.message.tool_calls):
                yield StreamChunk(
                    type="tool_call_delta",
                    tool_call=PartialToolCall(
                        id=tc.id,
                        index=idx,
                        name=tc.function.name,
                        arguments=tc.function.arguments,
                    ),
                )

        # 结束
        usage = None
        if response.usage:
            usage = UsageInfo(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
                total_tokens=response.usage.total_tokens or 0,
            )
        yield StreamChunk(type="done", usage=usage)

    @staticmethod
    def _extract_usage(chunk: Any) -> UsageInfo | None:
        """从流式 chunk 中提取 usage 信息。"""
        if not chunk.usage:
            return None
        return UsageInfo(
            input_tokens=chunk.usage.prompt_tokens or 0,
            output_tokens=chunk.usage.completion_tokens or 0,
            total_tokens=chunk.usage.total_tokens or 0,
        )


# ─── 消息格式转换 ─────────────────────────────────────────────────


def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """将内部 Message 列表转换为 OpenAI API 的消息格式。"""
    result: list[dict[str, Any]] = []
    for msg in messages:
        openai_msg: dict[str, Any] = {"role": msg.role}

        # 处理 content
        if msg.content is None:
            openai_msg["content"] = None
        elif isinstance(msg.content, str):
            openai_msg["content"] = msg.content
        else:
            # ContentBlock 列表 → OpenAI content parts
            openai_msg["content"] = [{"type": cb.type, "text": cb.text or ""} for cb in msg.content]

        # 处理 tool_calls（assistant 消息）
        if msg.tool_calls:
            openai_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]

        # 处理 tool_call_id（tool 消息）
        if msg.tool_call_id:
            openai_msg["tool_call_id"] = msg.tool_call_id

        # 处理 name（tool 消息可选）
        if msg.name:
            openai_msg["name"] = msg.name

        result.append(openai_msg)

    return result


# ─── 自动注册 ──────────────────────────────────────────────────


class _DeepSeekAliasProvider(OpenAICompatibleProvider):
    """OpenAICompatibleProvider 的 deepseek 注册别名。

    自动设置 provider_name="deepseek"，使得通过此别名获取的
    实例的 name 属性返回 "deepseek"，而非默认的 "openai"。
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("provider_name", "deepseek")
        super().__init__(**kwargs)


def _register_provider() -> None:
    """将 OpenAICompatibleProvider 注册到全局 ProviderRegistry。

    同时注册 openai 和 deepseek 两个别名，以匹配配置系统默认的
    default_provider: deepseek。
    """
    ProviderRegistry.register("openai", OpenAICompatibleProvider)
    ProviderRegistry.register("deepseek", _DeepSeekAliasProvider)


_register_provider()
