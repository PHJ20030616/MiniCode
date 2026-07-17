"""Session 集成测试：完整生命周期与 Agent Loop 保存恢复。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from minicode.agent.context_models import CompactionConfig, ContextConfig
from minicode.agent.loop import AgentLoop
from minicode.agent.planning_models import PlanningConfig
from minicode.cli.app import ChatApp
from minicode.commands.compact_cmd import CompactCommand
from minicode.config.models import (
    AgentConfig,
    AppConfig,
    MemoryConfig,
    PermissionsConfig,
)
from minicode.providers.base import BaseProvider, Message, StreamChunk
from minicode.providers.registry import MockProvider
from minicode.session import SessionManager
from minicode.tools.registry import ToolRegistry
from minicode.utils.exceptions import ProviderError


def text_chunks(text: str) -> list[StreamChunk]:
    """构造一组完整的纯文本 Provider 响应。"""
    return [
        StreamChunk(type="text_delta", text=text),
        StreamChunk(type="done"),
    ]


class RecordingStepProvider(BaseProvider):
    """逐次返回预置响应，并记录每次模型调用的独立快照。"""

    def __init__(self, responses: list[list[StreamChunk]]) -> None:
        self._responses = [
            [chunk.model_copy(deep=True) for chunk in response]
            for response in responses
        ]
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "recording-step"

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append(
            {
                "messages": [
                    message.model_copy(deep=True) for message in messages
                ],
                "tools": tools,
                "stream": stream,
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise AssertionError("没有预置可用的 Provider 响应")
        response = self._responses.pop(0)
        for chunk in response:
            yield chunk.model_copy(deep=True)

    async def list_models(self) -> list[str]:
        return ["recording-model"]


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


async def test_compacted_session_round_trip_can_continue(
    tmp_path: Path,
    tool_registry: ToolRegistry,
    mock_renderer: MagicMock,
) -> None:
    """手动压缩持久化后，新应用恢复该会话仍可继续调用主模型。"""
    config = AppConfig(
        default_provider="recording-step",
        default_model="recording-model",
        max_tokens=512,
        agent=AgentConfig(
            max_rounds=3,
            stream=False,
            planning=PlanningConfig(enabled=False),
            context=ContextConfig(
                max_input_tokens=2000,
                compaction=CompactionConfig(
                    auto_enabled=False,
                    trigger_ratio=0.9,
                    target_ratio=0.6,
                    summary_max_tokens=64,
                ),
            ),
        ),
        permissions=PermissionsConfig(trust_mode=True),
        memory=MemoryConfig(enabled=False),
    )
    provider1 = RecordingStepProvider(
        [text_chunks("## 当前任务与最终目标\n保留压缩后的关键事实。")]
    )
    app1 = ChatApp(config, workspace_root=tmp_path)
    loop1 = AgentLoop(
        provider=provider1,
        tool_registry=tool_registry,
        config=config,
        renderer=mock_renderer,
        workspace_root=tmp_path,
    )
    app1._agent_loop = loop1
    loop1.messages = [
        Message(role="user", content="旧任务背景：" + "甲" * 20_000),
        Message(role="assistant", content="已完成旧任务分析。"),
        Message(role="user", content="请保留最新约束。"),
        Message(role="assistant", content="已记录最新约束。"),
    ]

    with patch(
        "minicode.cli.app.CommandRegistry.find",
        return_value=CompactCommand(),
    ):
        should_exit = await app1._handle_command("/compact")

    assert should_exit is False
    assert len(provider1.calls) == 1
    summary_call = provider1.calls[0]
    assert summary_call["tools"] is None
    assert summary_call["stream"] is False
    assert app1._current_session is not None

    session_id = app1._current_session.id
    persisted = SessionManager(tmp_path).load(session_id)
    assert persisted is not None
    persisted_summaries = [
        message
        for message in persisted.messages
        if message.kind == "compact_summary"
    ]
    assert len(persisted_summaries) == 1

    provider2 = RecordingStepProvider([text_chunks("可以继续。")])
    app2 = ChatApp(config, workspace_root=tmp_path)
    loop2 = AgentLoop(
        provider=provider2,
        tool_registry=ToolRegistry(),
        config=config,
        renderer=mock_renderer,
        workspace_root=tmp_path,
    )
    app2._agent_loop = loop2

    assert await app2.switch_session(session_id) is True
    assert sum(
        message.kind == "compact_summary"
        for message in loop2.messages
    ) == 1

    result = await loop2.run("继续")

    assert result == "可以继续。"
    assert len(provider2.calls) == 1
    main_call_messages = provider2.calls[0]["messages"]
    assert isinstance(main_call_messages, list)
    assert any(
        isinstance(message, Message)
        and message.kind == "compact_summary"
        for message in main_call_messages
    )


async def test_chat_app_does_not_apply_legacy_message_slice_rollback(
    tmp_path: Path,
    app_config: AppConfig,
    tool_registry: ToolRegistry,
    mock_renderer: MagicMock,
) -> None:
    """AgentLoop 负责事务回滚，ChatApp 不得再按旧长度删除消息。"""

    class RejectingDeleteMessages(list[Message]):
        def __delitem__(self, key: object) -> None:
            raise AssertionError("ChatApp 不应执行旧的消息切片回滚")

    app = ChatApp(app_config, workspace_root=tmp_path)
    loop = AgentLoop(
        provider=MockProvider(response_text="不会被调用"),
        tool_registry=tool_registry,
        config=app_config,
        renderer=mock_renderer,
        workspace_root=tmp_path,
    )
    original_messages = RejectingDeleteMessages(
        [Message(role="user", content="既有历史")]
    )
    loop.messages = original_messages

    async def fail_after_agent_transaction(user_input: str) -> str | None:
        raise ProviderError("模拟上层可见错误")

    loop.run = fail_after_agent_transaction  # type: ignore[method-assign]
    app._agent_loop = loop

    await app._handle_message("继续")

    assert loop.messages is original_messages
    assert [message.content for message in loop.messages] == ["既有历史"]
