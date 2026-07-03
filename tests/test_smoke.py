"""Smoke tests for the MiniCode CLI, package imports, and config integration."""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import minicode
from minicode.main import app


def _write_yaml(path: Path, data: dict) -> None:
    """将字典写入 YAML 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


@pytest.fixture
def fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """将 Path.home() 指向临时目录，避免影响真实用户配置。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


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


class TestCliNoSubcommand:
    """验证无子命令时 CLI 能正常解析参数。"""

    def test_no_args_no_api_key(self, fake_home: Path) -> None:
        """验证无 API key 时 CLI 非零退出并输出清晰错误。"""
        result = CliRunner().invoke(app, [])
        assert result.exit_code != 0
        assert "未配置 API key" in result.output
        assert "Missing command" not in result.output

    def test_no_args_with_api_key(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证有 API key 时 CLI 正常退出。"""
        monkeypatch.setenv("MINICODE_OPENAI_API_KEY", "sk-test")
        result = CliRunner().invoke(app, [])
        assert result.exit_code == 0

    def test_model_only_with_api_key(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证 --model 参数在有 API key 时正常解析。"""
        monkeypatch.setenv("MINICODE_OPENAI_API_KEY", "sk-test")
        result = CliRunner().invoke(app, ["--model", "gpt-4o"])
        assert result.exit_code == 0
        assert "Missing command" not in result.output

    def test_multi_args_with_api_key(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证多参数组合在有 API key 时正常解析。"""
        monkeypatch.setenv("MINICODE_OPENAI_API_KEY", "sk-test")
        result = CliRunner().invoke(app, [
            "--provider", "openai",
            "--workspace", ".",
            "--debug",
        ])
        assert result.exit_code == 0
        assert "Missing command" not in result.output

    def test_config_flag(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证 --config 能加载指定配置文件。"""
        explicit = fake_home / "my-config.yaml"
        _write_yaml(
            explicit,
            {
                "providers": {
                    "openai": {
                        "api_key": "sk-from-config-flag",
                        "base_url": "https://api.openai.com/v1",
                        "models": ["gpt-4o"],
                    },
                },
            },
        )
        result = CliRunner().invoke(app, ["--config", str(explicit)])
        assert result.exit_code == 0
        assert "配置错误" not in result.output

    def test_config_flag_not_found(self, fake_home: Path) -> None:
        """验证 --config 指向不存在的文件时抛出错误。"""
        result = CliRunner().invoke(
            app, ["--config", str(fake_home / "nonexistent.yaml")]
        )
        assert result.exit_code != 0
        assert "不存在" in result.output

    def test_config_error_message(self, fake_home: Path) -> None:
        """验证配置错误输出不含长 traceback。"""
        result = CliRunner().invoke(app, [])
        assert result.exit_code != 0
        # 配置错误应为简洁的中文信息，不含 Traceback
        assert "Traceback" not in result.output
