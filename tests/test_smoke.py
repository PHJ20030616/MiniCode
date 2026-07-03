from typer.testing import CliRunner

import minicode
from minicode.main import app


def test_package_imports() -> None:
    assert minicode.__version__ == "0.1.0"


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "MiniCode 0.1.0" in result.output


def test_cli_help() -> None:
    """验证 --help 显示所有 v0.1 支持的参数。"""
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    # 检查所有 v0.1 必需参数
    assert "--model" in result.output
    assert "--provider" in result.output
    assert "--config" in result.output
    assert "--workspace" in result.output
    assert "--debug" in result.output
    assert "--version" in result.output
