# Plan-Before-Execute Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MiniCode 增加可配置的“先制定计划，再执行计划”能力，让普通任务在进入 ReAct 工具执行前先生成、展示并保存一份执行计划。

**Architecture:** 在 `agent` 层新增轻量规划器，由同一个 Provider 先进行一次无工具规划调用，解析为结构化 `ExecutionPlan` 后追加到会话历史，再进入现有 ReAct 循环执行。配置放在 `AppConfig.agent.planning` 下，默认关闭以保持现有行为，通过 YAML 或环境变量开启自动规划。

**Tech Stack:** Python 3.12, Pydantic v2, pytest, pytest-asyncio, Rich Markdown, 当前 `BaseProvider` / `AgentLoop` / `ContextConfig` 架构。

---

## 1. 需求边界

### 包含

| 能力 | 行为 |
| --- | --- |
| 规划配置 | 新增 `agent.planning.enabled/max_steps/max_tokens`，支持配置文件与环境变量 |
| 任务规划 | 普通用户消息进入 ReAct 前，先调用模型生成中文执行计划 |
| 计划展示 | 计划生成后用 Markdown 在终端展示，面向用户文案保持中文 |
| 计划入历史 | 将计划作为 assistant 消息追加到 `AgentLoop.messages`，执行阶段可看到计划 |
| 执行复用 | 继续使用现有工具、权限、上下文裁剪、记忆系统和自动保存 |
| 测试覆盖 | 覆盖配置、规划解析、规划调用、现有行为兼容、失败回滚 |

### 不包含

| 暂不做 | 原因 |
| --- | --- |
| 人工确认计划后再执行 | 当前需求是“先制定计划，再执行计划”，没有要求暂停等待用户确认 |
| 持久化独立计划文件 | 会话历史已经保存计划文本，独立计划文件会引入新的生命周期管理 |
| 复杂任务识别分类器 | MVP 先由配置决定是否规划，避免额外模型调用和不可预测分支 |
| 计划步骤状态实时更新 | 当前 ReAct loop 没有任务状态事件流，后续可在工具执行结果基础上扩展 |

## 2. 文件改动清单

### 新增文件

| 文件 | 职责 |
| --- | --- |
| `src/minicode/agent/planning_models.py` | 规划配置、步骤、执行计划的数据模型 |
| `src/minicode/agent/planner.py` | 构建规划提示词、调用 Provider、解析模型输出为 `ExecutionPlan` |
| `tests/test_agent/test_planner.py` | 规划器解析、Provider 调用、错误处理测试 |
| `tests/test_config/test_planning_config.py` | 规划配置默认值、自定义值、环境变量映射测试 |

### 修改文件

| 文件 | 改动 |
| --- | --- |
| `src/minicode/config/models.py` | `AgentConfig` 增加 `planning: PlanningConfig` |
| `src/minicode/config/loader.py` | 默认配置与 `MINICODE_PLANNING_*` 环境变量映射 |
| `src/minicode/agent/loop.py` | 在 `run()` 中接入规划阶段，抽出可复用 ReAct 执行循环 |
| `tests/test_agent/test_loop.py` | 增加规划启用/禁用、计划入历史、规划失败回滚测试 |
| `README.md` | 增加规划模式配置示例与行为说明 |

## 3. 设计细节

### 3.1 默认行为

`agent.planning.enabled` 默认 `False`。这样现有 `AgentLoop.run()` 调用路径、Provider 调用次数和测试预期保持不变。用户开启后，每条普通用户消息都会先规划再执行。

配置示例：

```yaml
agent:
  planning:
    enabled: true
    max_steps: 8
    max_tokens: 2048
```

环境变量：

```powershell
$env:MINICODE_PLANNING_ENABLED="true"
$env:MINICODE_PLANNING_MAX_STEPS="8"
$env:MINICODE_PLANNING_MAX_TOKENS="2048"
```

### 3.2 规划输出格式

规划器要求模型优先输出 JSON：

```json
{
  "goal": "修复文件读取命令在空文件上的提示",
  "steps": [
    {
      "title": "阅读现有文件读取工具",
      "description": "查看 ReadFile 的空文件处理逻辑和测试覆盖。"
    },
    {
      "title": "补充失败测试",
      "description": "新增空文件提示的断言，先验证当前行为。"
    }
  ]
}
```

解析成功后转换为中文 Markdown：

```markdown
### 执行计划

目标：修复文件读取命令在空文件上的提示

1. 阅读现有文件读取工具
   查看 ReadFile 的空文件处理逻辑和测试覆盖。
2. 补充失败测试
   新增空文件提示的断言，先验证当前行为。
```

如果模型没有输出合法 JSON，解析器从非空文本行中提取步骤；仍然形成 `ExecutionPlan(source="text_fallback")`。这样规划阶段不会因为格式小错误直接中断任务。

### 3.3 会话历史形态

规划启用后，一轮任务的最小历史形态为：

```python
[
    Message(role="user", content="请修复 README 中的命令说明"),
    Message(role="assistant", content="### 执行计划\n\n目标：...\n\n1. ..."),
    Message(role="assistant", content="已完成修复。"),
]
```

如果执行阶段调用工具，现有 `assistant(tool_calls)` 与 `tool` 消息继续按原逻辑追加。上下文裁剪无需为计划新增特殊规则，计划只是普通 assistant 消息。

## 4. 实施任务

### Task 1: 新增规划数据模型

**Files:**
- Create: `src/minicode/agent/planning_models.py`
- Modify: `src/minicode/config/models.py`
- Test: `tests/test_config/test_planning_config.py`

- [ ] **Step 1: 编写配置与计划模型测试**

创建 `tests/test_config/test_planning_config.py`：

```python
"""规划模式配置测试。"""

from pydantic import ValidationError

from minicode.agent.planning_models import ExecutionPlan, PlanStep, PlanningConfig
from minicode.config.models import AgentConfig, AppConfig


def test_planning_config_default_values() -> None:
    """默认关闭规划模式，避免改变现有 ReAct 行为。"""
    cfg = PlanningConfig()

    assert cfg.enabled is False
    assert cfg.max_steps == 8
    assert cfg.max_tokens == 2048


def test_planning_config_custom_values() -> None:
    """允许用户通过配置控制规划开关和预算。"""
    cfg = PlanningConfig(enabled=True, max_steps=5, max_tokens=1024)

    assert cfg.enabled is True
    assert cfg.max_steps == 5
    assert cfg.max_tokens == 1024


def test_planning_config_rejects_invalid_step_count() -> None:
    """步骤数量至少为 1，避免生成空计划。"""
    try:
        PlanningConfig(max_steps=0)
    except ValidationError as exc:
        assert "max_steps" in str(exc)
    else:
        raise AssertionError("max_steps=0 应触发校验错误")


def test_agent_config_contains_planning_config() -> None:
    """AgentConfig 应包含规划配置。"""
    cfg = AgentConfig()

    assert isinstance(cfg.planning, PlanningConfig)
    assert cfg.planning.enabled is False


def test_app_config_accepts_custom_planning_config() -> None:
    """AppConfig 支持嵌套传入规划配置。"""
    cfg = AppConfig(agent=AgentConfig(planning=PlanningConfig(enabled=True)))

    assert cfg.agent.planning.enabled is True


def test_execution_plan_to_markdown() -> None:
    """执行计划可以稳定渲染为中文 Markdown。"""
    plan = ExecutionPlan(
        goal="修复配置加载",
        steps=[
            PlanStep(index=1, title="阅读配置模型", description="确认默认值。"),
            PlanStep(index=2, title="补充测试", description="覆盖环境变量。"),
        ],
    )

    markdown = plan.to_markdown()

    assert "### 执行计划" in markdown
    assert "目标：修复配置加载" in markdown
    assert "1. 阅读配置模型" in markdown
    assert "确认默认值。" in markdown
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
uv run pytest tests/test_config/test_planning_config.py -v
```

Expected: FAIL，错误包含 `No module named 'minicode.agent.planning_models'`。

- [ ] **Step 3: 创建 `planning_models.py`**

写入 `src/minicode/agent/planning_models.py`：

```python
"""任务规划的数据模型。

规划模式在 ReAct 执行前生成一份可读计划，并把计划注入会话历史。
这些模型只描述计划本身，不直接依赖终端渲染或 Provider。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanningConfig(BaseModel):
    """Agent 规划模式配置。"""

    enabled: bool = False
    """是否在普通用户任务执行前先生成计划。默认关闭以保持现有行为。"""
    max_steps: int = Field(default=8, ge=1, le=20)
    """单个计划最多保留的步骤数。"""
    max_tokens: int = Field(default=2048, gt=0)
    """规划阶段允许模型输出的最大 token 数。"""


class PlanStep(BaseModel):
    """执行计划中的单个步骤。"""

    index: int = Field(ge=1)
    """步骤序号，从 1 开始。"""
    title: str = Field(min_length=1)
    """步骤标题，面向用户展示。"""
    description: str = Field(default="")
    """步骤说明，补充说明该步骤的动作和验收点。"""
    status: Literal["pending", "in_progress", "completed", "skipped"] = "pending"
    """步骤状态。MVP 只生成 pending，后续可用于实时状态更新。"""


class ExecutionPlan(BaseModel):
    """一次用户任务的执行计划。"""

    goal: str = Field(min_length=1)
    """计划要达成的目标。"""
    steps: list[PlanStep] = Field(min_length=1)
    """有序步骤列表。"""
    source: Literal["model", "text_fallback"] = "model"
    """计划来源：结构化模型输出或文本兜底解析。"""

    def to_markdown(self) -> str:
        """把结构化计划渲染成用户可读的中文 Markdown。"""
        lines = ["### 执行计划", "", f"目标：{self.goal}", ""]
        for step in self.steps:
            lines.append(f"{step.index}. {step.title}")
            if step.description:
                lines.append(f"   {step.description}")
        return "\n".join(lines)
```

- [ ] **Step 4: 修改 `AgentConfig`**

在 `src/minicode/config/models.py` 增加导入：

```python
from minicode.agent.planning_models import PlanningConfig
```

将 `AgentConfig` 改为：

```python
class AgentConfig(BaseModel):
    """Agent 循环行为配置。"""

    max_rounds: int = 20
    """Agent Loop 最大迭代轮次。"""
    stream: bool = True
    """是否启用流式输出。"""
    context: ContextConfig = Field(default_factory=ContextConfig)
    """上下文窗口管理配置。"""
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    """任务规划配置。"""
```

- [ ] **Step 5: 运行模型测试**

Run:

```powershell
uv run pytest tests/test_config/test_planning_config.py -v
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/minicode/agent/planning_models.py src/minicode/config/models.py tests/test_config/test_planning_config.py
git commit -m "feat: add planning config models"
```

### Task 2: 接入配置加载与环境变量

**Files:**
- Modify: `src/minicode/config/loader.py`
- Test: `tests/test_config/test_planning_config.py`

- [ ] **Step 1: 增加 loader 环境变量测试**

在 `tests/test_config/test_planning_config.py` 追加：

```python
from minicode.config.loader import load


def test_loader_reads_planning_values_from_project_config(tmp_path, monkeypatch) -> None:
    """项目配置文件可以开启规划模式。"""
    monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-test")
    config_dir = tmp_path / ".minicode"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "agent:\n"
        "  planning:\n"
        "    enabled: true\n"
        "    max_steps: 5\n"
        "    max_tokens: 1024\n",
        encoding="utf-8",
    )

    cfg = load(workspace=tmp_path)

    assert cfg.agent.planning.enabled is True
    assert cfg.agent.planning.max_steps == 5
    assert cfg.agent.planning.max_tokens == 1024


def test_loader_reads_planning_values_from_env(tmp_path, monkeypatch) -> None:
    """环境变量可以覆盖规划配置。"""
    monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("MINICODE_PLANNING_ENABLED", "true")
    monkeypatch.setenv("MINICODE_PLANNING_MAX_STEPS", "6")
    monkeypatch.setenv("MINICODE_PLANNING_MAX_TOKENS", "1536")

    cfg = load(workspace=tmp_path)

    assert cfg.agent.planning.enabled is True
    assert cfg.agent.planning.max_steps == 6
    assert cfg.agent.planning.max_tokens == 1536
```

- [ ] **Step 2: 运行新增测试确认失败**

Run:

```powershell
uv run pytest tests/test_config/test_planning_config.py::test_loader_reads_planning_values_from_env -v
```

Expected: FAIL，`cfg.agent.planning.enabled` 仍为 `False`。

- [ ] **Step 3: 修改 `ENV_CONFIG_MAP`**

在 `src/minicode/config/loader.py` 的 `ENV_CONFIG_MAP` 加入：

```python
    "MINICODE_PLANNING_ENABLED": ("agent", "planning", "enabled"),
    "MINICODE_PLANNING_MAX_STEPS": ("agent", "planning", "max_steps"),
    "MINICODE_PLANNING_MAX_TOKENS": ("agent", "planning", "max_tokens"),
```

- [ ] **Step 4: 修改 `_get_defaults()`**

在 `_get_defaults()` 的 `"agent"` 字典下加入：

```python
            "planning": {
                "enabled": False,
                "max_steps": 8,
                "max_tokens": 2048,
            },
```

完整 `agent` 片段应保持为：

```python
        "agent": {
            "max_rounds": 20,
            "stream": True,
            "context": {
                "max_input_tokens": 24000,
                "recent_messages": 16,
                "max_tool_output_chars": 12000,
                "keep_first_user_message": True,
            },
            "planning": {
                "enabled": False,
                "max_steps": 8,
                "max_tokens": 2048,
            },
        },
```

- [ ] **Step 5: 运行配置测试**

Run:

```powershell
uv run pytest tests/test_config/test_planning_config.py -v
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/minicode/config/loader.py tests/test_config/test_planning_config.py
git commit -m "feat: load planning config"
```

### Task 3: 实现任务规划器

**Files:**
- Create: `src/minicode/agent/planner.py`
- Test: `tests/test_agent/test_planner.py`

- [ ] **Step 1: 编写规划器测试**

创建 `tests/test_agent/test_planner.py`：

```python
"""任务规划器测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from minicode.agent.context_models import ContextConfig
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
        ']}'
    )

    plan = parse_execution_plan(text, fallback_goal="修复配置", max_steps=8)

    assert plan.goal == "修复配置"
    assert plan.source == "model"
    assert [step.index for step in plan.steps] == [1, 2]
    assert plan.steps[0].title == "阅读代码"


def test_parse_execution_plan_clamps_steps() -> None:
    """解析时按配置限制最大步骤数。"""
    text = (
        '{"goal":"任务","steps":['
        '{"title":"一"},{"title":"二"},{"title":"三"}'
        ']}'
    )

    plan = parse_execution_plan(text, fallback_goal="任务", max_steps=2)

    assert len(plan.steps) == 2
    assert [step.title for step in plan.steps] == ["一", "二"]


def test_parse_execution_plan_falls_back_to_text_lines() -> None:
    """模型未输出 JSON 时，从文本行兜底生成计划。"""
    text = "先阅读相关文件\n再补充测试\n最后运行验证"

    plan = parse_execution_plan(text, fallback_goal="完成任务", max_steps=8)

    assert plan.source == "text_fallback"
    assert plan.goal == "完成任务"
    assert [step.title for step in plan.steps] == [
        "先阅读相关文件",
        "再补充测试",
        "最后运行验证",
    ]


@pytest.mark.asyncio
async def test_task_planner_calls_provider_without_tools() -> None:
    """规划阶段不允许调用工具，只生成计划文本。"""
    provider = RecordingProvider(
        [
            StreamChunk(
                type="text_delta",
                text='{"goal":"修复测试","steps":[{"title":"阅读失败测试"}]}',
            ),
            StreamChunk(type="done"),
        ]
    )
    planner = TaskPlanner(
        provider=provider,
        planning_config=PlanningConfig(enabled=True, max_steps=8, max_tokens=1024),
        context_config=ContextConfig(),
        stream=True,
    )

    plan = await planner.create_plan(
        messages=[Message(role="user", content="请修复测试")],
        user_input="请修复测试",
        max_tokens=4096,
    )

    assert plan.goal == "修复测试"
    assert provider.calls[0]["tools"] is None
    assert provider.calls[0]["max_tokens"] == 1024
    api_messages = provider.calls[0]["messages"]
    assert isinstance(api_messages, list)
    assert api_messages[0].role == "system"
    assert "只输出 JSON" in str(api_messages[0].content)


@pytest.mark.asyncio
async def test_task_planner_raises_provider_error_on_error_chunk() -> None:
    """规划阶段收到 Provider 错误时向上抛出。"""
    provider = RecordingProvider([StreamChunk(type="error", text="模型不可用")])
    planner = TaskPlanner(
        provider=provider,
        planning_config=PlanningConfig(enabled=True),
        context_config=ContextConfig(),
        stream=True,
    )

    with pytest.raises(ProviderError, match="模型不可用"):
        await planner.create_plan(
            messages=[Message(role="user", content="请修复测试")],
            user_input="请修复测试",
            max_tokens=4096,
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```powershell
uv run pytest tests/test_agent/test_planner.py -v
```

Expected: FAIL，错误包含 `No module named 'minicode.agent.planner'`。

- [ ] **Step 3: 创建 `planner.py`**

写入 `src/minicode/agent/planner.py`：

```python
"""任务规划器。

规划器在 ReAct 工具执行前进行一次无工具模型调用，得到结构化执行计划。
它不修改会话历史，历史追加由 AgentLoop 统一负责，便于失败回滚。
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator
from typing import Any

from minicode.agent.context import build_messages
from minicode.agent.context_models import ContextConfig
from minicode.agent.planning_models import ExecutionPlan, PlanningConfig, PlanStep
from minicode.providers.base import BaseProvider, Message, StreamChunk
from minicode.utils.exceptions import ProviderError

PLANNING_SYSTEM_PROMPT = """你是 MiniCode 的任务规划器。
请先理解用户任务，然后制定一份简洁、可执行的中文计划。

输出要求：
1. 只输出 JSON，不要输出 Markdown，不要包裹代码块。
2. JSON 结构必须是 {"goal": "...", "steps": [{"title": "...", "description": "..."}]}。
3. steps 数量不要超过配置要求。
4. 计划应面向实际执行，包含阅读、修改、验证等必要动作。
5. 不要调用工具；这里只制定计划，后续执行阶段会使用工具。
"""


def _extract_json_object(text: str) -> str | None:
    """从模型文本中提取第一个 JSON 对象，兼容前后带解释文本的输出。"""
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
    """模型未给出合法 JSON 时，从文本行生成兜底计划。"""
    lines = [line.strip(" -0123456789.、\t") for line in text.splitlines()]
    titles = [line for line in lines if line][:max_steps]
    if not titles:
        titles = ["理解任务目标并检查相关上下文", "执行必要修改并验证结果"]

    steps = [
        PlanStep(index=index, title=title)
        for index, title in enumerate(titles, start=1)
    ]
    return ExecutionPlan(goal=fallback_goal.strip() or "完成用户任务", steps=steps, source="text_fallback")


def parse_execution_plan(text: str, fallback_goal: str, max_steps: int) -> ExecutionPlan:
    """把模型输出解析为执行计划，失败时降级到文本行计划。"""
    json_text = _extract_json_object(text)
    if json_text is None:
        return _fallback_plan(text, fallback_goal, max_steps)

    try:
        payload: Any = json.loads(json_text)
    except json.JSONDecodeError:
        return _fallback_plan(text, fallback_goal, max_steps)

    if not isinstance(payload, dict):
        return _fallback_plan(text, fallback_goal, max_steps)

    goal = str(payload.get("goal") or fallback_goal or "完成用户任务").strip()
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        return _fallback_plan(text, fallback_goal, max_steps)

    steps: list[PlanStep] = []
    for raw_step in raw_steps:
        step = _coerce_step(raw_step, len(steps) + 1)
        if step is not None:
            steps.append(step)
        if len(steps) >= max_steps:
            break

    if not steps:
        return _fallback_plan(text, fallback_goal, max_steps)

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

        stream = self.provider.chat(
            messages=context_result.messages,
            tools=None,
            stream=self.stream,
            max_tokens=planning_tokens,
        )
        if inspect.iscoroutine(stream):
            stream = await stream

        text = await _collect_text(stream)
        return parse_execution_plan(
            text,
            fallback_goal=user_input,
            max_steps=self.planning_config.max_steps,
        )
```

- [ ] **Step 4: 运行规划器测试**

Run:

```powershell
uv run pytest tests/test_agent/test_planner.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/minicode/agent/planner.py tests/test_agent/test_planner.py
git commit -m "feat: add task planner"
```

### Task 4: 集成 AgentLoop 的先规划后执行流程

**Files:**
- Modify: `src/minicode/agent/loop.py`
- Test: `tests/test_agent/test_loop.py`

- [ ] **Step 1: 增加 AgentLoop 集成测试**

在 `tests/test_agent/test_loop.py` 追加：

```python
@pytest.mark.asyncio
async def test_run_keeps_single_provider_call_when_planning_disabled(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """默认关闭规划模式，保持现有一次 ReAct 调用行为。"""
    provider = MockStepProvider([
        [
            StreamChunk(type="text_delta", text="完成"),
            StreamChunk(type="done", usage=_make_usage()),
        ]
    ])

    response, loop = await run_agent_loop(
        provider=provider,
        tool_registry=create_default_registry(),
        config=app_config,
        user_input="请回答",
        tmp_path=tmp_path,
    )

    assert response == "完成"
    assert provider.call_count == 1
    assert loop.last_execution_plan is None


@pytest.mark.asyncio
async def test_run_creates_plan_before_react_when_enabled(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """开启规划后，先生成计划，再进入执行阶段。"""
    app_config.agent.planning.enabled = True
    provider = MockStepProvider([
        [
            StreamChunk(
                type="text_delta",
                text='{"goal":"修复问题","steps":[{"title":"阅读代码","description":"定位问题。"}]}',
            ),
            StreamChunk(type="done", usage=_make_usage()),
        ],
        [
            StreamChunk(type="text_delta", text="已按计划完成"),
            StreamChunk(type="done", usage=_make_usage()),
        ],
    ])

    response, loop = await run_agent_loop(
        provider=provider,
        tool_registry=create_default_registry(),
        config=app_config,
        user_input="请修复问题",
        tmp_path=tmp_path,
    )

    assert response == "已按计划完成"
    assert provider.call_count == 2
    assert loop.last_execution_plan is not None
    assert loop.last_execution_plan.goal == "修复问题"
    assert loop.messages[0].role == "user"
    assert loop.messages[1].role == "assistant"
    assert "### 执行计划" in str(loop.messages[1].content)
    assert loop.messages[-1].content == "已按计划完成"


@pytest.mark.asyncio
async def test_run_rolls_back_when_planning_provider_fails(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """规划阶段失败时回滚本轮用户消息，不污染会话历史。"""
    app_config.agent.planning.enabled = True
    provider = MockStepProvider([
        [StreamChunk(type="error", text="规划失败")]
    ])
    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),  # type: ignore[arg-type]
        config=app_config,
        workspace_root=tmp_path,
    )

    response = await loop.run("请修复问题")

    assert response is None
    assert loop.messages == []
    assert loop.last_execution_plan is None
```

- [ ] **Step 2: 运行集成测试确认失败**

Run:

```powershell
uv run pytest tests/test_agent/test_loop.py::test_run_creates_plan_before_react_when_enabled -v
```

Expected: FAIL，`AgentLoop` 没有 `last_execution_plan` 或 Provider 调用次数仍为 1。

- [ ] **Step 3: 修改 `loop.py` 导入与初始化**

在 `src/minicode/agent/loop.py` 增加导入：

```python
from rich.markdown import Markdown

from minicode.agent.planner import TaskPlanner
from minicode.agent.planning_models import ExecutionPlan
```

在 `AgentLoop.__init__()` 中 `last_context_report` 后增加：

```python
        self.last_execution_plan: ExecutionPlan | None = None
```

- [ ] **Step 4: 新增计划创建辅助方法**

在 `AgentLoop` 类中增加：

```python
    async def _create_execution_plan(self, user_input: str) -> ExecutionPlan:
        """生成并展示执行计划。

        规划阶段只读取当前消息历史，不执行工具；计划生成后作为 assistant
        消息进入历史，使后续 ReAct 阶段可以基于计划行动。
        """
        self.renderer.show_info("正在制定执行计划...")
        planner = TaskPlanner(
            provider=self.provider,
            planning_config=self.config.agent.planning,
            context_config=self.config.agent.context,
            stream=self.config.agent.stream,
        )
        plan = await planner.create_plan(
            messages=self.messages,
            user_input=user_input,
            max_tokens=self.config.max_tokens,
        )
        plan_markdown = plan.to_markdown()
        self.renderer.console.print(Markdown(plan_markdown))
        self.messages.append(Message(role="assistant", content=plan_markdown))
        self.last_execution_plan = plan
        return plan
```

- [ ] **Step 5: 抽出 ReAct 执行循环**

把现有 `run()` 中从 `logger.debug("AgentLoop 开始"...` 到循环结束的逻辑移动到新方法：

```python
    async def _run_react_loop(self, history_len: int) -> str | None:
        """运行现有 ReAct 循环。

        Args:
            history_len: 本轮开始前的历史长度，用于 Provider 或流式错误时回滚。
        """
        logger.debug("AgentLoop 开始", max_rounds=self.config.agent.max_rounds)

        for round_num in range(1, self.config.agent.max_rounds + 1):
            ...
```

移动时保留原有循环体、错误处理、工具执行和返回逻辑。原来使用 `history_len` 回滚的代码继续使用参数中的 `history_len`。

- [ ] **Step 6: 改写 `run()` 编排**

将 `AgentLoop.run()` 改为：

```python
    async def run(self, user_input: str, *, force_plan: bool = False) -> str | None:
        """运行 ReAct 循环处理用户输入。

        Args:
            user_input: 用户输入文本。
            force_plan: 是否强制本轮先生成计划，主要供后续命令扩展使用。

        Returns:
            最终回复文本。若过程中出现无法恢复的错误则返回 None。
        """
        history_len = len(self.messages)
        self.messages.append(Message(role="user", content=user_input))
        self.last_execution_plan = None

        try:
            if force_plan or self.config.agent.planning.enabled:
                await self._create_execution_plan(user_input)
        except ProviderError as e:
            logger.debug("规划阶段失败", error=str(e))
            self.renderer.show_error(f"计划生成失败：{e}")
            del self.messages[history_len:]
            self.last_execution_plan = None
            return None

        return await self._run_react_loop(history_len)
```

注意：`_run_react_loop()` 内部仍负责执行阶段 Provider 错误回滚；规划阶段的回滚由 `run()` 负责。

- [ ] **Step 7: 运行 AgentLoop 测试**

Run:

```powershell
uv run pytest tests/test_agent/test_loop.py -v
```

Expected: PASS。

- [ ] **Step 8: Commit**

```bash
git add src/minicode/agent/loop.py tests/test_agent/test_loop.py
git commit -m "feat: plan before react execution"
```

### Task 5: 增加端到端配置与文档

**Files:**
- Modify: `README.md`
- Test: `tests/test_config/test_planning_config.py`

- [ ] **Step 1: 在 README 增加规划模式说明**

在 README 的配置说明区域加入：

````markdown
### 规划模式

默认情况下，MiniCode 会像普通 ReAct 助手一样直接回答或调用工具。你可以开启规划模式，让每个普通任务先生成执行计划，再按计划进入工具执行阶段：

```yaml
agent:
  planning:
    enabled: true
    max_steps: 8
    max_tokens: 2048
```

也可以使用环境变量临时开启：

```bash
MINICODE_PLANNING_ENABLED=true
MINICODE_PLANNING_MAX_STEPS=8
MINICODE_PLANNING_MAX_TOKENS=2048
```

开启后，MiniCode 会先显示“执行计划”，随后继续执行任务。斜杠命令仍按命令逻辑直接执行，不进入规划模式。
````

- [ ] **Step 2: 运行相关测试**

Run:

```powershell
uv run pytest tests/test_config/test_planning_config.py tests/test_agent/test_planner.py tests/test_agent/test_loop.py -v
```

Expected: PASS。

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document planning mode"
```

### Task 6: 全量验证

**Files:**
- No code changes

- [ ] **Step 1: 运行测试套件**

Run:

```powershell
uv run pytest
```

Expected: PASS。

- [ ] **Step 2: 运行 Ruff**

Run:

```powershell
uv run ruff check .
```

Expected: PASS。

- [ ] **Step 3: 运行 Mypy**

Run:

```powershell
uv run mypy src/minicode
```

Expected: PASS。

- [ ] **Step 4: 手动冒烟验证**

准备 `.minicode/config.yaml`：

```yaml
agent:
  planning:
    enabled: true
```

Run:

```powershell
uv run minicode
```

输入：

```text
请读取 README.md 并总结项目用途
```

Expected:
- 终端先显示中文提示 `正在制定执行计划...`
- 随后显示 `### 执行计划`
- 后续进入原有 ReAct 流程，按需要调用 `read_file`
- 会话保存后，计划文本作为 assistant 消息出现在会话历史中

- [ ] **Step 5: Commit 验证收尾**

如果 Task 6 只产生测试缓存或日志，不提交这些文件。若验证中修正文档或测试，按实际改动提交：

```bash
git add <changed-files>
git commit -m "test: verify planning mode"
```

## 5. 风险与回滚策略

| 风险 | 控制方式 |
| --- | --- |
| 规划模式增加一次模型调用，速度变慢 | 默认关闭；开启时用户明确选择更稳的任务流程 |
| 模型不按 JSON 输出 | `parse_execution_plan()` 支持文本行兜底，仍能形成计划 |
| 规划阶段失败污染会话 | `AgentLoop.run()` 在规划异常时删除本轮新增消息 |
| 计划占用上下文预算 | 计划作为普通 assistant 消息参与现有 context builder 裁剪 |
| 现有测试 Provider 响应数量不足 | 默认关闭规划，原测试不需要额外 mock 响应 |

## 6. 验收标准

- 默认配置下，普通对话仍只触发一次 Provider 调用。
- 开启 `agent.planning.enabled=true` 后，普通用户消息触发“规划调用 + 执行调用”。
- 规划调用 `tools=None`，不会在规划阶段执行工具。
- 计划以中文 Markdown 展示，并作为 assistant 消息保存到会话历史。
- Provider 在规划阶段报错时，本轮用户消息和计划消息都不会残留。
- `uv run pytest`、`uv run ruff check .`、`uv run mypy src/minicode` 全部通过。

## 7. 后续扩展方向

- 增加 `/plan <任务>` 一次性强制规划入口，复用 `AgentLoop.run(..., force_plan=True)`。
- 增加计划确认模式：展示计划后等待用户输入确认、编辑或取消。
- 根据工具执行结果更新 `PlanStep.status`，在终端展示步骤进度。
- 为简单问答增加轻量跳过规则，例如纯解释类问题不自动规划。

## 8. Self-Review

- Spec coverage: 已覆盖“面对一个任务时先制定计划，再执行计划”的核心需求，并明确计划展示、入历史、执行复用和配置入口。
- Placeholder scan: 文档未使用占位表达；每个任务都有具体文件、代码片段、命令和预期结果。
- Type consistency: `PlanningConfig`、`PlanStep`、`ExecutionPlan`、`TaskPlanner`、`parse_execution_plan()`、`AgentLoop.last_execution_plan` 在任务间命名一致。
