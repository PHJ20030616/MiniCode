"""test_commands 共享 fixtures。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from minicode.commands.base import CommandContext
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig


class _FakeRenderer:
    """测试用假渲染器。"""

    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []

    def show_info(self, message: str) -> None:
        self.info_messages.append(message)

    def show_error(self, message: str) -> None:
        self.error_messages.append(message)


class _FakeAgentLoop:
    """测试用假 AgentLoop。"""

    def __init__(self, messages: list[dict] | None = None) -> None:
        self.messages: list[dict] = messages or []


@pytest.fixture
def app_config() -> AppConfig:
    """创建测试用 AppConfig。"""
    return AppConfig(
        default_provider="deepseek",
        default_model="deepseek-v4-flash",
        max_tokens=16384,
        agent=AgentConfig(max_rounds=20, stream=True),
        permissions=PermissionsConfig(trust_mode=False),
        providers={
            "deepseek": ProviderConfig(
                api_key="sk-test-key",
                base_url="https://api.deepseek.com",
                models=["deepseek-v4-flash"],
            ),
        },
    )


@pytest.fixture
def fake_renderer() -> _FakeRenderer:
    """创建测试用渲染器。"""
    return _FakeRenderer()


@pytest.fixture
def fake_session_manager(tmp_path: Path) -> MagicMock:
    """创建测试用 SessionManager。"""
    mgr = MagicMock()
    mgr.list_sessions.return_value = []
    return mgr


@pytest.fixture
def command_ctx(
    app_config: AppConfig,
    fake_renderer: _FakeRenderer,
    fake_session_manager: MagicMock,
    tmp_path: Path,
) -> CommandContext:
    """构建标准 CommandContext。"""
    return CommandContext(
        app_config=app_config,
        workspace_root=tmp_path,
        session_manager=fake_session_manager,
        agent_loop=_FakeAgentLoop(),
        renderer=fake_renderer,
        console=Console(file=None),
    )
