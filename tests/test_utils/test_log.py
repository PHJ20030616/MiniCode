"""MiniCode 日志系统的单元测试。"""

import json
import logging
from pathlib import Path

import structlog

from minicode.utils.log import get_logger, setup_logging


def _clear_structlog_context() -> None:
    """清除 structlog 上下文变量，避免测试间污染。"""
    for key in list(structlog.contextvars.get_contextvars()):
        structlog.contextvars.unbind_contextvars(key)


class TestSetupLogging:
    """测试日志系统配置。"""

    def test_default_mode_no_file(self) -> None:
        """普通模式不创建日志文件。"""
        _clear_structlog_context()
        result = setup_logging(debug=False)
        assert result is None

    def test_debug_mode_creates_log_file(self, tmp_path: Path) -> None:
        """调试模式在 .minicode/logs/ 下创建日志文件。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        result = setup_logging(debug=True, log_base_dir=log_base)
        assert result is not None
        assert result.exists()
        assert result.suffix == ".log"
        assert "minicode-" in result.name

    def test_log_file_in_subdirectory(self, tmp_path: Path) -> None:
        """日志文件位于 log_base_dir/logs/ 子目录下。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        result = setup_logging(debug=True, log_base_dir=log_base)
        assert result is not None
        assert result.parent == log_base / "logs"

    def test_log_dir_created(self, tmp_path: Path) -> None:
        """日志目录在初始化时自动创建。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        assert not (log_base / "logs").exists()
        setup_logging(debug=True, log_base_dir=log_base)
        assert (log_base / "logs").is_dir()

    def test_log_dir_created_existing(self, tmp_path: Path) -> None:
        """已存在的日志目录不会报错。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        (log_base / "logs").mkdir(parents=True)
        setup_logging(debug=True, log_base_dir=log_base)

    def test_none_log_dir_skips_file(self) -> None:
        """即使 debug=True，log_base_dir=None 也不会写入文件。"""
        _clear_structlog_context()
        result = setup_logging(debug=True, log_base_dir=None)
        assert result is None

    def test_reinit_clears_old_context(self, tmp_path: Path) -> None:
        """多次初始化时旧上下文被清除。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base, provider="old-value")
        setup_logging(debug=True, log_base_dir=log_base, provider="new-value")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("provider") == "new-value"


class TestRootLoggerIsolation:
    """测试 setup_logging 不污染 root logger。"""

    def test_root_logger_handlers_untouched(self, tmp_path: Path) -> None:
        """setup_logging 不修改 root logger 的 handler 列表。"""
        _clear_structlog_context()
        original_handlers = list(logging.getLogger().handlers)
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base)
        assert logging.getLogger().handlers == original_handlers

    def test_root_logger_level_untouched(self, tmp_path: Path) -> None:
        """setup_logging 不修改 root logger 的日志级别。"""
        _clear_structlog_context()
        original_level = logging.getLogger().level
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base)
        assert logging.getLogger().level == original_level

    def test_minicode_logger_has_handler(self, tmp_path: Path) -> None:
        """debug 模式下 minicode 专属 logger 有文件 handler。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base)
        mc_logger = logging.getLogger("minicode")
        assert len(mc_logger.handlers) >= 1
        assert any(isinstance(h, logging.FileHandler) for h in mc_logger.handlers)

    def test_minicode_logger_propagate_false(self, tmp_path: Path) -> None:
        """minicode 专属 logger 不向上传播日志。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base)
        mc_logger = logging.getLogger("minicode")
        assert mc_logger.propagate is False


class TestExtraFields:
    """测试额外上下文字段注入。"""

    def test_extra_fields_bound(self, tmp_path: Path) -> None:
        """额外字段被绑定到 structlog 上下文。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(
            debug=True,
            log_base_dir=log_base,
            provider="test-provider",
            model="test-model",
        )
        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("provider") == "test-provider"
        assert ctx.get("model") == "test-model"

    def test_none_fields_skipped(self, tmp_path: Path) -> None:
        """值为 None 的额外字段不会绑定。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(
            debug=True,
            log_base_dir=log_base,
            provider=None,
        )
        ctx = structlog.contextvars.get_contextvars()
        assert "provider" not in ctx


class TestGetLogger:
    """测试日志器获取。"""

    def test_get_logger_returns_logger(self) -> None:
        """get_logger 返回日志器对象（未配置时返回 proxy）。"""
        _clear_structlog_context()
        logger = get_logger("test")
        assert logger is not None

    def test_get_logger_after_setup(self, tmp_path: Path) -> None:
        """日志系统配置后 get_logger 可用。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base)
        logger = get_logger("test")
        assert logger is not None

    def test_get_logger_default_name(self) -> None:
        """不传名称时不报错。"""
        _clear_structlog_context()
        logger = get_logger()
        assert logger is not None


class TestLogOutput:
    """测试日志输出内容（仅调试模式写入文件）。"""

    def test_debug_log_contains_json(self, tmp_path: Path) -> None:
        """调试模式下日志文件包含 JSON 格式的关键上下文。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(
            debug=True,
            log_base_dir=log_base,
            provider="openai",
            model="gpt-4o",
        )
        logger = get_logger("test")
        logger.info("测试消息", extra_field="extra_value")

        log_entries = list((log_base / "logs").iterdir())
        assert len(log_entries) == 1

        content = log_entries[0].read_text(encoding="utf-8")
        record = json.loads(content)

        assert record["event"] == "测试消息"
        assert record["extra_field"] == "extra_value"
        assert record["provider"] == "openai"
        assert record["model"] == "gpt-4o"
        assert record["level"] == "info"

    def test_log_level_in_output(self, tmp_path: Path) -> None:
        """日志文件记录中应包含级别信息。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base)
        logger = get_logger()
        logger.warning("警告消息")

        content = next((log_base / "logs").iterdir()).read_text(encoding="utf-8")
        record = json.loads(content)
        assert record["level"] == "warning"

    def test_timestamp_in_output(self, tmp_path: Path) -> None:
        """日志记录应包含时间戳。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base)
        logger = get_logger()
        logger.info("带时间戳的消息")

        content = next((log_base / "logs").iterdir()).read_text(encoding="utf-8")
        record = json.loads(content)
        assert "timestamp" in record

    def test_exception_log_contains_traceback(self, tmp_path: Path) -> None:
        """异常日志应包含真实的 traceback 文本，而非仅 exc_info: true。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base)
        logger = get_logger("test")
        try:
            raise ValueError("测试异常")
        except ValueError:
            logger.exception("发生异常")

        log_entries = list((log_base / "logs").iterdir())
        assert len(log_entries) == 1

        content = log_entries[0].read_text(encoding="utf-8")
        record = json.loads(content)

        assert record["event"] == "发生异常"
        assert record["level"] == "error"
        # 应包含 exception 字段，而非只有 exc_info: true
        assert "exception" in record
        assert isinstance(record["exception"], str)
        assert "ValueError" in record["exception"]
        assert "测试异常" in record["exception"]
        assert "Traceback" in record["exception"] or "traceback" in record["exception"].lower()

    def test_exc_info_true_contains_traceback(self, tmp_path: Path) -> None:
        """logger.debug(..., exc_info=True) 也应包含 traceback。"""
        _clear_structlog_context()
        log_base = tmp_path / ".minicode"
        setup_logging(debug=True, log_base_dir=log_base)
        logger = get_logger("test")
        try:
            raise RuntimeError("运行时错误")
        except RuntimeError:
            logger.debug("调式异常", exc_info=True)

        log_entries = list((log_base / "logs").iterdir())
        assert len(log_entries) == 1

        content = log_entries[0].read_text(encoding="utf-8")
        record = json.loads(content)

        assert "exception" in record
        assert "RuntimeError" in record["exception"]
        assert "运行时错误" in record["exception"]
