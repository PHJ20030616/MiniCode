"""OpenAICompatibleProvider 单元测试。

使用 mock client 验证：
- 流式文本 delta 被正确转换为内部 StreamChunk。
- 非流式响应（工具调用模式）正确转换。
- 401/429/5xx/超时 错误转换为友好的 ProviderError。
- list_models 正确返回模型列表。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import openai
import pytest

from minicode.providers.base import Message, ToolCall
from minicode.providers.openai_compatible import OpenAICompatibleProvider, _convert_messages
from minicode.providers.registry import ProviderRegistry
from minicode.utils.exceptions import ProviderError

# ─── Mock 辅助类 ──────────────────────────────────────────────────
# 模拟 OpenAI SDK 响应对象的必要属性


class MockDelta:
    """模拟 OpenAI ChatCompletionChunk.choices[0].delta。"""

    def __init__(
        self, content: str | None = None, role: str | None = None, tool_calls: Any = None
    ) -> None:
        self.content = content
        self.role = role
        self.tool_calls = tool_calls


class MockChoice:
    """模拟 OpenAI ChatCompletionChunk.choices[0]。"""

    def __init__(self, delta: MockDelta, finish_reason: str | None = None, index: int = 0) -> None:
        self.delta = delta
        self.finish_reason = finish_reason
        self.index = index


class MockUsage:
    """模拟 OpenAI response usage。"""

    def __init__(
        self, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class MockChunk:
    """模拟 OpenAI ChatCompletionChunk（流式响应的一块）。"""

    def __init__(
        self,
        choices: list[MockChoice],
        usage: MockUsage | None = None,
        model: str = "test-model",
        id: str = "test-chunk-id",
        object: str = "chat.completion.chunk",
        created: int = 0,
    ) -> None:
        self.choices = choices
        self.usage = usage
        self.model = model
        self.id = id
        self.object = object
        self.created = created


class MockToolCallDelta:
    """模拟流式 tool_call delta。"""

    def __init__(self, index: int = 0, id: str | None = None, function: Any = None) -> None:
        self.index = index
        self.id = id
        self.function = function


class MockFunctionDelta:
    """模拟流式 function delta。"""

    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class MockNonStreamingMessage:
    """模拟 ChatCompletionMessage（非流式响应的 message）。"""

    def __init__(
        self, content: str | None = None, tool_calls: Any = None, role: str = "assistant"
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.role = role


class MockNonStreamingChoice:
    """模拟非流式响应的 choice。"""

    def __init__(
        self, message: MockNonStreamingMessage, finish_reason: str = "stop", index: int = 0
    ) -> None:
        self.message = message
        self.finish_reason = finish_reason
        self.index = index


class MockNonStreamingToolCall:
    """模拟非流式响应的 tool_call。"""

    def __init__(self, id: str, function: Any, type: str = "function") -> None:
        self.id = id
        self.function = function
        self.type = type


class MockNonStreamingFunction:
    """模拟非流式响应的 function。"""

    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class MockNonStreamingResponse:
    """模拟 ChatCompletion（非流式完整响应）。"""

    def __init__(
        self,
        choices: list[MockNonStreamingChoice],
        usage: MockUsage | None = None,
        model: str = "test-model",
        id: str = "test-response-id",
        object: str = "chat.completion",
        created: int = 0,
    ) -> None:
        self.choices = choices
        self.usage = usage
        self.model = model
        self.id = id
        self.object = object
        self.created = created


class MockRequest:
    """模拟 httpx.Request 对象。"""

    def __init__(self) -> None:
        self.method = "POST"
        self.url = "https://test.com/v1/chat/completions"
        self.headers: dict[str, str] = {}
        self.content = b""


class MockModel:
    """模拟 Model 对象。"""

    def __init__(self, id: str) -> None:
        self.id = id


class MockModelList:
    """模拟 Models.list() 响应。"""

    def __init__(self, data: list[MockModel]) -> None:
        self.data = data


# ─── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _cleanup_registry() -> Any:
    """每个测试前后清理 ProviderRegistry，避免 MockProvider 干扰。"""
    for alias in ("openai", "deepseek"):
        ProviderRegistry.unregister(alias)
    yield
    for alias in ("openai", "deepseek"):
        ProviderRegistry.unregister(alias)


@pytest.fixture
def mock_client(mocker: Any) -> Any:
    """创建 mock AsyncOpenAI client。"""
    client = mocker.AsyncMock()
    client.chat.completions.create = mocker.AsyncMock()
    mocker.patch("minicode.providers.openai_compatible.AsyncOpenAI", return_value=client)
    return client


def make_provider(**kwargs: Any) -> OpenAICompatibleProvider:
    """快速创建测试用 Provider 实例。"""
    params: dict[str, Any] = {
        "model": "test-model",
        "api_key": "sk-test",
        "base_url": "https://test.com/v1",
    }
    params.update(kwargs)
    return OpenAICompatibleProvider(**params)


async def async_gen(*chunks: Any) -> AsyncGenerator[Any, None]:
    """辅助函数：将多个 chunk 转为异步生成器。"""
    for chunk in chunks:
        yield chunk


# ─── 流式文本测试 ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestStreamText:
    """验证流式文本 delta 被正确转换为内部 StreamChunk。"""

    async def test_single_text_delta(self, mock_client: Any) -> None:
        """单个文本块应转换为一个 text_delta + done。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(
                choices=[MockChoice(delta=MockDelta(content="你好，世界！"))], model="test-model"
            ),
            MockChunk(
                choices=[MockChoice(delta=MockDelta(), finish_reason="stop")],
                model="test-model",
                usage=MockUsage(5, 10, 15),
            ),
        )

        provider = make_provider()
        chunks = [
            chunk async for chunk in provider.chat(messages=[Message(role="user", content="你好")])
        ]

        assert len(chunks) == 2
        assert chunks[0].type == "text_delta"
        assert chunks[0].text == "你好，世界！"
        assert chunks[1].type == "done"
        assert chunks[1].usage is not None
        assert chunks[1].usage.input_tokens == 5
        assert chunks[1].usage.output_tokens == 10
        assert chunks[1].usage.total_tokens == 15

    async def test_multiple_text_deltas(self, mock_client: Any) -> None:
        """多个文本块应依次转换为 text_delta。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(choices=[MockChoice(delta=MockDelta(content="你好"))], model="test-model"),
            MockChunk(choices=[MockChoice(delta=MockDelta(content="，"))], model="test-model"),
            MockChunk(choices=[MockChoice(delta=MockDelta(content="世界！"))], model="test-model"),
            MockChunk(
                choices=[MockChoice(delta=MockDelta(), finish_reason="stop")],
                model="test-model",
                usage=MockUsage(3, 5, 8),
            ),
        )

        provider = make_provider()
        chunks = [
            chunk async for chunk in provider.chat(messages=[Message(role="user", content="你好")])
        ]

        text_deltas = [c for c in chunks if c.type == "text_delta"]
        assert len(text_deltas) == 3
        assert text_deltas[0].text == "你好"
        assert text_deltas[1].text == "，"
        assert text_deltas[2].text == "世界！"
        assert chunks[-1].type == "done"

    async def test_empty_choices_skipped(self, mock_client: Any) -> None:
        """choices 为空的 chunk 应跳过。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(choices=[], model="test-model"),
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Hello"))], model="test-model"),
            MockChunk(
                choices=[MockChoice(delta=MockDelta(), finish_reason="stop")],
                model="test-model",
                usage=MockUsage(1, 1, 2),
            ),
        )

        provider = make_provider()
        chunks = [
            chunk async for chunk in provider.chat(messages=[Message(role="user", content="Hi")])
        ]

        assert len(chunks) == 2
        assert chunks[0].type == "text_delta"
        assert chunks[0].text == "Hello"

    async def test_no_usage_info(self, mock_client: Any) -> None:
        """没有 usage 信息时 done chunk 的 usage 应为 None。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(
                choices=[MockChoice(delta=MockDelta(content="Hello"), finish_reason="stop")],
                model="test-model",
            ),
        )

        provider = make_provider()
        chunks = [
            chunk async for chunk in provider.chat(messages=[Message(role="user", content="Hi")])
        ]

        assert chunks[1].type == "done"
        assert chunks[1].usage is None

    async def test_with_tools_parameter(self, mock_client: Any) -> None:
        """tools 参数应传递给 API。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Done"))], model="test-model"),
            MockChunk(
                choices=[MockChoice(delta=MockDelta(), finish_reason="stop")],
                model="test-model",
            ),
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取文件",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]

        provider = make_provider()
        chunks = [
            chunk
            async for chunk in provider.chat(
                messages=[Message(role="user", content="Read file")], tools=tools
            )
        ]

        assert len(chunks) == 2
        assert chunks[0].text == "Done"

        # 验证 tools 参数已传递
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs[1]["tools"] == tools

    async def test_with_max_tokens(self, mock_client: Any) -> None:
        """max_tokens 参数应传递给 API。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(
                choices=[MockChoice(delta=MockDelta(content="Short"), finish_reason="length")],
                model="test-model",
            ),
        )

        provider = make_provider()
        chunks = [
            chunk
            async for chunk in provider.chat(
                messages=[Message(role="user", content="Hi")], max_tokens=10
            )
        ]

        assert len(chunks) == 2  # text_delta + done
        assert chunks[0].text == "Short"
        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs[1]["max_tokens"] == 10

    async def test_model_name_passed(self, mock_client: Any) -> None:
        """model 名称应正确传递给 API。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(
                choices=[MockChoice(delta=MockDelta(content="OK"), finish_reason="stop")],
                model="gpt-4o",
            ),
        )

        provider = make_provider(model="gpt-4o")
        _ = [chunk async for chunk in provider.chat(messages=[Message(role="user", content="Hi")])]

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs[1]["model"] == "gpt-4o"

    async def test_stream_options_passed(self, mock_client: Any) -> None:
        """stream=True 时应传递 stream_options={"include_usage": True}。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(
                choices=[MockChoice(delta=MockDelta(content="OK"), finish_reason="stop")],
                model="test-model",
            ),
        )

        provider = make_provider()
        _ = [chunk async for chunk in provider.chat(messages=[Message(role="user", content="Hi")])]

        call_kwargs = mock_client.chat.completions.create.call_args
        assert call_kwargs[1]["stream_options"] == {"include_usage": True}

    async def test_usage_only_final_chunk(self, mock_client: Any) -> None:
        """两阶段结束：finish_reason 无 usage → usage-only chunk 应携带 usage。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Hello"))], model="test-model"),
            # finish_reason 无 usage
            MockChunk(
                choices=[MockChoice(delta=MockDelta(), finish_reason="stop")],
                model="test-model",
            ),
            # 后续 usage-only chunk
            MockChunk(choices=[], model="test-model", usage=MockUsage(3, 6, 9)),
        )

        provider = make_provider()
        chunks = [
            chunk async for chunk in provider.chat(messages=[Message(role="user", content="Hi")])
        ]

        # 应仅有一个 done，且携带 usage
        done_chunks = [c for c in chunks if c.type == "done"]
        assert len(done_chunks) == 1
        assert done_chunks[0].usage is not None
        assert done_chunks[0].usage.input_tokens == 3
        assert done_chunks[0].usage.output_tokens == 6
        assert done_chunks[0].usage.total_tokens == 9

    async def test_finish_reason_no_usage_fallback(self, mock_client: Any) -> None:
        """finish_reason 无 usage 且无后续 usage-only chunk → fallback done usage=None。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Hello"))], model="test-model"),
            MockChunk(
                choices=[MockChoice(delta=MockDelta(), finish_reason="stop")],
                model="test-model",
            ),
        )

        provider = make_provider()
        chunks = [
            chunk async for chunk in provider.chat(messages=[Message(role="user", content="Hi")])
        ]

        assert len(chunks) == 2
        assert chunks[0].type == "text_delta"
        assert chunks[1].type == "done"
        assert chunks[1].usage is None

    async def test_usage_only_as_only_end(self, mock_client: Any) -> None:
        """仅 usage-only chunk（无前置 finish_reason）应转为 done 并携带 usage。"""
        mock_client.chat.completions.create.return_value = async_gen(
            MockChunk(choices=[MockChoice(delta=MockDelta(content="Hello"))], model="test-model"),
            MockChunk(choices=[], model="test-model", usage=MockUsage(4, 8, 12)),
        )

        provider = make_provider()
        chunks = [
            chunk async for chunk in provider.chat(messages=[Message(role="user", content="Hi")])
        ]

        assert len(chunks) == 2
        assert chunks[0].type == "text_delta"
        assert chunks[0].text == "Hello"
        assert chunks[1].type == "done"
        assert chunks[1].usage is not None
        assert chunks[1].usage.input_tokens == 4
        assert chunks[1].usage.output_tokens == 8
        assert chunks[1].usage.total_tokens == 12


# ─── 非流式响应测试（工具调用模式）─────────────────────────────────


@pytest.mark.asyncio
class TestNonStreamChat:
    """验证非流式响应（工具调用场景）正确转换。"""

    async def test_non_stream_text(self, mock_client: Any) -> None:
        """非流式文本应正确转换为 text_delta + done。"""
        mock_client.chat.completions.create.return_value = MockNonStreamingResponse(
            choices=[
                MockNonStreamingChoice(message=MockNonStreamingMessage(content="Hello, world!"))
            ],
            usage=MockUsage(10, 5, 15),
        )

        provider = make_provider()
        chunks = [
            chunk
            async for chunk in provider.chat(
                messages=[Message(role="user", content="Hi")], stream=False
            )
        ]

        assert len(chunks) == 2
        assert chunks[0].type == "text_delta"
        assert chunks[0].text == "Hello, world!"
        assert chunks[1].type == "done"
        assert chunks[1].usage is not None
        assert chunks[1].usage.input_tokens == 10

    async def test_non_stream_tool_calls(self, mock_client: Any) -> None:
        """非流式工具调用应正确转换为 tool_call_delta。"""
        mock_client.chat.completions.create.return_value = MockNonStreamingResponse(
            choices=[
                MockNonStreamingChoice(
                    message=MockNonStreamingMessage(
                        content="我来查询文件。",
                        tool_calls=[
                            MockNonStreamingToolCall(
                                id="call_001",
                                function=MockNonStreamingFunction(
                                    name="read_file",
                                    arguments='{"file_path": "test.txt"}',
                                ),
                            ),
                        ],
                    ),
                )
            ],
            usage=MockUsage(20, 10, 30),
        )

        provider = make_provider()
        chunks = [
            chunk
            async for chunk in provider.chat(
                messages=[Message(role="user", content="Read file")], stream=False
            )
        ]

        assert len(chunks) == 3
        assert chunks[0].type == "text_delta"
        assert chunks[0].text == "我来查询文件。"
        assert chunks[1].type == "tool_call_delta"
        assert chunks[1].tool_call is not None
        assert chunks[1].tool_call.id == "call_001"
        assert chunks[1].tool_call.name == "read_file"
        assert chunks[1].tool_call.arguments == '{"file_path": "test.txt"}'
        assert chunks[2].type == "done"

    async def test_non_stream_multi_tool_calls(self, mock_client: Any) -> None:
        """非流式多工具调用应分别有正确的 index 0、1。"""
        mock_client.chat.completions.create.return_value = MockNonStreamingResponse(
            choices=[
                MockNonStreamingChoice(
                    message=MockNonStreamingMessage(
                        content=None,
                        tool_calls=[
                            MockNonStreamingToolCall(
                                id="call_001",
                                function=MockNonStreamingFunction(
                                    name="glob",
                                    arguments='{"pattern": "*.py"}',
                                ),
                            ),
                            MockNonStreamingToolCall(
                                id="call_002",
                                function=MockNonStreamingFunction(
                                    name="read_file",
                                    arguments='{"file_path": "main.py"}',
                                ),
                            ),
                        ],
                    ),
                )
            ],
            usage=MockUsage(25, 15, 40),
        )

        provider = make_provider()
        chunks = [
            chunk
            async for chunk in provider.chat(
                messages=[Message(role="user", content="Find and read")], stream=False
            )
        ]

        # 提取 tool_call_delta 块
        tool_chunks = [c for c in chunks if c.type == "tool_call_delta"]
        assert len(tool_chunks) == 2
        assert tool_chunks[0].tool_call is not None
        assert tool_chunks[0].tool_call.index == 0
        assert tool_chunks[0].tool_call.id == "call_001"
        assert tool_chunks[0].tool_call.name == "glob"
        assert tool_chunks[1].tool_call is not None
        assert tool_chunks[1].tool_call.index == 1
        assert tool_chunks[1].tool_call.id == "call_002"
        assert tool_chunks[1].tool_call.name == "read_file"
        # arguments 不能合并
        assert tool_chunks[0].tool_call.arguments == '{"pattern": "*.py"}'
        assert tool_chunks[1].tool_call.arguments == '{"file_path": "main.py"}'
        # 最后应有一个 done
        assert chunks[-1].type == "done"

    async def test_non_stream_content_none(self, mock_client: Any) -> None:
        """非流式响应 content 为 None 时不应产生 text_delta。"""
        mock_client.chat.completions.create.return_value = MockNonStreamingResponse(
            choices=[
                MockNonStreamingChoice(
                    message=MockNonStreamingMessage(
                        content=None,
                        tool_calls=[
                            MockNonStreamingToolCall(
                                id="call_002",
                                function=MockNonStreamingFunction(
                                    name="glob",
                                    arguments='{"pattern": "*.py"}',
                                ),
                            ),
                        ],
                    ),
                )
            ],
        )

        provider = make_provider()
        chunks = [
            chunk
            async for chunk in provider.chat(
                messages=[Message(role="user", content="Find py files")], stream=False
            )
        ]

        # 只有 tool_call_delta + done
        assert len(chunks) == 2
        assert chunks[0].type == "tool_call_delta"
        assert chunks[1].type == "done"


# ─── 错误处理测试 ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestErrorHandling:
    """验证网络错误和 HTTP 错误被转换为友好错误。"""

    async def test_authentication_error(self, mock_client: Any) -> None:
        """401 错误应转换为 ProviderError。"""
        mock_client.chat.completions.create.side_effect = openai.AuthenticationError(
            "Invalid API key",
            response=mocker_http_response(401),
            body=None,
        )

        provider = make_provider()
        with pytest.raises(ProviderError, match="API key 认证失败"):
            async for _ in provider.chat(messages=[Message(role="user", content="Hi")]):
                pass

    async def test_rate_limit_error(self, mock_client: Any) -> None:
        """429 错误重试耗尽后应 yield error chunk。"""
        mock_client.chat.completions.create.side_effect = openai.RateLimitError(
            "Rate limit exceeded",
            response=mocker_http_response(429),
            body=None,
        )

        provider = make_provider()
        msg = Message(role="user", content="Hi")
        chunks = [chunk async for chunk in provider.chat(messages=[msg])]

        error_chunks = [c for c in chunks if c.type == "error"]
        assert len(error_chunks) == 1
        assert "重试" in (error_chunks[0].text or "")

    async def test_internal_server_error(self, mock_client: Any) -> None:
        """5xx 错误重试耗尽后应 yield error chunk。"""
        mock_client.chat.completions.create.side_effect = openai.InternalServerError(
            "Server error",
            response=mocker_http_response(500),
            body=None,
        )

        provider = make_provider()
        msg = Message(role="user", content="Hi")
        chunks = [chunk async for chunk in provider.chat(messages=[msg])]

        error_chunks = [c for c in chunks if c.type == "error"]
        assert len(error_chunks) == 1
        assert "重试" in (error_chunks[0].text or "")

    async def test_timeout_error(self, mock_client: Any) -> None:
        """超时错误重试耗尽后应 yield error chunk。"""
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError(
            "Request timed out"
        )

        provider = make_provider()
        msg = Message(role="user", content="Hi")
        chunks = [chunk async for chunk in provider.chat(messages=[msg])]

        error_chunks = [c for c in chunks if c.type == "error"]
        assert len(error_chunks) == 1
        assert "重试" in (error_chunks[0].text or "")

    async def test_connection_error(self, mock_client: Any) -> None:
        """APIConnectionError 重试耗尽后应 yield error chunk。"""
        mock_request = type(
            "MockRequest",
            (),
            {
                "method": "POST",
                "url": "https://test.com/v1/chat/completions",
                "headers": {},
                "content": b"",
            },
        )()
        mock_client.chat.completions.create.side_effect = openai.APIConnectionError(
            message="Connection refused",
            request=mock_request,
        )

        provider = make_provider()
        msg = Message(role="user", content="Hi")
        chunks = [chunk async for chunk in provider.chat(messages=[msg])]

        error_chunks = [c for c in chunks if c.type == "error"]
        assert len(error_chunks) == 1
        assert "重试" in (error_chunks[0].text or "")

    async def test_api_status_error(self, mock_client: Any) -> None:
        """其他 API 状态错误应转换为 ProviderError。"""
        mock_client.chat.completions.create.side_effect = openai.APIStatusError(
            "Bad request",
            response=mocker_http_response(400),
            body=None,
        )

        provider = make_provider()
        with pytest.raises(ProviderError, match="异常状态码 400"):
            async for _ in provider.chat(messages=[Message(role="user", content="Hi")]):
                pass


class TestRetryIntegration:
    """验证 Provider 层重试集成。"""

    async def test_transient_error_retried_then_succeeds(self, mock_client: Any) -> None:
        """transient 错误应被重试，恢复后正常返回结果。"""
        call_count = 0

        async def fail_then_ok(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise openai.APITimeoutError("timeout")
            return async_gen(
                MockChunk(
                    choices=[MockChoice(delta=MockDelta(content="最终回复"))],
                    model="test-model",
                ),
                MockChunk(
                    choices=[MockChoice(delta=MockDelta(), finish_reason="stop")],
                    model="test-model",
                ),
            )

        mock_client.chat.completions.create = fail_then_ok  # type: ignore[method-assign]

        provider = make_provider()
        msg = Message(role="user", content="你好")
        chunks = [chunk async for chunk in provider.chat(messages=[msg])]

        assert call_count == 3
        assert any(c.text == "最终回复" for c in chunks if c.type == "text_delta")

    async def test_all_retries_exhausted_yields_error_chunk(self, mock_client: Any) -> None:
        """流式模式下重试耗尽应 yield error chunk。"""
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError("always timeout")

        provider = make_provider()
        msg = Message(role="user", content="你好")
        chunks = [chunk async for chunk in provider.chat(messages=[msg])]

        # 应至少有一个 error chunk
        error_chunks = [c for c in chunks if c.type == "error"]
        assert len(error_chunks) == 1
        assert "重试" in (error_chunks[0].text or "")

    async def test_non_stream_retry_exhausted_raises(self, mock_client: Any) -> None:
        """非流式模式下重试耗尽应抛出 ProviderError。"""
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError("timeout")

        provider = make_provider()
        msg = Message(role="user", content="你好")
        with pytest.raises(ProviderError, match="重试"):
            async for _ in provider.chat(messages=[msg], stream=False):
                pass

    async def test_auth_error_not_retried(self, mock_client: Any) -> None:
        """401 错误不应触发重试，立即抛出 ProviderError。"""
        call_count = 0

        async def auth_fail(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            response = type(
                "MockResponse",
                (),
                {"status_code": 401, "headers": {}, "request": MockRequest()},
            )()
            raise openai.AuthenticationError("bad key", response=response, body=None)

        mock_client.chat.completions.create = auth_fail  # type: ignore[method-assign]

        provider = make_provider()
        with pytest.raises(ProviderError, match="API key 认证失败"):
            async for _ in provider.chat(messages=[Message(role="user", content="你好")]):
                pass
        assert call_count == 1  # 不应重试

    async def test_list_models_retry_then_succeed(self, mock_client: Any) -> None:
        """list_models 也应支持重试。"""
        call_count = 0

        async def fail_then_ok() -> Any:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise openai.APITimeoutError("timeout")
            return MockModelList(data=[MockModel("gpt-4o")])

        mock_client.models.list = fail_then_ok  # type: ignore[method-assign]

        provider = make_provider()
        models = await provider.list_models()
        assert models == ["gpt-4o"]
        assert call_count == 2


# ─── list_models 测试 ────────────────────────────────────────────


@pytest.mark.asyncio
class TestListModels:
    """验证 list_models 正确返回模型列表。"""

    async def test_list_models_success(self, mock_client: Any) -> None:
        """验证 list_models 返回正确的模型 ID 列表。"""
        mock_client.models.list = mocker_async_return(
            MockModelList(
                data=[MockModel("gpt-4o"), MockModel("gpt-4o-mini"), MockModel("deepseek-chat")]
            )
        )

        provider = make_provider()
        models = await provider.list_models()

        assert models == ["gpt-4o", "gpt-4o-mini", "deepseek-chat"]

    async def test_list_models_authentication_error(self, mock_client: Any) -> None:
        """list_models 的认证错误应转换为 ProviderError。"""
        mock_client.models.list = mocker_async_raise(
            openai.AuthenticationError("Invalid key", response=mocker_http_response(401), body=None)
        )

        provider = make_provider()
        with pytest.raises(ProviderError, match="API key 认证失败"):
            await provider.list_models()

    async def test_list_models_timeout(self, mock_client: Any) -> None:
        """list_models 的超时重试耗尽后应抛出包含重试信息的 ProviderError。"""
        mock_client.models.list = mocker_async_raise(openai.APITimeoutError("Timeout"))

        provider = make_provider()
        with pytest.raises(ProviderError, match="重试"):
            await provider.list_models()

    async def test_list_models_connection_error(self, mock_client: Any) -> None:
        """list_models 的 APIConnectionError 重试耗尽后应抛出包含重试信息的 ProviderError。"""
        mock_request = type(
            "MockRequest",
            (),
            {
                "method": "GET",
                "url": "https://test.com/v1/models",
                "headers": {},
                "content": b"",
            },
        )()
        mock_client.models.list = mocker_async_raise(
            openai.APIConnectionError(message="Connection refused", request=mock_request)
        )

        provider = make_provider()
        with pytest.raises(ProviderError, match="重试"):
            await provider.list_models()


# ─── 辅助工具 ─────────────────────────────────────────────────────


def mocker_http_response(status_code: int) -> Any:
    """创建 mock httpx.Response，包含 OpenAI SDK 所需的 request 属性。"""
    mock_request = type(
        "MockRequest",
        (),
        {
            "method": "POST",
            "url": "https://test.com/v1/chat/completions",
            "headers": {},
            "content": b"",
        },
    )()
    response = type(
        "MockResponse",
        (),
        {
            "status_code": status_code,
            "headers": {},
            "request": mock_request,
        },
    )()
    return response


def mocker_async_return(value: Any) -> Any:
    """创建一个返回指定值的异步函数。"""

    async def _mock(*args: Any, **kwargs: Any) -> Any:
        return value

    return _mock


def mocker_async_raise(exc: Exception) -> Any:
    """创建一个抛出指定异常的异步函数。"""

    async def _mock(*args: Any, **kwargs: Any) -> Any:
        raise exc

    return _mock


# ─── 消息格式转换测试 ────────────────────────────────────────────


class TestConvertMessages:
    """验证 _convert_messages 正确转换内部消息为 OpenAI 格式。"""

    def test_user_message(self) -> None:
        """用户消息应转换为 {'role': 'user', 'content': '...'}。"""
        messages = [Message(role="user", content="你好")]
        result = _convert_messages(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "你好"

    def test_system_message(self) -> None:
        """系统消息应转换为 {'role': 'system', 'content': '...'}。"""
        messages = [Message(role="system", content="你是一个助手。")]
        result = _convert_messages(messages)

        assert result[0]["role"] == "system"
        assert result[0]["content"] == "你是一个助手。"

    def test_assistant_with_tool_calls(self) -> None:
        """assistant 消息的 tool_calls 应转换为 OpenAI 格式。"""
        from minicode.providers.base import FunctionCall

        messages = [
            Message(
                role="assistant",
                content="我来读取文件。",
                tool_calls=[
                    ToolCall(
                        id="call_001",
                        function=FunctionCall(name="read_file", arguments='{"path": "test.txt"}'),
                    )
                ],
            )
        ]
        result = _convert_messages(messages)

        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == "我来读取文件。"
        assert "tool_calls" in result[0]
        assert result[0]["tool_calls"][0]["id"] == "call_001"
        assert result[0]["tool_calls"][0]["function"]["name"] == "read_file"

    def test_tool_message(self) -> None:
        """tool 消息应包含 tool_call_id。"""
        messages = [
            Message(
                role="tool", content="文件内容：Hello", tool_call_id="call_001", name="read_file"
            )
        ]
        result = _convert_messages(messages)

        assert result[0]["role"] == "tool"
        assert result[0]["content"] == "文件内容：Hello"
        assert result[0]["tool_call_id"] == "call_001"
        assert result[0]["name"] == "read_file"

    def test_content_block_list(self) -> None:
        """ContentBlock 列表应转换为 OpenAI content array。"""
        from minicode.providers.base import ContentBlock

        messages = [
            Message(
                role="user",
                content=[
                    ContentBlock(type="text", text="第一段"),
                    ContentBlock(type="text", text="第二段"),
                ],
            )
        ]
        result = _convert_messages(messages)

        assert isinstance(result[0]["content"], list)
        assert len(result[0]["content"]) == 2
        assert result[0]["content"][0]["type"] == "text"
        assert result[0]["content"][0]["text"] == "第一段"
        assert result[0]["content"][1]["text"] == "第二段"

    def test_content_none(self) -> None:
        """content 为 None 时应传递 None。"""
        messages = [Message(role="assistant", content=None)]
        result = _convert_messages(messages)

        assert result[0]["content"] is None

    def test_multiple_messages(self) -> None:
        """多条消息应正确排序。"""
        messages = [
            Message(role="system", content="助手"),
            Message(role="user", content="你好"),
            Message(role="assistant", content="你好！"),
        ]
        result = _convert_messages(messages)

        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"
        assert result[2]["role"] == "assistant"


# ─── 自动注册测试 ───────────────────────────────────────────────


class TestRegistration:
    """验证 OpenAICompatibleProvider 自动注册到 ProviderRegistry。"""

    def test_provider_auto_registered(self) -> None:
        """模块加载后应自动注册到 ProviderRegistry。"""
        # autouse fixture 先 unregister 了，需要重新注册验证
        from minicode.providers.openai_compatible import _register_provider

        _register_provider()
        provider = ProviderRegistry.get("openai", model="test-model", api_key="sk-test")
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.name == "openai"

    def test_provider_name(self) -> None:
        """Provider 的 name 属性默认应为 'openai'。"""
        provider = make_provider()
        assert provider.name == "openai"

    def test_deepseek_alias_registered(self) -> None:
        """deepseek 别名应返回 provider.name == "deepseek"。"""
        from minicode.providers.openai_compatible import _register_provider

        _register_provider()
        provider = ProviderRegistry.get(
            "deepseek",
            model="deepseek-chat",
            api_key="sk-deepseek",
            base_url="https://api.deepseek.com/v1",
        )
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.name == "deepseek"

    def test_deepseek_alias_with_provider_name(self) -> None:
        """deepseek 别名可传入 provider_name 覆盖 name。"""
        from minicode.providers.openai_compatible import _register_provider

        _register_provider()
        provider = ProviderRegistry.get(
            "deepseek",
            model="deepseek-chat",
            api_key="sk-deepseek",
            provider_name="deepseek",
        )
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.name == "deepseek"
