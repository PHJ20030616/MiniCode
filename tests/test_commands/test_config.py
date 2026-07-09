"""/config 命令单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from minicode.commands.base import CommandContext
from minicode.commands.config_cmd import ConfigCommand
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig


class _FakeRenderer:
    """测试用假渲染器。"""

    def show_info(self, message: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        pass


def _make_config() -> AppConfig:
    """创建一个测试用 AppConfig。"""
    return AppConfig(
        default_provider="deepseek",
        default_model="deepseek-v4-flash",
        max_tokens=16384,
        agent=AgentConfig(max_rounds=20, stream=True),
        permissions=PermissionsConfig(trust_mode=False),
        providers={
            "openai": ProviderConfig(
                api_key="sk-test1234",
                base_url="https://api.openai.com/v1",
                models=["gpt-4o", "gpt-4o-mini"],
            ),
            "deepseek": ProviderConfig(
                api_key="sk-ds5678abcd",
                base_url="https://api.deepseek.com",
                models=["deepseek-v4-flash"],
            ),
        },
    )


class TestConfigCommand:
    """/config 命令测试。"""

    def test_name(self) -> None:
        """验证命令名。"""
        cmd = ConfigCommand()
        assert cmd.name == "config"

    @pytest.mark.asyncio
    async def test_execute_show_default(self) -> None:
        """无参数时默认显示配置。"""
        config = _make_config()
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path.cwd(),
            session_manager=MagicMock(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = ConfigCommand()
        result = await cmd.execute("", ctx)

        assert result.success is True
        msg = result.message or ""
        # 验证关键配置项出现在输出中
        assert "deepseek" in msg
        assert "deepseek-v4-flash" in msg
        assert "16384" in msg

    @pytest.mark.asyncio
    async def test_execute_show_hides_api_key(self) -> None:
        """/config show 应脱敏 API key。"""
        config = _make_config()
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path.cwd(),
            session_manager=MagicMock(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = ConfigCommand()
        result = await cmd.execute("show", ctx)

        msg = result.message or ""
        # API key 完整值不应出现在输出中
        assert "sk-test1234" not in msg
        assert "sk-ds5678abcd" not in msg

    @pytest.mark.asyncio
    async def test_execute_unknown_subcommand(self) -> None:
        """未知子命令应返回提示。"""
        config = _make_config()
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path.cwd(),
            session_manager=MagicMock(),  # type: ignore[arg-type]
            agent_loop=None,
            renderer=_FakeRenderer(),  # type: ignore[arg-type]
            console=Console(file=None),
        )

        cmd = ConfigCommand()
        result = await cmd.execute("unknown", ctx)

        assert result.success is False
        assert "show" in (result.message or "")
