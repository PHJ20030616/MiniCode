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
        assert "timed out" in exc_info.value.last_error.lower()
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
