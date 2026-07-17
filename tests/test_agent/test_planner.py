"""任务规划器测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from minicode.agent.planner import TaskPlanner, parse_execution_plan
from minicode.agent.planning_models import PlanningConfig
from minicode.providers.base import BaseProvider, Message, StreamChunk
from minicode.utils.exceptions import ProviderError


class RecordingProvider(BaseProvider):
    """记录规划调用参数的测试 Provider。"""

    def __init__(self, chunks: list[StreamChunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "recording"

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append(
            {
                "messages": messages,
                "tools": tools,
                "stream": stream,
                "max_tokens": max_tokens,
            }
        )
        for chunk in self.chunks:
            yield chunk

    async def list_models(self) -> list[str]:
        return ["recording-model"]


def test_parse_execution_plan_from_json() -> None:
    """解析模型输出的 JSON 计划。"""
    text = (
        '{"goal":"修复配置","steps":['
        '{"title":"阅读代码","description":"定位配置加载。"},'
        '{"title":"补充测试","description":"覆盖环境变量。"}'
        "]}"
    )

    plan = parse_execution_plan(text, fallback_goal="修复配置", max_steps=8)

    assert plan.goal == "修复配置"
    assert plan.source == "model"
    assert [step.index for step in plan.steps] == [1, 2]
    assert plan.steps[0].title == "阅读代码"
    assert plan.steps[0].description == "定位配置加载。"


def test_parse_execution_plan_clamps_steps() -> None:
    """解析时按配置限制最大步骤数。"""
    text = '{"goal":"任务","steps":[{"title":"一"},{"title":"二"},{"title":"三"}]}'

    plan = parse_execution_plan(text, fallback_goal="任务", max_steps=2)

    assert len(plan.steps) == 2
    assert [step.title for step in plan.steps] == ["一", "二"]


def test_parse_execution_plan_falls_back_to_text_lines() -> None:
    """模型未输出 JSON 时，从文本行兜底生成计划。"""
    text = "1. 先阅读相关文件\n- 再补充测试\n最后运行验证"

    plan = parse_execution_plan(text, fallback_goal="完成任务", max_steps=8)

    assert plan.source == "text_fallback"
    assert plan.goal == "完成任务"
    assert [step.title for step in plan.steps] == [
        "先阅读相关文件",
        "再补充测试",
        "最后运行验证",
    ]


@pytest.mark.asyncio
async def test_task_planner_forwards_prebuilt_messages_without_tools() -> None:
    """规划器原样转发预构建消息，并禁用工具。"""
    provider = RecordingProvider(
        [
            StreamChunk(
                type="text_delta",
                text='{"goal":"修复问题","steps":[{"title":"阅读代码"}]}',
            ),
            StreamChunk(type="done"),
        ]
    )
    planner = TaskPlanner(
        provider=provider,
        planning_config=PlanningConfig(enabled=True, max_steps=4, max_tokens=1024),
        stream=True,
    )
    api_messages = [
        Message(role="system", content="预构建规划提示词"),
        Message(role="user", content="请修复问题"),
    ]

    plan = await planner.create_plan(
        api_messages=api_messages,
        user_input="请修复问题",
        max_tokens=512,
    )

    assert plan.goal == "修复问题"
    assert plan.steps[0].title == "阅读代码"
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["tools"] is None
    assert call["stream"] is True
    assert call["max_tokens"] == 512
    assert call["messages"] is api_messages


@pytest.mark.asyncio
async def test_task_planner_raises_provider_error_from_stream() -> None:
    """规划阶段收到 Provider 错误时向上抛出，交由 AgentLoop 回滚。"""
    provider = RecordingProvider([StreamChunk(type="error", text="规划失败")])
    planner = TaskPlanner(
        provider=provider,
        planning_config=PlanningConfig(enabled=True),
        stream=True,
    )

    with pytest.raises(ProviderError, match="规划失败"):
        await planner.create_plan(
            api_messages=[
                Message(role="system", content="预构建规划提示词"),
                Message(role="user", content="请修复问题"),
            ],
            user_input="请修复问题",
            max_tokens=None,
        )
