"""任务规划器。

规划器负责在 ReAct 执行前生成结构化计划；它不执行工具，失败回滚由
AgentLoop 统一负责，便于保持会话历史一致。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from typing import Any, cast

from minicode.agent.context import build_messages
from minicode.agent.context_models import ContextConfig
from minicode.agent.planning_models import ExecutionPlan, PlanningConfig, PlanStep
from minicode.providers.base import BaseProvider, Message, StreamChunk
from minicode.utils.exceptions import ProviderError

PLANNING_SYSTEM_PROMPT = (
    "你是 MiniCode 的任务规划器。"
    "请先理解用户任务，然后制定一份简洁、可执行的中文计划。"
    """

输出要求：
1. 只输出 JSON，不要输出 Markdown，不要包裹代码块。
2. JSON 结构必须是 {"goal": "...", "steps": [{"title": "...", "description": "..."}]}。
3. steps 数量不要超过配置要求。
4. 计划应面向实际执行，包含阅读、修改、验证等必要动作。
5. 不要调用工具；这里只制定计划，后续执行阶段会使用工具。
"""
)


def _extract_json_object(text: str) -> str | None:
    """从模型文本中提取第一个 JSON 对象，兼容前后夹带解释文本的输出。"""
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def _coerce_step(raw_step: object, index: int) -> PlanStep | None:
    """把模型返回的步骤对象转换为 PlanStep。"""
    if isinstance(raw_step, dict):
        title = str(raw_step.get("title") or "").strip()
        description = str(raw_step.get("description") or "").strip()
    else:
        title = str(raw_step).strip()
        description = ""

    if not title:
        return None
    return PlanStep(index=index, title=title, description=description)


def _fallback_plan(text: str, fallback_goal: str, max_steps: int) -> ExecutionPlan:
    """模型未给出合法 JSON 时，从非空文本行生成兜底计划。"""
    limit = max(1, max_steps)
    lines = [line.strip(" \t-0123456789.、)") for line in text.splitlines()]
    titles = [line for line in lines if line][:limit]
    if not titles:
        titles = [
            "理解任务目标并检查相关上下文",
            "执行必要修改并验证结果",
        ]

    steps = [
        PlanStep(index=index, title=title)
        for index, title in enumerate(titles, start=1)
    ]
    return ExecutionPlan(
        goal=fallback_goal.strip() or "完成用户任务",
        steps=steps,
        source="text_fallback",
    )


def parse_execution_plan(text: str, fallback_goal: str, max_steps: int) -> ExecutionPlan:
    """把模型输出解析为执行计划，失败时降级到文本行计划。"""
    limit = max(1, max_steps)
    json_text = _extract_json_object(text)
    if json_text is None:
        return _fallback_plan(text, fallback_goal, limit)

    try:
        payload: Any = json.loads(json_text)
    except json.JSONDecodeError:
        return _fallback_plan(text, fallback_goal, limit)

    if not isinstance(payload, dict):
        return _fallback_plan(text, fallback_goal, limit)

    goal = str(payload.get("goal") or fallback_goal or "完成用户任务").strip()
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        return _fallback_plan(text, fallback_goal, limit)

    steps: list[PlanStep] = []
    for raw_step in raw_steps:
        step = _coerce_step(raw_step, len(steps) + 1)
        if step is not None:
            steps.append(step)
        if len(steps) >= limit:
            break

    if not steps:
        return _fallback_plan(text, fallback_goal, limit)

    return ExecutionPlan(goal=goal, steps=steps, source="model")


async def _collect_text(stream: AsyncIterator[StreamChunk]) -> str:
    """收集规划阶段的文本输出，遇到 Provider 错误立即失败。"""
    text_parts: list[str] = []
    async for chunk in stream:
        if chunk.type == "text_delta" and chunk.text:
            text_parts.append(chunk.text)
        elif chunk.type == "error":
            raise ProviderError(chunk.text or "规划阶段模型响应出错。")
        elif chunk.type == "done":
            break
    return "".join(text_parts)


class TaskPlanner:
    """基于当前 Provider 生成任务执行计划。"""

    def __init__(
        self,
        provider: BaseProvider,
        planning_config: PlanningConfig,
        context_config: ContextConfig,
        stream: bool,
    ) -> None:
        self.provider = provider
        self.planning_config = planning_config
        self.context_config = context_config
        self.stream = stream

    async def create_plan(
        self,
        messages: list[Message],
        user_input: str,
        max_tokens: int | None,
    ) -> ExecutionPlan:
        """调用模型生成执行计划。"""
        context_result = build_messages(
            messages=messages,
            system_prompt=PLANNING_SYSTEM_PROMPT,
            context_config=self.context_config,
        )
        planning_tokens = self.planning_config.max_tokens
        if max_tokens is not None:
            planning_tokens = min(planning_tokens, max_tokens)

        # 规划阶段必须禁用工具，避免模型在尚未制定计划时产生副作用。
        stream = self.provider.chat(
            messages=context_result.messages,
            tools=None,
            stream=self.stream,
            max_tokens=planning_tokens,
        )
        if inspect.iscoroutine(stream):
            stream = await stream

        text = await _collect_text(cast(AsyncIterator[StreamChunk], stream))
        return parse_execution_plan(
            text,
            fallback_goal=user_input,
            max_steps=self.planning_config.max_steps,
        )
