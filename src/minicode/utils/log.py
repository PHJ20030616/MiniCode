"""MiniCode 日志系统配置。

基于 structlog 提供结构化日志能力：
- 普通模式：日志仅作为结构化数据存在，不输出到控制台。
  用户可见的消息通过 typer.echo / Rich 渲染器直接输出。
- 调试模式（--debug）：将结构化 JSON 日志写入 .minicode/logs/，
  包含 provider、model、workspace、错误类型等上下文。
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

_LOG_DIR_NAME = "logs"
_LOG_FILENAME_FORMAT = "minicode-{timestamp}.log"
_DATETIME_FORMAT = "%Y%m%d-%H%M%S"

# MiniCode 专属 logger 名，不与第三方库共享 root logger
_MINICODE_LOGGER_NAME = "minicode"

# 日志所有级别共用的处理器链
_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.dev.set_exc_info,
    structlog.processors.format_exc_info,
]


class _SilentLogger:
    """完全静默的日志器，不产生任何 I/O。"""

    def msg(self, *args: Any, **kwargs: Any) -> None: ...
    def log(self, *args: Any, **kwargs: Any) -> None: ...
    def debug(self, *args: Any, **kwargs: Any) -> None: ...
    def info(self, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, *args: Any, **kwargs: Any) -> None: ...
    def error(self, *args: Any, **kwargs: Any) -> None: ...
    def critical(self, *args: Any, **kwargs: Any) -> None: ...
    def exception(self, *args: Any, **kwargs: Any) -> None: ...
    def fatal(self, *args: Any, **kwargs: Any) -> None: ...


class _ClearExcInfoFilter(logging.Filter):
    """清除日志记录中的异常信息，避免 stdlib Formatter 重复渲染 traceback。

    structlog 的 format_exc_info 已会将 traceback 写入 JSON 的 exception 字段，
    不需要 stdlib 的 Formatter 再追加一次。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.exc_info = None
        record.exc_text = None
        return True


class _MinicodeLoggerFactory:
    """返回 minicode 专属日志器的工厂。

    所有 structlog 消息都路由到 logging.getLogger("minicode")，
    确保文件 handler 能收到所有消息，同时避免污染 root logger。
    """

    def __call__(self, name: str | None = None) -> logging.Logger:
        return logging.getLogger(_MINICODE_LOGGER_NAME)


class _SilentLoggerFactory:
    """返回 _SilentLogger 实例的工厂，不产生任何 I/O。"""

    def __call__(self, name: str | None = None) -> _SilentLogger:
        return _SilentLogger()


def _setup_file_logging(log_dir: Path, level: int) -> Path:
    """配置 MiniCode 专属 logger 写入 JSON 格式的日志文件。

    使用 logging.getLogger("minicode") 专属 logger，propagate=False，
    不污染 root logger 的 handler 列表。

    重复初始化时会先关闭并移除旧的文件 handler。
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime(_DATETIME_FORMAT)
    log_file = log_dir / _LOG_FILENAME_FORMAT.format(timestamp=timestamp)

    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    handler.setLevel(level)
    handler.set_name("minicode-file-handler")
    handler.addFilter(_ClearExcInfoFilter())

    logger = logging.getLogger(_MINICODE_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    # 关闭并移除旧的文件 handler（仅清理自己创建的）
    for h in list(logger.handlers):
        if isinstance(h, logging.FileHandler):
            h.close()
            logger.removeHandler(h)

    logger.addHandler(handler)

    return log_file


def setup_logging(
    debug: bool = False,
    log_base_dir: Path | None = None,
    **extra_fields: Any,
) -> Path | None:
    """配置 MiniCode 日志系统。

    参数：
        debug: 是否启用调试模式。
               启用后，将结构化日志写入 log_base_dir/logs/ 目录。
        log_base_dir: 日志文件的根目录（通常为 workspace/.minicode/）。
                      为 None 时即便 debug=True 也不写入文件。
        **extra_fields: 注入到每条日志记录的额外上下文，
                        如 provider='openai'、model='gpt-4o'、workspace='/path'。

    返回：
        调试模式且写入文件时返回日志文件路径，其他情况返回 None。
    """
    # 清除旧上下文，避免多次初始化串值
    structlog.contextvars.clear_contextvars()

    log_file: Path | None = None

    if debug and log_base_dir is not None:
        log_dir = log_base_dir / _LOG_DIR_NAME
        log_file = _setup_file_logging(log_dir, logging.DEBUG)

    if log_file is not None:
        # 调试模式：通过 stdlib 专属 logger 输出 JSON 到文件
        structlog.configure(
            processors=[
                *_SHARED_PROCESSORS,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=_MinicodeLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # 普通模式：日志仅收集上下文，不输出到控制台。
        # 使用完全静默的 LoggerFactory 避免任何 I/O 操作。
        structlog.configure(
            processors=[
                *_SHARED_PROCESSORS,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=_SilentLoggerFactory(),
            cache_logger_on_first_use=True,
        )

    # 绑定额外上下文到 structlog，使每条日志自动携带这些字段
    for key, value in extra_fields.items():
        if value is not None:
            structlog.contextvars.bind_contextvars(**{key: value})

    return log_file


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """获取配置好的结构化日志器。

    用法::

        from minicode.utils.log import get_logger

        logger = get_logger(__name__)
        logger.info("配置加载完成", provider="openai", model="gpt-4o")
    """
    return structlog.get_logger(name or __name__)  # type: ignore[no-any-return]
