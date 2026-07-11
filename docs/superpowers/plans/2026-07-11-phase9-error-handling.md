# Phase 9: 错误处理与稳定性 — 执行计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MiniCode 在异常条件下稳定运行：网络抖动自动恢复、错误消息具体可操作、全局异常不丢会话数据

**Architecture:** 三层错误处理：Provider 层重试 transient 错误（指数退避 3 次），Agent Loop 层处理 Provider 错误和工具异常，全局层负责信号处理和优雅关闭

**Tech Stack:** Python 3.12+, Pydantic v2, openai SDK, structlog, pytest-asyncio

## Global Constraints

- Python 3.12+ 语法（`|` union types, `Annotated` for metadata）
- Pydantic v2 BaseModel 用于所有配置模型
- 所有用户可见文字使用中文
- 所有 catch 块记录 debug 日志（`logger.debug`）
- 重试 jitter 在测试中可通过依赖注入控制
- 会话保存 fail-soft（记录 debug 日志，不阻断主流程）
- 信号处理使用 `patch_stdout` 上下文避免与 prompt_toolkit 冲突

---

### Task 1: 指数退避重试工具

**Files:**
- Create: `src/minicode/utils/retry.py` — RetryConfig, is_retryable, async_retry
- Modify: `src/minicode/utils/exceptions.py:38` — 追加 RetryExhaustedError
- Create: `tests/test_utils/test_retry.py` — 完整单元测试

**Interfaces:**
- Consumes: `openai` 异常类型（APITimeoutError, APIConnectionError, RateLimitError, InternalServerError, AuthenticationError, APIStatusError）
- Produces: `RetryConfig(BaseModel)`, `is_retryable(e: Exception) -> bool`, `async_retry(fn, *args, config=None, **kwargs) -> T`, `RetryExhaustedError`

- [ ] **Step 1: 在 exceptions.py 末尾追加 RetryExhaustedError**

```python
class RetryExhaustedError(MiniCodeError):
    """重试耗尽错误。

    当 transient 错误在指定次数的重试后仍无法恢复时抛出。
    包含重试次数和最后一次错误的详情，供上层渲染友好消息。
    """

    def __init__(self, message: str, attempts: int = 0, last_error: str = "") -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(message)
```

在 `src/minicode/utils/exceptions.py` 文件末尾、`ToolError` 类之后追加此新类。

- [ ] **Step 2: 创建 test_retry.py 并编写测试**

文件中保留现有 import（`from __future__ import annotations`），测试代码如下：

```python
"""重试工具单元测试。

验证 RetryConfig、is_retryable、async_retry 的行为。
"""

from __future__ import annotations

import asyncio
from typing import Any

import openai
import pytest

from minicode.utils.exceptions import RetryExhaustedError
from minicode.utils.retry import RetryConfig, async_retry, is_retryable


# ─── is_retryable 测试 ────────────────────────────────────────


class MockRequest:
    def __init__(self) -> None:
        self.method = "POST"
        self.url = "https://test.com/v1/chat/completions"
        self.headers: dict[str, str] = {}
        self.content = b""


class TestIsRetryable:
    """验证可重试和不可重试异常的判断。"""

    def test_timeout_is_retryable(self) -> None:
        assert is_retryable(openai.APITimeoutError("timeout")) is True

    def test_connection_error_is_retryable(self) -> None:
        request = MockRequest()
        assert is_retryable(openai.APIConnectionError(message="refused", request=request)) is True

    def test_rate_limit_is_retryable(self) -> None:
        response = type("MockResponse", (), {"status_code": 429, "headers": {}, "request": MockRequest()})()
        assert is_retryable(openai.RateLimitError("ratelimit", response=response, body=None)) is True

    def test_server_error_is_retryable(self) -> None:
        response = type("MockResponse", (), {"status_code": 500, "headers": {}, "request": MockRequest()})()
        assert is_retryable(openai.InternalServerError("500", response=response, body=None)) is True

    def test_auth_error_not_retryable(self) -> None:
        response = type("MockResponse", (), {"status_code": 401, "headers": {}, "request": MockRequest()})()
        assert is_retryable(openai.AuthenticationError("401", response=response, body=None)) is False

    def test_bad_request_not_retryable(self) -> None:
        response = type("MockResponse", (), {"status_code": 400, "headers": {}, "request": MockRequest()})()
        assert is_retryable(openai.APIStatusError("400", response=response, body=None)) is False

    def test_not_openai_error_not_retryable(self) -> None:
        assert is_retryable(ValueError("not openai")) is False


# ─── async_retry 测试 ─────────────────────────────────────────


class TestAsyncRetry:
    """验证重试行为：成功、重试后成功、重试耗尽、不可重试立即抛出。"""

    async def test_success_first_attempt(self) -> None:
        """第一次调用成功，不应重试。"""
        call_count = 0

        async def succeed() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await async_retry(succeed)
        assert result == "ok"
        assert call_count == 1

    async def test_retry_then_succeed(self) -> None:
        """前 2 次失败，第 3 次成功。"""
        call_count = 0

        async def eventually_succeed() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise openai.APITimeoutError("timeout")
            return "recovered"

        result = await async_retry(eventually_succeed)
        assert result == "recovered"
        assert call_count == 3

    async def test_retry_exhausted(self) -> None:
        """连续 4 次失败后应抛出 RetryExhaustedError。"""
        call_count = 0

        async def always_fail() -> str:
            nonlocal call_count
            call_count += 1
            raise openai.APITimeoutError("always timeout")

        with pytest.raises(RetryExhaustedError) as exc_info:
            await async_retry(always_fail)

        assert exc_info.value.attempts == 3
        assert "always timeout" in exc_info.value.last_error
        assert call_count == 4  # 初始调用 + 3 次重试

    async def test_non_retryable_raises_immediately(self) -> None:
        """不可重试异常应直接抛出，不应重试。"""
        call_count = 0

        async def auth_fail() -> str:
            nonlocal call_count
            call_count += 1
            response = type("MockResponse", (), {"status_code": 401, "headers": {}, "request": MockRequest()})()
            raise openai.AuthenticationError("bad key", response=response, body=None)

        with pytest.raises(openai.AuthenticationError):
            await async_retry(auth_fail)
        assert call_count == 1  # 只调用了一次

    async def test_retry_config_custom(self) -> None:
        """自定义 RetryConfig 应生效（max_retries=1）。"""
        call_count = 0
        config = RetryConfig(max_retries=1, base_delay=0.01, jitter=0.0)

        async def fail_twice() -> str:
            nonlocal call_count
            call_count += 1
            raise openai.APITimeoutError("timeout")

        with pytest.raises(RetryExhaustedError):
            await async_retry(fail_twice, config=config)
        assert call_count == 2  # 初始 + 1 次重试

    async def test_retry_with_args_kwargs(self) -> None:
        """args/kwargs 应正确传递给被调函数。"""
        async def echo(msg: str, suffix: str = "") -> str:
            return msg + suffix

        result = await async_retry(echo, "hello", suffix=" world")
        assert result == "hello world"
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest tests/test_utils/test_retry.py -v
```
预期：失败，ModuleNotFoundError（retry.py 不存在）。

- [ ] **Step 4: 实现 retry.py**

创建 `src/minicode/utils/retry.py`：

```python
"""指数退避重试工具。

为 MiniCode Provider 提供异步重试能力，支持：
- 可配置的重试次数、初始延迟、最大延迟、随机抖动
- 可重试/不可重试异常的自动判断
- 重试耗尽时抛出 RetryExhaustedError
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import openai
from pydantic import BaseModel

from minicode.utils.exceptions import RetryExhaustedError

T = TypeVar("T")


class RetryConfig(BaseModel):
    """重试配置。

    Attributes:
        max_retries: 最大重试次数（默认 3），初始调用不计入。
        base_delay: 初始延迟秒数（默认 1.0）。
        max_delay: 最大延迟秒数（默认 10.0）。
        jitter: 随机抖动范围秒数（默认 0.5）。
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 10.0
    jitter: float = 0.5


def is_retryable(error: Exception) -> bool:
    """判断异常是否应触发重试。

    可重试（transient 错误）：
    - APITimeoutError / APIConnectionError：网络层临时故障
    - RateLimitError（429）：API 限流
    - InternalServerError（5xx）：服务端临时问题

    不可重试（立即上报）：
    - AuthenticationError（401）：认证失败
    - APIStatusError（400/403/404 等）：客户端请求错误
    - 其他非 openai 异常
    """
    if isinstance(error, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(error, (openai.RateLimitError, openai.InternalServerError)):
        return True
    return False


async def async_retry(
    fn: Callable[..., Awaitable[T]],
    *args: object,
    config: RetryConfig | None = None,
    **kwargs: object,
) -> T:
    """带指数退避的异步重试。

    指数退避公式：
        delay = min(base_delay * 2^attempt, max_delay) + random.uniform(-jitter, jitter)

    Args:
        fn: 要执行的异步函数。
        config: 重试配置。None 时使用默认配置（3 次重试）。
        *args: 传递给 fn 的位置参数。
        **kwargs: 传递给 fn 的关键字参数。

    Returns:
        函数执行结果。

    Raises:
        RetryExhaustedError: 所有 retryable 重试均失败后抛出，包含重试次数。
        原始异常: 非 retryable 异常直接抛出，不重试。
    """
    cfg = config or RetryConfig()
    last_exception: Exception | None = None

    for attempt in range(cfg.max_retries + 1):  # 初始调用 + max_retries 次重试
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if not is_retryable(e):
                raise  # 不可重试，立即抛出
            if attempt < cfg.max_retries:
                delay = min(cfg.base_delay * (2**attempt), cfg.max_delay)
                jitter_offset = random.uniform(-cfg.jitter, cfg.jitter)
                total_delay = max(0.0, delay + jitter_offset)
                await asyncio.sleep(total_delay)

    raise RetryExhaustedError(
        f"操作在 {cfg.max_retries} 次重试后仍然失败：{last_exception}",
        attempts=cfg.max_retries,
        last_error=str(last_exception or ""),
    )
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_utils/test_retry.py -v
```
预期：全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add src/minicode/utils/retry.py src/minicode/utils/exceptions.py tests/test_utils/test_retry.py
git commit -m "feat: 添加指数退避重试工具 (Retry Utility)"
```

---

### Task 2: Provider 重试集成与错误消息增强

**Files:**
- Modify: `src/minicode/providers/openai_compatible.py:82-120` — chat() 和 list_models() 集成 retry
- Modify: `tests/test_providers/test_openai_compatible.py` — 追加重试行为和增强错误消息测试

**Interfaces:**
- Consumes: `RetryConfig`、`async_retry`、`RetryExhaustedError` from Task 1
- Produces: 修改后的 `chat()` 方法在 transient 错误时自动重试；`_stream_chat` 重试耗尽后 yield error chunk

- [ ] **Step 1: 编写 Provider 重试测试**

在 `tests/test_providers/test_openai_compatible.py` 的 `TestErrorHandling` 类末尾追加：

```python
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
                MockChunk(choices=[MockChoice(delta=MockDelta(content="最终回复"))], model="test-model"),
                MockChunk(choices=[MockChoice(delta=MockDelta(), finish_reason="stop")], model="test-model"),
            )

        mock_client.chat.completions.create = fail_then_ok  # type: ignore[method-assign]

        provider = make_provider()
        chunks = [chunk async for chunk in provider.chat(messages=[Message(role="user", content="你好")])]

        assert call_count == 3
        assert any(c.text == "最终回复" for c in chunks if c.type == "text_delta")

    async def test_all_retries_exhausted_yields_error_chunk(self, mock_client: Any) -> None:
        """流式模式下重试耗尽应 yield error chunk。"""
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError("always timeout")

        provider = make_provider()
        chunks = [chunk async for chunk in provider.chat(messages=[Message(role="user", content="你好")])]

        # 应至少有一个 error chunk
        error_chunks = [c for c in chunks if c.type == "error"]
        assert len(error_chunks) == 1
        assert "重试" in (error_chunks[0].text or "")

    async def test_non_stream_retry_exhausted_raises(self, mock_client: Any) -> None:
        """非流式模式下重试耗尽应抛出 ProviderError。"""
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError("timeout")

        provider = make_provider()
        with pytest.raises(ProviderError, match="重试"):
            async for _ in provider.chat(messages=[Message(role="user", content="你好")], stream=False):
                pass

    async def test_auth_error_not_retried(self, mock_client: Any) -> None:
        """401 错误不应触发重试，立即抛出 ProviderError。"""
        call_count = 0

        async def auth_fail(**kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            response = type("MockResponse", (), {"status_code": 401, "headers": {}, "request": MockRequest()})()
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
```

同时在文件头部导入 `MockRequest`（或直接在测试类中定义）。

在文件顶部测试辅助区域添加 `MockRequest`：

```python
class MockRequest:
    """模拟 httpx.Request 对象。"""
    def __init__(self) -> None:
        self.method = "POST"
        self.url = "https://test.com/v1/chat/completions"
        self.headers: dict[str, str] = {}
        self.content = b""
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_providers/test_openai_compatible.py::TestRetryIntegration -v
```
预期：失败（Provider 尚未集成 retry）。

- [ ] **Step 3: 修改 openai_compatible.py 集成重试**

**修改 `chat()` 方法**（约第 82-102 行）：

```python
from minicode.utils.exceptions import ProviderError, RetryExhaustedError
from minicode.utils.retry import RetryConfig, async_retry

# ... (在 chat 方法内部)

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
```

**修改 `_stream_chat()` 方法**（约第 143-195 行）：

将 `stream = await self._client.chat.completions.create(**kwargs)` 替换为：

```python
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

    # ... 其余代码保持不变，包括 async for chunk in stream: 循环
```

**修改 `_non_stream_chat()` 方法**（约第 197-231 行）：

将 `response = await self._client.chat.completions.create(**kwargs, stream=False)` 替换为：

```python
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

    # ... 其余代码保持不变
```

**修改 `list_models()` 方法**（约第 104-120 行）：

将 `response = await self._client.models.list()` 替换为：

```python
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
    # ... 其余 except 子句保持不变
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_providers/test_openai_compatible.py -v
```
预期：全部 PASS（包括已有测试和新增的 TestRetryIntegration）。

- [ ] **Step 5: 提交**

```bash
git add src/minicode/providers/openai_compatible.py tests/test_providers/test_openai_compatible.py
git commit -m "feat: Provider 集成指数退避重试并增强错误消息"
```

---

### Task 3: Agent Loop 错误处理增强

**Files:**
- Modify: `src/minicode/agent/loop.py:127-202` — run() 增加 ProviderError 针对性处理
- Modify: `tests/test_agent/test_loop.py` — 追加 ProviderError 恢复测试

- [ ] **Step 1: 编写 Agent Loop 错误处理测试**

在 `tests/test_agent/test_loop.py` 末尾追加：

```python
# ─── Test: ProviderError 处理 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_error_rolls_back_and_returns_none(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """ProviderError 应回滚用户消息并返回 None。"""
    class FailingProvider(BaseProvider):
        @property
        def name(self) -> str:
            return "failing"

        async def chat(
            self, messages: list[Message], tools: list[dict] | None = None,
            stream: bool = True, max_tokens: int | None = None,
        ) -> AsyncIterator[StreamChunk]:
            # 直接抛出 ProviderError，模拟 chat() 中重试耗尽后的行为
            raise ProviderError("模拟 Provider 错误")

        async def list_models(self) -> list[str]:
            return []

    provider = FailingProvider()
    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "测试错误", tmp_path
    )

    assert response is None
    # 用户消息应被回滚
    assert len(loop.messages) == 0


@pytest.mark.asyncio
async def test_stream_error_chunk_handling(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """流式 error chunk 应回滚用户消息。"""
    class ErrorChunkProvider(BaseProvider):
        @property
        def name(self) -> str:
            return "error-chunk"

        async def chat(
            self, messages: list[Message], tools: list[dict] | None = None,
            stream: bool = True, max_tokens: int | None = None,
        ) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(
                type="error",
                text="请求在 3 次重试后仍然失败：API 超时",
            )

        async def list_models(self) -> list[str]:
            return []

    provider = ErrorChunkProvider()
    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "测试错误", tmp_path
    )

    assert response is None
    assert len(loop.messages) == 0  # 用户消息回滚
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_agent/test_loop.py::test_provider_error_rolls_back_and_returns_none -v
```
预期：失败（Agent Loop 未处理 ProviderError）。

- [ ] **Step 3: 修改 loop.py 增强错误处理**

**修改 `run()` 方法**（在 `stream = self.provider.chat(...)` 调用处增加 try/except）：

找到 `stream = self.provider.chat(...)` 这一行（当前约第 154 行），在其前后增加异常处理：

```python
            # 调用 Provider
            tools_schema = self.tool_registry.get_tools_schema()
            # memory 禁用时从 tools schema 中过滤掉 remember
            if not self._memory_enabled:
                tools_schema = [
                    t for t in tools_schema
                    if t.get("function", {}).get("name") != "remember"
                ]

            try:
                stream = self.provider.chat(
                    messages=api_messages,
                    tools=tools_schema,
                    stream=self.config.agent.stream,
                    max_tokens=self.config.max_tokens,
                )
            except ProviderError as e:
                logger.debug("Provider 调用失败", round=round_num, error=str(e))
                self.renderer.show_error(f"{e}")
                self.messages.pop()  # 回滚用户消息
                return None

            # 处理流式响应：渲染文本 + 收集 tool_call
            text_content, tool_calls, usage = await self._process_stream(stream)
```

**修改 `_execute_tools()` 方法**中的工具错误渲染（约第 372-376 行），增强工具失败时的用户提示：

```python
            if result.success:
                self.renderer.show_info(f"工具执行成功（{len(result.output)} 字符）")
            else:
                error_detail = result.error or result.output
                self.renderer.show_error(f"工具执行失败：{name} — {error_detail[:200]}")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_agent/test_loop.py -v
```
预期：全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/minicode/agent/loop.py tests/test_agent/test_loop.py
git commit -m "feat: Agent Loop ProviderError 处理增强"
```

---

### Task 4: 全局异常处理与优雅关闭

**Files:**
- Modify: `src/minicode/cli/app.py:59-82` — run() 增加信号处理 + 优雅关闭
- Modify: `src/minicode/main.py:129-136` — 全局异常日志增强
- Modify: `tests/test_cli/test_app.py` — 追加优雅关闭和全局异常测试

- [ ] **Step 1: 编写优雅关闭测试**

在 `tests/test_cli/test_app.py` 的 `TestAppRun` 类中追加：

```python
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
```

需要在文件头部导入 `Message`：

```python
from minicode.providers.base import Message
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_cli/test_app.py::TestAppRun::test_shutdown_gracefully_with_agent_loop -v
```
预期：失败（_shutdown_gracefully 方法不存在）。

- [ ] **Step 3: 修改 app.py 添加优雅关闭**

**在 `ChatApp` 类中新增 `_shutdown_gracefully` 方法**（在 `_clear_and_new_session` 方法之后）：

```python
    async def _shutdown_gracefully(self) -> None:
        """优雅关闭：保存当前会话 + 清理资源。

        在收到退出信号或全局异常时调用。
        使用 fail-soft 策略：保存失败不阻断退出流程。
        """
        logger.debug("正在优雅关闭...")
        try:
            if self._agent_loop is not None and self._current_session is not None:
                self._current_session.messages = list(self._agent_loop.messages)
                self._current_session.updated_at = datetime.now(UTC)
                self._get_session_manager().save(self._current_session)
                logger.debug("会话已保存", session_id=self._current_session.id)
        except Exception as e:
            logger.debug("优雅关闭时保存会话失败", error=str(e))
        self.renderer.show_info("再见！")
```

**在 `run()` 方法中增加信号处理**（在 while 循环前）：

```python
    async def run(self) -> None:
        """运行对话主循环。

        持续接收用户输入，调用 AgentLoop 处理，直到用户输入 exit/quit 或按 Ctrl+C/D 退出。
        注册 SIGINT 信号处理器，确保中断时优雅保存会话。
        """
        import signal

        self.renderer.show_info("输入 exit 或 Ctrl+C 退出。")

        # 注册 SIGINT 处理器：设置中断标志
        self._interrupted = False

        def _handle_sigint(signum: object, frame: object) -> None:
            self._interrupted = True

        signal.signal(signal.SIGINT, _handle_sigint)

        while True:
            if self._interrupted:
                await self._shutdown_gracefully()
                break

            try:
                # ... 下方现有的 try 块保持不变
```

同时在 `__init__` 中添加 `_interrupted` 字段初始化：

```python
        self._interrupted: bool = False
```

**在 `finally` 中确保关闭时保存**（在 `run()` 退出前）：

实际的实现应该是在 main.py 中处理，而不是在 app.py 内部，因为信号处理与 asyncio 运行器交互复杂。更务实的做法是：在 `ChatApp.run()` 中维护现有的 `KeyboardInterrupt` 捕获，并增加 `_shutdown_gracefully` 调用。

修改现有的 `run()` 方法中的 `KeyboardInterrupt` 处理：

```python
            except (KeyboardInterrupt, EOFError):
                await self._shutdown_gracefully()
                break
```

- [ ] **Step 4: 修改 main.py 增强全局异常日志**

找到 `main()` 函数中的异常处理（约第 129-136 行），增强日志：

```python
    except ConfigError as e:
        typer.echo(f"配置错误：{e}", err=True)
        logger.debug("配置加载失败", error=str(e), exc_info=True)
        raise typer.Exit(code=1) from None
    except MiniCodeError as e:
        typer.echo(f"错误：{e}", err=True)
        logger.debug("运行时错误", error=str(e), exc_info=True)
        raise typer.Exit(code=1) from None
    except Exception as e:
        typer.echo(f"发生未预期的错误：{e}", err=True)
        logger.debug("未捕获的异常", error=str(e), exc_info=True)
        raise typer.Exit(code=1) from None
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_cli/test_app.py -v
uv run pytest -k "test_main" tests/ -v  # 如果有 main 测试
```
预期：全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add src/minicode/cli/app.py src/minicode/main.py tests/test_cli/test_app.py
git commit -m "feat: 全局异常处理与优雅关闭"
```

---

### Task 5: lint 修复与最终验证

- [ ] **Step 1: 运行 lint 修复问题**

```bash
uv run ruff check . --fix
uv run mypy src/minicode
```

修复所有 lint 和类型检查问题。

- [ ] **Step 2: 运行完整测试套件**

```bash
uv run pytest --cov=src/minicode --cov-report=term
```
预期：全部 PASS，覆盖率不低于当前基线。

- [ ] **Step 3: 最终提交**

```bash
git add -A
git commit -m "chore: Phase 9 lint 修复与最终验证"
```
