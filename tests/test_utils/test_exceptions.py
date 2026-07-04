"""MiniCode 异常层级的单元测试。"""

import pytest

from minicode.utils.exceptions import ConfigError, MiniCodeError, ProviderError, ToolError


class TestExceptionHierarchy:
    """验证异常继承层级是否正确。"""

    def test_minicode_error_is_base(self) -> None:
        """MiniCodeError 是基础异常，继承自 Exception。"""
        assert issubclass(MiniCodeError, Exception)

    def test_config_error_inherits_minicode_error(self) -> None:
        """ConfigError 继承自 MiniCodeError。"""
        assert issubclass(ConfigError, MiniCodeError)

    def test_provider_error_inherits_minicode_error(self) -> None:
        """ProviderError 继承自 MiniCodeError。"""
        assert issubclass(ProviderError, MiniCodeError)

    def test_tool_error_inherits_minicode_error(self) -> None:
        """ToolError 继承自 MiniCodeError。"""
        assert issubclass(ToolError, MiniCodeError)

    def test_all_caught_by_minicode_error(self) -> None:
        """所有自定义异常都能被 MiniCodeError 捕获。"""
        errors: list[MiniCodeError] = [
            ConfigError("配置错误"),
            ProviderError("提供商错误"),
            ToolError("工具错误"),
        ]
        for error in errors:
            assert isinstance(error, MiniCodeError)

    def test_error_message_preserved(self) -> None:
        """异常消息被正确保留。"""
        message = "测试错误消息"
        error = ConfigError(message)
        assert str(error) == message

    def test_config_error_message(self) -> None:
        """ConfigError 支持带建议的多行消息。"""
        msg = (
            "提供商 'openai' 未配置 API key。\n"
            "请通过环境变量设置：MINICODE_OPENAI_API_KEY"
        )
        error = ConfigError(msg)
        assert str(error) == msg

    def test_provider_error_message(self) -> None:
        """ProviderError 包含提供商名称。"""
        error = ProviderError("提供商 'deepseek' 返回 401 认证失败")
        assert "deepseek" in str(error)
        assert "401" in str(error)

    def test_tool_error_message(self) -> None:
        """ToolError 包含工具名称和原因。"""
        error = ToolError("工具 'read_file' 执行失败：文件不存在")
        assert "read_file" in str(error)

    def test_catch_orders(self) -> None:
        """验证可以先捕获子类再捕获父类。这是异常处理的基础模式。"""
        try:
            raise ConfigError("测试")
        except ConfigError:
            pass  # 优先捕获具体异常
        except MiniCodeError:
            pytest.fail("不应走到父类 catch")
