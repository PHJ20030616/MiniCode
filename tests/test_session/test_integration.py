"""Session 集成测试：完整生命周期与 Agent Loop 保存恢复。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from minicode.agent.loop import AgentLoop
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig
from minicode.providers.registry import MockProvider
from minicode.session import SessionManager
from minicode.tools.registry import ToolRegistry


@pytest.fixture
def app_config() -> AppConfig:
    """测试用的最小化 AppConfig。"""
    return AppConfig(
        default_provider="mock",
        default_model="mock-model",
        agent=AgentConfig(max_rounds=5, stream=False),
        permissions=PermissionsConfig(trust_mode=True),
    )


@pytest.fixture
def tool_registry() -> ToolRegistry:
    """空的 ToolRegistry，避免工具注册的副作用。"""
    return ToolRegistry()


@pytest.fixture
def mock_renderer() -> MagicMock:
    """Mock 的 StreamingRenderer，避免 Rich 终端依赖。"""
    renderer = MagicMock()
    renderer.console = MagicMock()
    # AgentLoop 调用 renderer.console.status(...) 返回一个 context manager
    renderer.console.status.return_value.__enter__ = MagicMock()
    renderer.console.status.return_value.__exit__ = MagicMock()
    return renderer


class TestFullLifecycle:
    """会话完整生命周期集成测试。"""

    def test_create_save_list_load_delete(self, tmp_path: Path) -> None:
        """create → save → list → load → delete 完整生命周期。"""
        manager = SessionManager(tmp_path)

        # 1. create
        session = manager.create(model="test-model", provider="test-provider")
        assert session.id
        assert session.model == "test-model"

        # 2. save
        manager.save(session)
        session_file = tmp_path / ".minicode" / "sessions" / f"{session.id}.json"
        assert session_file.exists()

        # 3. list
        sessions = manager.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["id"] == session.id

        # 4. load
        loaded = manager.load(session.id)
        assert loaded is not None
        assert loaded.id == session.id
        assert loaded.model == "test-model"

        # 5. delete
        deleted = manager.delete(session.id)
        assert deleted is True
        assert not session_file.exists()
        assert manager.list_sessions() == []


class TestAgentLoopSaveAndResume:
    """Agent Loop 完成后保存，新建 Loop 加载并继续。"""

    async def test_save_and_resume(
        self,
        tmp_path: Path,
        app_config: AppConfig,
        tool_registry: ToolRegistry,
        mock_renderer: MagicMock,
    ) -> None:
        """Agent Loop 完成后保存会话，新建 Agent Loop 加载会话并继续一轮。"""
        # 1. 创建 SessionManager
        manager = SessionManager(tmp_path)

        # 2. 创建第一个 AgentLoop（MockProvider 预设回复）
        provider1 = MockProvider(response_text="Hello")
        loop1 = AgentLoop(
            provider=provider1,
            tool_registry=tool_registry,
            config=app_config,
            renderer=mock_renderer,
        )

        # 3. 运行第一轮
        result1 = await loop1.run("Hi")
        assert result1 == "Hello"
        assert len(loop1.messages) == 3  # user + plan + assistant
        assert "### 执行计划" in str(loop1.messages[1].content)

        # 4. 保存会话
        session = manager.create(model="mock-model", provider="mock", workspace_root=str(tmp_path))
        session.messages = list(loop1.messages)
        manager.save(session)
        saved_id = session.id

        # 5. 创建第二个 AgentLoop（新 MockProvider）
        provider2 = MockProvider(response_text="World")
        loop2 = AgentLoop(
            provider=provider2,
            tool_registry=tool_registry,
            config=app_config,
            renderer=mock_renderer,
        )

        # 6. 加载之前的会话，恢复 messages
        loaded = manager.load(saved_id)
        assert loaded is not None
        loop2.messages = list(loaded.messages)
        assert len(loop2.messages) == 3
        assert "### 执行计划" in str(loop2.messages[1].content)

        # 7. 继续一轮
        result2 = await loop2.run("Continue")
        assert result2 == "World"

        # 8. 验证消息数量：3 条旧 + 3 条新 = 6 条
        assert len(loop2.messages) == 6
        assert loop2.messages[0].role == "user"
        assert loop2.messages[0].content == "Hi"
        assert loop2.messages[3].role == "user"
        assert loop2.messages[3].content == "Continue"
        assert loop2.messages[4].role == "assistant"
        assert "### 执行计划" in str(loop2.messages[4].content)
        assert loop2.messages[5].role == "assistant"
        assert loop2.messages[5].content == "World"
