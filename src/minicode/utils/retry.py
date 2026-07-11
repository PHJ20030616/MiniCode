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
