"""MiniCode 异常层级。

所有 MiniCode 自定义异常都继承自 MiniCodeError，便于顶层统一捕获
并显示友好的错误信息（而非长 traceback）。
"""


class MiniCodeError(Exception):
    """MiniCode 基础异常类。

    所有自定义异常的基类。顶层捕获此异常后向用户显示友好消息，
    并在 debug 模式下记录完整 traceback 到日志文件。
    """


class ConfigError(MiniCodeError):
    """配置相关的错误。

    发生在配置文件解析、API key 验证、环境变量引用等环节。
    此类错误不会打印长 traceback，直接显示错误描述。
    """


class ProviderError(MiniCodeError):
    """AI 提供商相关的错误。

    包括网络超时、认证失败（401）、限流（429）、服务端错误（5xx）、
    无效请求等。在 debug 模式下会记录详细的请求/响应上下文。
    """


class ToolError(MiniCodeError):
    """工具执行相关的错误。

    包括文件不存在、路径越权、shell 命令失败、输出截断等。
    错误信息会同时返回给模型和用户。
    """


class RetryExhaustedError(MiniCodeError):
    """重试耗尽错误。

    当 transient 错误在指定次数的重试后仍无法恢复时抛出。
    包含重试次数和最后一次错误的详情，供上层渲染友好消息。
    """

    def __init__(self, message: str, attempts: int = 0, last_error: str = "") -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(message)
