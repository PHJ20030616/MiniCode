"""ReAct Agent Loop 测试。

测试覆盖：
1. 无工具直接回答（纯文本对话）
2. 一次 read_file 后回答（单工具调用）
3. 多工具串行调用
4. 工具错误返回给模型
5. 超过最大轮次截断
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

import minicode.agent.compaction as compaction
from minicode.agent.context_models import (
    CompactionConfig,
    CompactionReport,
    CompactionResult,
    CompactionTrigger,
)
from minicode.agent.loop import AgentLoop
from minicode.agent.planner import PLANNING_SYSTEM_PROMPT
from minicode.agent.planning_models import PlanningConfig
from minicode.cli.confirm import ConfirmerResult
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig
from minicode.permissions.models import PermissionDecision
from minicode.providers.base import (
    BaseProvider,
    FunctionCall,
    Message,
    PartialToolCall,
    StreamChunk,
    ToolCall,
    UsageInfo,
)
from minicode.tools import create_default_registry
from minicode.tools.base import ToolResult
from minicode.tools.registry import ToolRegistry
from minicode.utils.exceptions import (
    ContextCompactionError,
    ContextWindowExceededError,
    ProviderError,
)

# ─── Mock Provider ─────────────────────────────────────────────────


class MockStepProvider(BaseProvider):
    """逐步返回预设响应的 Mock Provider。

    每次调用 chat 时从 responses 列表中返回下一组 StreamChunk。
    用于模拟 ReAct 的多轮交互。
    """

    def __init__(self, responses: list[list[StreamChunk]]) -> None:
        self.responses = responses
        self.call_count = 0
        self.calls: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "mock-step"

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
        if self.call_count >= len(self.responses):
            return
        chunks = self.responses[self.call_count]
        self.call_count += 1
        for chunk in chunks:
            yield chunk

    async def list_models(self) -> list[str]:
        return ["mock-model"]


# ─── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def app_config() -> AppConfig:
    """测试用 AppConfig。"""
    return AppConfig(
        default_provider="mock",
        default_model="mock-model",
        max_tokens=4096,
        agent=AgentConfig(
            max_rounds=8,
            stream=True,
            planning=PlanningConfig(enabled=False),
        ),
        permissions=PermissionsConfig(trust_mode=False),
        providers={
            "mock": ProviderConfig(
                api_key="sk-test",
                base_url="https://api.mock.com/v1",
                models=["mock-model"],
            ),
        },
    )


@pytest.fixture
def tool_registry(tmp_path: Path) -> ToolRegistry:
    """带有内置工具的注册器，指向临时目录。"""
    registry = create_default_registry()
    return registry


def _make_usage(input_t: int = 10, output_t: int = 5) -> UsageInfo:
    return UsageInfo(
        input_tokens=input_t,
        output_tokens=output_t,
        total_tokens=input_t + output_t,
    )


# ─── Helpers ───────────────────────────────────────────────────────


async def run_agent_loop(
    provider: BaseProvider,
    tool_registry: ToolRegistry,
    config: AppConfig,
    user_input: str,
    tmp_path: Path,
) -> tuple[str | None, AgentLoop]:
    """创建 AgentLoop 并运行，返回 (response, loop)。"""
    loop = AgentLoop(
        provider=provider,
        tool_registry=tool_registry,
        renderer=MagicRenderer(),  # type: ignore[arg-type]
        config=config,
        workspace_root=tmp_path,
    )
    response = await loop.run(user_input)
    return response, loop


class MagicRenderer:
    """简化版 Mock Renderer，避免 Rich Live 依赖。"""

    def __init__(self) -> None:
        self.console = MagicConsole()
        self.info_messages: list[str] = []
        self.error_messages: list[str] = []

    def show_info(self, message: str) -> None:
        self.info_messages.append(message)

    def show_error(self, message: str) -> None:
        self.error_messages.append(message)

    def show_usage(self, usage: UsageInfo) -> None:
        pass

    def show_user_input(self, text: str) -> None:
        pass


class MagicConsole:
    """Mock Console，用于测试时替代 Rich Console。"""

    def print(self, *args: Any, **kwargs: Any) -> None:
        pass

    def status(self, *args: Any, **kwargs: Any) -> MagicStatus:
        return MagicStatus()


class MagicStatus:
    """Minimal context manager returned by MagicConsole.status."""

    def __enter__(self) -> MagicStatus:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass


class RecordingStatus:
    """Records status context enter and exit calls."""

    def __init__(self, console: RecordingConsole) -> None:
        self.console = console

    def __enter__(self) -> RecordingStatus:
        self.console.status_enters += 1
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.console.status_exits += 1


class RecordingConsole(Console):
    """Rich Console that records status usage and final print calls."""

    def __init__(self) -> None:
        self.output = StringIO()
        super().__init__(file=self.output, highlight=False)
        self.status_messages: list[str] = []
        self.status_enters = 0
        self.status_exits = 0
        self.printed_args: list[object] = []

    def status(self, status: object, *args: Any, **kwargs: Any) -> RecordingStatus:
        self.status_messages.append(str(status))
        return RecordingStatus(self)

    def print(self, *args: Any, **kwargs: Any) -> None:
        self.printed_args.extend(args)
        super().print(*args, **kwargs)


def _compaction_report(
    *,
    trigger: CompactionTrigger = CompactionTrigger.AUTOMATIC,
) -> CompactionReport:
    """构造自动压缩接入测试使用的稳定报告。"""
    return CompactionReport(
        trigger=trigger,
        created_at=datetime.now(UTC),
        before_tokens=12_345,
        after_tokens=6_789,
        before_message_count=6,
        after_message_count=2,
        summarized_message_count=4,
        cleared_tool_result_count=2,
        unconsumed_tool_result_count=0,
        retry_used=False,
        target_reached=True,
        focus_provided=False,
    )


def _auto_compaction_config() -> CompactionConfig:
    """使用足够低的阈值，稳定触发规划和主调用预检。"""
    return CompactionConfig(
        auto_enabled=True,
        trigger_ratio=0.001,
        target_ratio=0.0005,
        summary_max_tokens=64,
    )


@pytest.mark.parametrize(
    ("trigger", "label"),
    [
        (CompactionTrigger.AUTOMATIC, "自动"),
        (CompactionTrigger.MANUAL, "手动"),
    ],
)
def test_format_compaction_report_uses_chinese_label(
    trigger: CompactionTrigger,
    label: str,
) -> None:
    """压缩提示展示触发方式、词元变化和工具结果清理数。"""
    text = compaction.format_compaction_report(_compaction_report(trigger=trigger))

    assert text == f"上下文{label}压缩：12,345 → 6,789 词元，清理工具结果 2 条。"


@pytest.mark.asyncio
async def test_prepare_main_call_skips_compactor_below_threshold(
    tmp_path: Path,
    app_config: AppConfig,
) -> None:
    """占用率低于阈值时不调用压缩器，并使用严格消息构建。"""

    class FailingCompactor:
        async def compact(self, *args: object, **kwargs: object) -> CompactionResult:
            raise AssertionError("低于阈值时不应调用压缩器")

    config = app_config.model_copy(deep=True)
    config.agent.context.max_input_tokens = 100_000
    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),  # type: ignore[arg-type]
        config=config,
        workspace_root=tmp_path,
    )
    assert isinstance(loop.context_compactor, compaction.ContextCompactor)
    loop.context_compactor = FailingCompactor()  # type: ignore[assignment]
    loop.messages.append(Message(role="user", content="短消息"))

    api_messages = await loop._prepare_main_call(loop.system_prompt, [])

    assert [message.role for message in api_messages] == ["system", "user"]
    assert loop.last_context_report is not None
    assert loop.last_compaction_report is None
    assert loop.compaction_count == 0


@pytest.mark.asyncio
async def test_react_auto_compaction_commits_summary_report_and_count(
    tmp_path: Path,
    app_config: AppConfig,
) -> None:
    """ReAct 预检提交压缩器结果，并记录报告与累计次数。"""

    class SummaryCompactor:
        def __init__(self, report: CompactionReport) -> None:
            self.report = report
            self.calls: list[dict[str, object]] = []

        async def compact(
            self,
            messages: list[Message],
            system_prompt: str,
            tools_schema: list[dict],
            trigger: CompactionTrigger,
            focus: str | None = None,
        ) -> CompactionResult:
            self.calls.append(
                {
                    "system_prompt": system_prompt,
                    "tools_schema": tools_schema,
                    "trigger": trigger,
                }
            )
            return CompactionResult(
                messages=[
                    Message(
                        role="user",
                        kind="compact_summary",
                        content="已压缩的历史摘要",
                    ),
                    messages[-1].model_copy(deep=True),
                ],
                report=self.report,
                changed=True,
            )

    config = app_config.model_copy(deep=True)
    config.agent.context.compaction = _auto_compaction_config()
    provider = MockStepProvider(
        [[
            StreamChunk(type="text_delta", text="压缩后继续执行"),
            StreamChunk(type="done", usage=_make_usage()),
        ]]
    )
    renderer = MagicRenderer()
    report = _compaction_report()
    compactor = SummaryCompactor(report)
    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=renderer,  # type: ignore[arg-type]
        config=config,
        workspace_root=tmp_path,
    )
    loop.context_compactor = compactor  # type: ignore[assignment]

    response = await loop.run("继续任务")

    assert response == "压缩后继续执行"
    assert loop.messages[0].kind == "compact_summary"
    assert loop.messages[0].content == "已压缩的历史摘要"
    assert loop.messages[1].content == "继续任务"
    assert loop.last_compaction_report == report
    assert loop.compaction_count == 1
    assert renderer.info_messages[-1] == compaction.format_compaction_report(report)
    assert compactor.calls[0]["trigger"] == CompactionTrigger.AUTOMATIC
    provider_messages = provider.calls[0]["messages"]
    assert isinstance(provider_messages, list)
    assert provider_messages[1].kind == "compact_summary"


@pytest.mark.asyncio
async def test_planning_preflight_context_error_rolls_back_current_turn(
    tmp_path: Path,
    app_config: AppConfig,
) -> None:
    """规划预检失败时显示中文错误，并回滚本轮用户消息。"""

    class FailingCompactor:
        async def compact(
            self,
            messages: list[Message],
            system_prompt: str,
            tools_schema: list[dict],
            trigger: CompactionTrigger,
            focus: str | None = None,
        ) -> CompactionResult:
            raise ContextCompactionError("摘要生成失败")

    config = app_config.model_copy(deep=True)
    config.agent.planning.enabled = True
    config.agent.context.compaction = _auto_compaction_config()
    provider = MockStepProvider([])
    renderer = MagicRenderer()
    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=renderer,  # type: ignore[arg-type]
        config=config,
        workspace_root=tmp_path,
    )
    loop.messages.append(Message(role="assistant", content="此前回复"))
    previous_messages = list(loop.messages)
    loop.context_compactor = FailingCompactor()  # type: ignore[assignment]

    result = await loop.run("请继续规划")

    assert result is None
    assert loop.messages == previous_messages
    assert loop.last_execution_plan is None
    assert provider.call_count == 0
    assert renderer.error_messages
    assert "规划" in renderer.error_messages[-1]
    assert "上下文" in renderer.error_messages[-1]
    assert "摘要生成失败" in renderer.error_messages[-1]


@pytest.mark.asyncio
async def test_react_preflight_context_error_rolls_back_current_turn(
    tmp_path: Path,
    app_config: AppConfig,
) -> None:
    """ReAct 预检失败时显示中文错误，并回滚本轮用户消息。"""

    class FailingCompactor:
        async def compact(
            self,
            messages: list[Message],
            system_prompt: str,
            tools_schema: list[dict],
            trigger: CompactionTrigger,
            focus: str | None = None,
        ) -> CompactionResult:
            raise ContextWindowExceededError("上下文仍然超出窗口")

    config = app_config.model_copy(deep=True)
    config.agent.context.compaction = _auto_compaction_config()
    provider = MockStepProvider([])
    renderer = MagicRenderer()
    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=renderer,  # type: ignore[arg-type]
        config=config,
        workspace_root=tmp_path,
    )
    loop.messages.append(Message(role="assistant", content="此前回复"))
    previous_messages = list(loop.messages)
    loop.context_compactor = FailingCompactor()  # type: ignore[assignment]

    result = await loop.run("请继续执行")

    assert result is None
    assert loop.messages == previous_messages
    assert provider.call_count == 0
    assert renderer.error_messages
    assert "上下文" in renderer.error_messages[-1]
    assert "上下文仍然超出窗口" in renderer.error_messages[-1]


@pytest.mark.asyncio
async def test_planning_preflight_runs_before_planner_without_tools(
    tmp_path: Path,
    app_config: AppConfig,
) -> None:
    """规划 Provider 调用前先用规划提示词和空工具 schema 执行预检。"""
    events: list[tuple[str, object, object]] = []
    reports = [
        _compaction_report().model_copy(update={"before_tokens": 12_345}),
        _compaction_report().model_copy(update={"before_tokens": 23_456}),
    ]

    class OrderedProvider(MockStepProvider):
        async def chat(
            self,
            messages: list[Message],
            tools: list[dict] | None = None,
            stream: bool = True,
            max_tokens: int | None = None,
        ) -> AsyncIterator[StreamChunk]:
            events.append(("provider", tools, messages))
            async for chunk in super().chat(messages, tools, stream, max_tokens):
                yield chunk

    class OrderedCompactor:
        def __init__(self) -> None:
            self.call_count = 0

        async def compact(
            self,
            messages: list[Message],
            system_prompt: str,
            tools_schema: list[dict],
            trigger: CompactionTrigger,
            focus: str | None = None,
        ) -> CompactionResult:
            events.append(("compact", tools_schema, system_prompt))
            report = reports[self.call_count]
            self.call_count += 1
            return CompactionResult(
                messages=[message.model_copy(deep=True) for message in messages],
                report=report,
                changed=True,
            )

    config = app_config.model_copy(deep=True)
    config.agent.planning.enabled = True
    config.agent.context.compaction = _auto_compaction_config()
    provider = OrderedProvider(
        [
            [
                StreamChunk(
                    type="text_delta",
                    text='{"goal":"完成任务","steps":[{"title":"执行"}]}',
                ),
                StreamChunk(type="done"),
            ],
            [
                StreamChunk(type="text_delta", text="完成"),
                StreamChunk(type="done"),
            ],
        ]
    )
    renderer = MagicRenderer()
    compactor = OrderedCompactor()
    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=renderer,  # type: ignore[arg-type]
        config=config,
        workspace_root=tmp_path,
    )
    loop.context_compactor = compactor  # type: ignore[assignment]

    response = await loop.run("请完成任务")

    assert response == "完成"
    assert [event[0] for event in events] == [
        "compact",
        "provider",
        "compact",
        "provider",
    ]
    assert events[0] == ("compact", [], PLANNING_SYSTEM_PROMPT)
    assert events[1][1] is None
    assert events[2][1] == loop._get_tools_schema()
    assert events[2][2] == loop.system_prompt
    assert events[3][1] == loop._get_tools_schema()
    assert compactor.call_count == 2
    assert loop.compaction_count == 2
    assert loop.last_compaction_report == reports[1]
    assert loop.last_context_report is not None
    assert renderer.info_messages[-2:] == [
        compaction.format_compaction_report(report) for report in reports
    ]


# ─── Test 1: 无工具直接回答 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_direct_text_response(tmp_path: Path, app_config: AppConfig) -> None:
    """模型直接返回文本，不调用工具。"""
    provider = MockStepProvider([
        [
            StreamChunk(type="text_delta", text="你好！我是 MiniCode。"),
            StreamChunk(type="text_delta", text="我可以帮你编程。"),
            StreamChunk(type="done", usage=_make_usage()),
        ],
    ])

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "你好", tmp_path
    )

    assert response is not None
    assert "你好" in response
    assert "MiniCode" in response
    # 对话历史：user + assistant
    assert len(loop.messages) == 2
    assert loop.messages[0].role == "user"
    assert loop.messages[1].role == "assistant"
    assert loop.messages[1].tool_calls is None


@pytest.mark.asyncio
async def test_process_stream_prints_complete_response_once(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """Collect text while thinking, then render the complete response once."""
    provider = MockStepProvider([[
        StreamChunk(type="text_delta", text="Hello "),
        StreamChunk(type="text_delta", text="world"),
        StreamChunk(type="done", usage=_make_usage(4, 2)),
    ]])
    renderer = MagicRenderer()
    console = RecordingConsole()
    renderer.console = console
    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=renderer,  # type: ignore[arg-type]
        config=app_config,
        workspace_root=tmp_path,
    )

    text_content, tool_calls, usage = await loop._process_stream(
        provider.chat([], [], stream=True)
    )

    assert text_content == "Hello world"
    assert tool_calls is None
    assert usage is not None
    assert console.status_enters == 1
    assert console.status_exits == 1
    assert any("正在思考" in message for message in console.status_messages)
    markdown_outputs = [arg for arg in console.printed_args if isinstance(arg, Markdown)]
    assert len(markdown_outputs) == 1
    assert console.output.getvalue().count("Hello world") == 1


# ─── Test 2: 一次 read_file 后回答 ─────────────────────────────────


@pytest.mark.asyncio
async def test_read_file_then_answer(tmp_path: Path, app_config: AppConfig) -> None:
    """模型调用一次 read_file，然后基于结果回答。"""
    # 创建测试文件
    test_file = tmp_path / "README.md"
    test_file.write_text("# MiniCode\n\n一个 AI 编程助手。", encoding="utf-8")

    provider = MockStepProvider([
        # 第一轮：调用 read_file
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_read",
                    index=0,
                    name="read_file",
                    arguments='{"file_path": "README.md"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(15, 5)),
        ],
        # 第二轮：总结文件内容
        [
            StreamChunk(type="text_delta", text="根据 README.md 的内容："),
            StreamChunk(type="text_delta", text="\n\nMiniCode 是一个 AI 编程助手。"),
            StreamChunk(type="done", usage=_make_usage(100, 20)),
        ],
    ])

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "README.md 的内容是什么？", tmp_path
    )

    assert response is not None
    assert "MiniCode" in response
    # 历史：user + assistant(with tool_call) + tool(result) + assistant(text)
    assert len(loop.messages) == 4
    assert loop.messages[1].role == "assistant"
    assert loop.messages[1].tool_calls is not None
    assert len(loop.messages[1].tool_calls) == 1
    assert loop.messages[1].tool_calls[0].function.name == "read_file"
    # 验证 tool result 包含了文件内容
    assert loop.messages[2].role == "tool"
    assert "# MiniCode" in (loop.messages[2].content or "")


# ─── Test 3: 多工具串行调用 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_tools_serial(tmp_path: Path, app_config: AppConfig) -> None:
    """模型连续调用多个工具（glob 查找文件 → read_file 读取内容）。"""
    # 创建测试文件
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hello')", encoding="utf-8")
    (src_dir / "utils.py").write_text("def add(a, b): return a + b", encoding="utf-8")

    provider = MockStepProvider([
        # 第一轮：glob 查找文件
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_glob",
                    index=0,
                    name="glob",
                    arguments='{"pattern": "src/**/*.py"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(20, 10)),
        ],
        # 第二轮：读取找到的第一个文件
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_read",
                    index=0,
                    name="read_file",
                    arguments='{"file_path": "src/main.py"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(50, 10)),
        ],
        # 第三轮：总结
        [
            StreamChunk(type="text_delta", text="src/main.py 的内容是：print('hello')"),
            StreamChunk(type="done", usage=_make_usage(80, 15)),
        ],
    ])

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "查找并读取 Python 文件", tmp_path
    )

    assert response is not None
    assert "hello" in response or "print" in response
    # 验证历史包含多轮工具调用
    # assistant 消息数 = 工具调用轮次(2) + 最终文本回答(1) = 3
    assistant_msgs = [m for m in loop.messages if m.role == "assistant"]
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(assistant_msgs) == 3
    assert len(tool_msgs) == 2       # 两次工具结果
    # 验证 glob 和 read_file 都被调用了
    tool_call_names = []
    for msg in assistant_msgs:
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_call_names.append(tc.function.name)
    assert "glob" in tool_call_names
    assert "read_file" in tool_call_names


# ─── Test 3b: 同一轮多工具调用 ────────────────────────────────────


@pytest.mark.asyncio
async def test_same_round_multi_tool_calls(tmp_path: Path, app_config: AppConfig) -> None:
    """模型在同一轮返回多个 tool_call（不同 index），不应被合并。"""
    # 创建测试文件
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")

    provider = MockStepProvider([
        # 第一轮：同时返回两个工具调用，index 分别为 0 和 1
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_glob", index=0, name="glob",
                    arguments='{"pattern": "*.py"}',
                ),
            ),
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_read", index=1, name="read_file",
                    arguments='{"file_path": "main.py"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(20, 10)),
        ],
        # 第二轮：最终回复
        [
            StreamChunk(type="text_delta", text="已找到并读取文件。"),
            StreamChunk(type="done", usage=_make_usage(60, 10)),
        ],
    ])

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "查找并读取文件", tmp_path
    )

    assert response is not None
    # 验证两个工具都被执行了（两个 ToolMessage）
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 2
    # glob 结果不包含 main.py 路径内容，read_file 结果包含
    read_file_msg = tool_msgs[1]
    assert "hello" in (read_file_msg.content or "")
    # 验证 assistant 消息中有两个 tool_calls（未合并）
    assistant_with_tools = [m for m in loop.messages if m.role == "assistant" and m.tool_calls]
    assert len(assistant_with_tools) == 1
    assert len(assistant_with_tools[0].tool_calls) == 2  # 两个未合并


@pytest.mark.asyncio
async def test_consecutive_subagent_calls_run_concurrently_in_order(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """连续 run_subagent 调用应并行执行，并按原 tool_call 顺序写回。"""

    class RecordingRegistry(ToolRegistry):
        def __init__(self) -> None:
            super().__init__()
            self.started: list[str] = []
            self.release = asyncio.Event()

        async def execute_tool(
            self,
            name: str,
            args: dict,
            workspace_root: Path,
        ) -> ToolResult:
            task_name = str(args["name"])
            self.started.append(task_name)
            await self.release.wait()
            return ToolResult(success=True, output=f"结果：{task_name}")

    registry = RecordingRegistry()
    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=registry,
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )
    calls = [
        ToolCall(
            id="call_a",
            function=FunctionCall(
                name="run_subagent",
                arguments='{"name":"A","task":"任务 A"}',
            ),
        ),
        ToolCall(
            id="call_b",
            function=FunctionCall(
                name="run_subagent",
                arguments='{"name":"B","task":"任务 B"}',
            ),
        ),
    ]

    task = asyncio.create_task(loop._execute_tools(calls))
    for _ in range(20):
        if registry.started == ["A", "B"]:
            break
        await asyncio.sleep(0.01)

    assert registry.started == ["A", "B"]
    registry.release.set()
    await task

    assert [m.tool_call_id for m in loop.messages] == ["call_a", "call_b"]
    assert [m.content for m in loop.messages] == ["结果：A", "结果：B"]


@pytest.mark.asyncio
async def test_subagent_batch_is_not_serial(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """用耗时工具验证同一批 run_subagent 没有退化为串行执行。"""

    class SlowRegistry(ToolRegistry):
        async def execute_tool(
            self,
            name: str,
            args: dict,
            workspace_root: Path,
        ) -> ToolResult:
            await asyncio.sleep(0.05)
            return ToolResult(success=True, output=str(args["name"]))

    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=SlowRegistry(),
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )
    calls = [
        ToolCall(
            id="call_a",
            function=FunctionCall(
                name="run_subagent",
                arguments='{"name":"A","task":"任务 A"}',
            ),
        ),
        ToolCall(
            id="call_b",
            function=FunctionCall(
                name="run_subagent",
                arguments='{"name":"B","task":"任务 B"}',
            ),
        ),
    ]

    started = perf_counter()
    await loop._execute_tools(calls)

    assert perf_counter() - started < 0.09


@pytest.mark.asyncio
async def test_subagent_batch_respects_concurrency_limit(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """同一批 run_subagent 调用应遵守 agent.subagents.concurrency。"""

    class CountingRegistry(ToolRegistry):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def execute_tool(
            self,
            name: str,
            args: dict,
            workspace_root: Path,
        ) -> ToolResult:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.01)
                return ToolResult(success=True, output=str(args["name"]))
            finally:
                self.active -= 1

    config = app_config.model_copy(deep=True)
    config.agent.subagents.concurrency = 1
    registry = CountingRegistry()
    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=registry,
        renderer=MagicRenderer(),
        config=config,
        workspace_root=tmp_path,
    )
    calls = [
        ToolCall(
            id="call_a",
            function=FunctionCall(
                name="run_subagent",
                arguments='{"name":"A","task":"任务 A"}',
            ),
        ),
        ToolCall(
            id="call_b",
            function=FunctionCall(
                name="run_subagent",
                arguments='{"name":"B","task":"任务 B"}',
            ),
        ),
    ]

    await loop._execute_tools(calls)

    assert registry.max_active == 1
    assert [m.tool_call_id for m in loop.messages] == ["call_a", "call_b"]


@pytest.mark.asyncio
async def test_subagent_batch_rejects_more_than_max_agents(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """同一轮委派超过 max_agents 时应返回中文错误且不执行工具。"""

    class FailingRegistry(ToolRegistry):
        async def execute_tool(
            self,
            name: str,
            args: dict,
            workspace_root: Path,
        ) -> ToolResult:
            raise AssertionError("不应执行超过 max_agents 的子代理批次")

    config = app_config.model_copy(deep=True)
    config.agent.subagents.max_agents = 1
    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=FailingRegistry(),
        renderer=MagicRenderer(),
        config=config,
        workspace_root=tmp_path,
    )
    calls = [
        ToolCall(
            id="call_a",
            function=FunctionCall(
                name="run_subagent",
                arguments='{"name":"A","task":"任务 A"}',
            ),
        ),
        ToolCall(
            id="call_b",
            function=FunctionCall(
                name="run_subagent",
                arguments='{"name":"B","task":"任务 B"}',
            ),
        ),
    ]

    await loop._execute_tools(calls)

    assert [m.tool_call_id for m in loop.messages] == ["call_a", "call_b"]
    assert all("最多允许启动 1 个子代理" in (m.content or "") for m in loop.messages)


# ─── Test 4: 工具错误返回给模型 ────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_error_returned_to_model(tmp_path: Path, app_config: AppConfig) -> None:
    """工具执行出错时，错误信息应返回给模型。"""
    provider = MockStepProvider([
        # 第一轮：调用 read_file 读取不存在的文件
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_err",
                    index=0,
                    name="read_file",
                    arguments='{"file_path": "nonexistent.py"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(15, 5)),
        ],
        # 第二轮：模型基于错误信息回复
        [
            StreamChunk(type="text_delta", text="文件 nonexistent.py 不存在，"),
            StreamChunk(type="text_delta", text="请确认路径是否正确。"),
            StreamChunk(type="done", usage=_make_usage(60, 10)),
        ],
    ])

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "读取 nonexistent.py", tmp_path
    )

    assert response is not None
    # 验证 tool result 包含错误信息而非文件内容
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "不存在" in (tool_msgs[0].content or "")
    # 验证模型第二轮基于错误回复
    assert "不存在" in response or "正确" in response


# ─── Test 5: 超过最大轮次截断 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_max_rounds_exceeded(tmp_path: Path, app_config: AppConfig) -> None:
    """超过最大轮次后应停止循环。"""
    app_config.agent.max_rounds = 3  # 限制为 3 轮

    # 每轮都返回工具调用，触发持续循环
    tool_call_chunks = [
        StreamChunk(
            type="tool_call_delta",
            tool_call=PartialToolCall(
                id="call_repeat",
                index=0,
                name="glob",
                arguments='{"pattern": "*.py"}',
            ),
        ),
        StreamChunk(type="done", usage=_make_usage(10, 5)),
    ]

    provider = MockStepProvider([tool_call_chunks for _ in range(4)])  # 4 轮 > max_rounds

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "搜索文件", tmp_path
    )

    # 应该执行了 max_rounds 次（而非不限制地继续）
    assert provider.call_count == app_config.agent.max_rounds
    # 历史中应只有 max_rounds 个 assistant 消息
    assistant_msgs = [m for m in loop.messages if m.role == "assistant"]
    assert len(assistant_msgs) == app_config.agent.max_rounds


# ─── Test 6: 流式响应错误处理 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_error_handling(tmp_path: Path, app_config: AppConfig) -> None:
    """流式响应中发生错误应回滚用户消息。"""
    provider = MockStepProvider([
        [
            StreamChunk(type="error", text="API 内部错误"),
        ],
    ])

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "触发错误", tmp_path
    )

    assert response is None
    # 用户消息应被回滚
    assert len(loop.messages) == 0


# ─── Test 7: 工具 JSON 参数解析错误 ────────────────────────────────


@pytest.mark.asyncio
async def test_tool_invalid_json_args(tmp_path: Path, app_config: AppConfig) -> None:
    """工具参数 JSON 解析失败时，错误应返回给模型。"""
    provider = MockStepProvider([
        # 第一轮：参数不是合法 JSON
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_bad",
                    index=0,
                    name="read_file",
                    arguments="not-valid-json",
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        # 第二轮：模型基于错误信息回复
        [
            StreamChunk(type="text_delta", text="参数格式有误，请重试。"),
            StreamChunk(type="done", usage=_make_usage(30, 8)),
        ],
    ])

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "读取文件", tmp_path
    )

    assert response is not None
    # 验证第一轮 tool result 包含 JSON 解析错误
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "解析错误" in (tool_msgs[0].content or "")


# ─── Test 8: Rich markup 安全性 ──────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tools_no_rich_markup_error(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """工具名包含数字或下划线时不应抛出 Rich MarkupError。"""
    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_rf", index=0, name="read_file",
                    arguments='{"file_path": "test.py"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="已读取文件。"),
            StreamChunk(type="done", usage=_make_usage(30, 8)),
        ],
    ])

    (tmp_path / "test.py").write_text("content", encoding="utf-8")

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "读取 test.py", tmp_path
    )

    assert response is not None
    assert "已读取" in response
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1


@pytest.mark.asyncio
async def test_execute_tools_console_print_uses_text_object(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """验证 _execute_tools 使用 Text 对象而非拼接 Rich 标签。"""
    (tmp_path / "a.py").write_text("x", encoding="utf-8")

    registry = create_default_registry()
    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=registry,
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )

    printed_args: list[object] = []
    original_print = loop.renderer.console.print
    loop.renderer.console.print = lambda *a, **kw: printed_args.extend(a)  # type: ignore[method-assign]

    await loop._execute_tools([
        ToolCall(
            id="call_1",
            function=FunctionCall(name="glob", arguments='{"pattern": "*.py"}'),
        ),
    ])

    loop.renderer.console.print = original_print  # type: ignore[method-assign]

    assert len(printed_args) == 1
    text_obj = printed_args[0]
    assert isinstance(text_obj, Text)
    assert text_obj.style == "dim"
    assert "glob" in text_obj.plain


# ─── Mock Permission helpers ─────────────────────────────────────────


class MockPermissionConfirmer:
    """Mock PermissionConfirmer 返回预设结果。"""

    def __init__(self, action: str = "allow") -> None:
        self.action = action
        self.last_decision: PermissionDecision | None = None
        self.confirm_count = 0

    async def confirm(self, decision: PermissionDecision) -> ConfirmerResult:
        self.last_decision = decision
        self.confirm_count += 1
        return ConfirmerResult(action=self.action)  # type: ignore[arg-type]


class MockPermissionStore:
    """Mock PermissionStore 返回预设匹配结果。"""

    def __init__(self, has_match: bool = False) -> None:
        self.has_match = has_match
        self.added_rules: list[tuple[str, str]] = []

    def find_match(self, tool_name: str, target_paths: list[Path]) -> bool:
        return self.has_match

    def add_rule(self, tool_name: str, path_pattern: str) -> None:
        self.added_rules.append((tool_name, path_pattern))


# ─── Permission Tests ────────────────────────────────────────────────


def _agent_loop_with_permissions(
    provider: BaseProvider,
    tool_registry: ToolRegistry,
    config: AppConfig,
    tmp_path: Path,
    store: MockPermissionStore | None = None,
    confirmer: MockPermissionConfirmer | None = None,
) -> AgentLoop:
    """创建带有权限组件的 AgentLoop，用于测试。"""
    loop = AgentLoop(
        provider=provider,
        tool_registry=tool_registry,
        renderer=MagicRenderer(),
        config=config,
        workspace_root=tmp_path,
        permission_store=store,
        permission_confirmer=confirmer,
    )
    return loop


@pytest.mark.asyncio
async def test_deny_tool_not_executed_adds_rejection(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """DENY 级别工具不执行，追加拒绝 ToolMessage。"""
    # .env 属于敏感文件 → DENY
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=123", encoding="utf-8")

    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_deny",
                    index=0,
                    name="read_file",
                    arguments='{"file_path": ".env"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        # 第二轮：模型应收到拒绝信息并回复
        [
            StreamChunk(type="text_delta", text="无法读取敏感文件。"),
            StreamChunk(type="done", usage=_make_usage(30, 8)),
        ],
    ])

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "读取 .env", tmp_path
    )

    assert response is not None
    # 验证有 ToolMessage 且内容包含"权限拒绝"
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "权限拒绝" in (tool_msgs[0].content or "")
    # 拒绝结果应返回给模型
    assert "无法读取" in response


@pytest.mark.asyncio
async def test_caution_with_confirmer_deny(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """CAUTION 级别工具被 confirmer 拒绝时不执行。"""
    confirmer = MockPermissionConfirmer(action="deny")

    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_grep",
                    index=0,
                    name="grep",
                    arguments='{"pattern": "TODO"}',  # 无 glob → CAUTION
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="用户拒绝了搜索请求。"),
            StreamChunk(type="done", usage=_make_usage(20, 6)),
        ],
    ])

    loop = _agent_loop_with_permissions(
        provider, create_default_registry(), app_config, tmp_path,
        confirmer=confirmer,
    )
    response = await loop.run("搜索 TODO")

    assert response is not None
    assert confirmer.confirm_count == 1
    # 验证有拒绝 ToolMessage
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "用户拒绝" in (tool_msgs[0].content or "") or "权限拒绝" in (tool_msgs[0].content or "")
    assert "拒绝" in response


@pytest.mark.asyncio
async def test_caution_with_confirmer_allow(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """CAUTION 级别工具被 confirmer 允许时正常执行。"""
    confirmer = MockPermissionConfirmer(action="allow")

    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_grep",
                    index=0,
                    name="grep",
                    arguments='{"pattern": "TODO"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="搜索已完成。"),
            StreamChunk(type="done", usage=_make_usage(30, 8)),
        ],
    ])

    loop = _agent_loop_with_permissions(
        provider, create_default_registry(), app_config, tmp_path,
        confirmer=confirmer,
    )
    response = await loop.run("搜索 TODO")

    assert response is not None
    assert confirmer.confirm_count == 1
    # 验证工具正常执行（有 ToolMessage 且内容不为拒绝）
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "权限拒绝" not in (tool_msgs[0].content or "")
    assert "用户拒绝" not in (tool_msgs[0].content or "")
    assert "搜索已完成" in response


@pytest.mark.asyncio
async def test_caution_with_trust_mode_skips_confirmer(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """trust_mode=True 跳过 caution 级别的确认交互。"""
    app_config.permissions.trust_mode = True
    confirmer = MockPermissionConfirmer(action="deny")  # 即使 deny，信任模式下不会被调用

    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_grep",
                    index=0,
                    name="grep",
                    arguments='{"pattern": "TODO"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="搜索已完成。"),
            StreamChunk(type="done", usage=_make_usage(30, 8)),
        ],
    ])

    loop = _agent_loop_with_permissions(
        provider, create_default_registry(), app_config, tmp_path,
        confirmer=confirmer,
    )
    response = await loop.run("搜索 TODO")

    assert response is not None
    # 信任模式下 confirmer 不应被调用
    assert confirmer.confirm_count == 0
    # 工具正常执行
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1


@pytest.mark.asyncio
async def test_caution_with_store_match_skips_confirmer(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """PermissionStore 有匹配规则时跳过 confirmer。"""
    store = MockPermissionStore(has_match=True)
    confirmer = MockPermissionConfirmer(action="deny")  # 即使 deny，命中 store 时不会被调用

    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_grep",
                    index=0,
                    name="grep",
                    arguments='{"pattern": "TODO"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="搜索已完成。"),
            StreamChunk(type="done", usage=_make_usage(30, 8)),
        ],
    ])

    loop = _agent_loop_with_permissions(
        provider, create_default_registry(), app_config, tmp_path,
        store=store,
        confirmer=confirmer,
    )
    response = await loop.run("搜索 TODO")

    assert response is not None
    # store 命中，confirmer 不应被调用
    assert confirmer.confirm_count == 0
    # 工具正常执行
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1


@pytest.mark.asyncio
async def test_always_allow_adds_rule_to_store(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """用户选择 always allow 时规则写入 store，工具正常执行。"""
    store = MockPermissionStore()
    confirmer = MockPermissionConfirmer(action="always_allow")

    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_grep",
                    index=0,
                    name="grep",
                    arguments='{"pattern": "TODO"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="搜索已完成。"),
            StreamChunk(type="done", usage=_make_usage(30, 8)),
        ],
    ])

    loop = _agent_loop_with_permissions(
        provider, create_default_registry(), app_config, tmp_path,
        store=store,
        confirmer=confirmer,
    )
    response = await loop.run("搜索 TODO")

    assert response is not None
    assert confirmer.confirm_count == 1
    # 验证规则被添加
    assert len(store.added_rules) >= 1
    # 工具正常执行
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1


@pytest.mark.asyncio
async def test_safe_tool_bypasses_confirmer(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """SAFE 级别工具不触发 confirmer。"""
    store = MockPermissionStore()
    confirmer = MockPermissionConfirmer(action="deny")

    # 创建测试文件
    (tmp_path / "test.py").write_text("print('hello')", encoding="utf-8")

    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_read",
                    index=0,
                    name="read_file",
                    arguments='{"file_path": "test.py"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="文件内容已读取。"),
            StreamChunk(type="done", usage=_make_usage(30, 8)),
        ],
    ])

    loop = _agent_loop_with_permissions(
        provider, create_default_registry(), app_config, tmp_path,
        store=store,
        confirmer=confirmer,
    )
    response = await loop.run("读取 test.py")

    assert response is not None
    # SAFE 工具不应触发 confirmer 或 store
    assert confirmer.confirm_count == 0
    # 工具正常执行
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1


@pytest.mark.asyncio
async def test_deny_in_trust_mode_still_denies(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """trust_mode=True 时 DENY 仍然拒绝。"""
    app_config.permissions.trust_mode = True
    env_file = tmp_path / ".env"
    env_file.write_text("SECRET=123", encoding="utf-8")

    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_deny",
                    index=0,
                    name="read_file",
                    arguments='{"file_path": ".env"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="无法读取敏感文件。"),
            StreamChunk(type="done", usage=_make_usage(30, 8)),
        ],
    ])

    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "读取 .env", tmp_path
    )

    assert response is not None
    # 验证有拒绝 ToolMessage
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "权限拒绝" in (tool_msgs[0].content or "")


@pytest.mark.asyncio
async def test_caution_without_confirmer_rejected(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """CAUTION 级别工具没有 confirmer 时拒绝执行（fail-close）。"""
    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_grep",
                    index=0,
                    name="grep",
                    arguments='{"pattern": "TODO"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="已被拒绝。"),
            StreamChunk(type="done", usage=_make_usage(20, 6)),
        ],
    ])

    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )
    response = await loop.run("搜索 TODO")

    assert response is not None
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "权限拒绝" in (tool_msgs[0].content or "")
    # 确认是拒绝消息而非正常执行结果
    assert (tool_msgs[0].content or "").startswith("权限拒绝")


@pytest.mark.asyncio
async def test_dangerous_without_confirmer_rejected(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """DANGEROUS 级别工具没有 confirmer 时拒绝执行。"""
    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_bash",
                    index=0,
                    name="bash",
                    arguments='{"command": "echo hello"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        [
            StreamChunk(type="text_delta", text="命令被拒绝。"),
            StreamChunk(type="done", usage=_make_usage(20, 6)),
        ],
    ])

    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )
    response = await loop.run("执行命令 echo hello")

    assert response is not None
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "权限拒绝" in (tool_msgs[0].content or "")


# ─── Memory disabled tests ──────────────────────────────────────────────


@pytest.fixture
def app_config_memory_disabled() -> AppConfig:
    """memory.enabled=False 的测试用 AppConfig。"""
    from minicode.config.models import MemoryConfig

    return AppConfig(
        default_provider="mock",
        default_model="mock-model",
        max_tokens=4096,
        agent=AgentConfig(
            max_rounds=8,
            stream=True,
            planning=PlanningConfig(enabled=False),
        ),
        permissions=PermissionsConfig(trust_mode=False),
        memory=MemoryConfig(enabled=False),
        providers={
            "mock": ProviderConfig(
                api_key="sk-test",
                base_url="https://api.mock.com/v1",
                models=["mock-model"],
            ),
        },
    )


@pytest.mark.asyncio
async def test_memory_disabled_remember_filtered_from_schema(
    tmp_path: Path, app_config_memory_disabled: AppConfig
) -> None:
    """memory.enabled=False 时 tools schema 不应包含 remember。"""
    registry = create_default_registry()

    # AgentLoop 应过滤 remember 工具
    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=registry,
        renderer=MagicRenderer(),
        config=app_config_memory_disabled,
        workspace_root=tmp_path,
    )
    tool_names = {
        tool.get("function", {}).get("name")
        for tool in loop._get_tools_schema()
    }
    assert "remember" not in tool_names


@pytest.mark.asyncio
async def test_memory_disabled_remember_execution_rejected(
    tmp_path: Path, app_config_memory_disabled: AppConfig
) -> None:
    """memory.enabled=False 时 remember 执行应被拒绝。"""
    provider = MockStepProvider([
        # 第一轮：模型调用 remember
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_remember",
                    index=0,
                    name="remember",
                    arguments='{"name": "test-mem", "content": "test content"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        # 第二轮：模型基于结果回复
        [
            StreamChunk(type="text_delta", text="已处理记忆请求。"),
            StreamChunk(type="done", usage=_make_usage(20, 6)),
        ],
    ])

    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config_memory_disabled,
        workspace_root=tmp_path,
    )
    response = await loop.run("记住测试内容")

    assert response is not None
    # 验证有 ToolMessage 且内容为"记忆系统已禁用"
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "记忆系统已禁用" in (tool_msgs[0].content or "")


@pytest.mark.asyncio
async def test_memory_disabled_no_remember_in_system_prompt(
    tmp_path: Path, app_config_memory_disabled: AppConfig
) -> None:
    """memory.enabled=False 时 system prompt 不应包含记忆工具说明和 remember 工具描述。"""
    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config_memory_disabled,
        workspace_root=tmp_path,
    )
    assert "记忆工具使用说明" not in loop.system_prompt
    assert "  - remember:" not in loop.system_prompt


# ─── System prompt reload tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_reload_memory_updates_system_prompt(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """reload_memory() 应刷新 system prompt 以包含新记忆。"""
    from datetime import UTC, datetime

    from minicode.memory.manager import MemoryManager
    from minicode.memory.models import MemoryMetadata, MemoryType

    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )

    # 初始 system prompt 不含测试记忆
    assert "test-reload-memory" not in loop.system_prompt

    # 手动添加记忆
    manager = MemoryManager(tmp_path)
    now = datetime.now(UTC)
    meta = MemoryMetadata(
        name="test-reload-memory",
        description="Reload test",
        created_at=now,
        updated_at=now,
        type=MemoryType.USER,
    )
    manager.add(meta, "这是 reload 测试内容")

    # 调用 reload
    loop.reload_memory()

    # system prompt 应包含新记忆的名称和内容
    assert "test-reload-memory" in loop.system_prompt
    assert "这是 reload 测试内容" in loop.system_prompt


@pytest.mark.asyncio
async def test_reload_memory_after_delete_removes_from_prompt(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """删除记忆后 reload_memory() 不再包含被删记忆。"""
    from datetime import UTC, datetime

    from minicode.memory.manager import MemoryManager
    from minicode.memory.models import MemoryMetadata, MemoryType

    # 先添加一条记忆
    manager = MemoryManager(tmp_path)
    now = datetime.now(UTC)
    meta = MemoryMetadata(
        name="to-be-deleted",
        description="将被删除",
        created_at=now,
        updated_at=now,
        type=MemoryType.PROJECT,
    )
    manager.add(meta, "将被删除的内容")

    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )

    # 初始提示词包含该记忆
    assert "to-be-deleted" in loop.system_prompt

    # 删除记忆
    manager.delete("to-be-deleted")
    loop.reload_memory()

    # 提示词不应再包含被删记忆
    assert "to-be-deleted" not in loop.system_prompt


@pytest.mark.asyncio
async def test_reload_memory_disabled_no_content(
    tmp_path: Path, app_config_memory_disabled: AppConfig
) -> None:
    """memory.enabled=False 时 reload_memory() 不注入任何记忆内容。"""
    from datetime import UTC, datetime

    from minicode.memory.manager import MemoryManager
    from minicode.memory.models import MemoryMetadata, MemoryType

    # 添加记忆（绕过禁用，直接操作文件）
    manager = MemoryManager(tmp_path)
    now = datetime.now(UTC)
    meta = MemoryMetadata(
        name="secret-memory",
        description="不应出现",
        created_at=now,
        updated_at=now,
        type=MemoryType.USER,
    )
    manager.add(meta, "不应出现的记忆内容")

    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config_memory_disabled,
        workspace_root=tmp_path,
    )

    # reload 后不应包含任何记忆
    loop.reload_memory()
    assert "secret-memory" not in loop.system_prompt
    assert "不应出现" not in loop.system_prompt
    assert "用户记忆" not in loop.system_prompt


# ─── Test: ProviderError 处理 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_error_rolls_back_and_returns_none(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """ProviderError 应回滚用户消息并返回 None。"""
    class FailingProvider(BaseProvider):
        @property
        def name(self) -> str:
            return "failing"

        async def chat(
            self, messages: list[Message], tools: list[dict] | None = None,
            stream: bool = True, max_tokens: int | None = None,
        ) -> AsyncIterator[StreamChunk]:
            # 直接抛出 ProviderError，模拟 chat() 中重试耗尽后的行为
            raise ProviderError("模拟 Provider 错误")

        async def list_models(self) -> list[str]:
            return []

    provider = FailingProvider()
    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "测试错误", tmp_path
    )

    assert response is None
    # 用户消息应被回滚
    assert len(loop.messages) == 0


@pytest.mark.asyncio
async def test_stream_error_chunk_handling(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """流式 error chunk 应回滚用户消息。"""
    class ErrorChunkProvider(BaseProvider):
        @property
        def name(self) -> str:
            return "error-chunk"

        async def chat(
            self, messages: list[Message], tools: list[dict] | None = None,
            stream: bool = True, max_tokens: int | None = None,
        ) -> AsyncIterator[StreamChunk]:
            yield StreamChunk(
                type="error",
                text="请求在 3 次重试后仍然失败：API 超时",
            )

        async def list_models(self) -> list[str]:
            return []

    provider = ErrorChunkProvider()
    response, loop = await run_agent_loop(
        provider, create_default_registry(), app_config, "测试错误", tmp_path
    )

    assert response is None
    assert len(loop.messages) == 0  # 用户消息回滚


# ─── Test: 第二轮失败回滚 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_round_error_chunk_rolls_back_all(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """第一轮工具调用成功执行，第二轮流式 error chunk 应回滚本轮所有消息。"""
    provider = MockStepProvider([
        # 第一轮：工具调用（glob 会成功执行）
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_g", index=0, name="glob",
                    arguments='{"pattern": "*.py"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage(10, 5)),
        ],
        # 第二轮：流式错误（模拟 provider 临时故障）
        [
            StreamChunk(type="error", text="API 临时不可用"),
        ],
    ])

    loop = AgentLoop(
        provider=provider,
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )
    history_len = len(loop.messages)  # 0
    response = await loop.run("查找文件")

    assert response is None
    # 应完整回滚：用户消息 + 第一轮 assistant(tool_call) + tool(result) 全部清空
    assert len(loop.messages) == history_len


@pytest.mark.asyncio
async def test_second_round_provider_error_rolls_back_all(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """第一轮工具调用成功执行，第二轮 ProviderError 应回滚本轮所有消息。"""
    call_count = 0

    class SecondRoundFailsProvider(BaseProvider):
        @property
        def name(self) -> str:
            return "second-round-fails"

        async def chat(
            self, messages: list[Message], tools: list[dict] | None = None,
            stream: bool = True, max_tokens: int | None = None,
        ) -> AsyncIterator[StreamChunk]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(
                    type="tool_call_delta",
                    tool_call=PartialToolCall(
                        id="c1", index=0, name="glob",
                        arguments='{"pattern": "*.py"}',
                    ),
                )
                yield StreamChunk(type="done", usage=_make_usage(10, 5))
            else:
                raise ProviderError("模拟 Provider 错误")

        async def list_models(self) -> list[str]:
            return []

    loop = AgentLoop(
        provider=SecondRoundFailsProvider(),
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )
    history_len = len(loop.messages)  # 0
    response = await loop.run("查找文件")

    assert response is None
    # 应完整回滚所有消息
    assert len(loop.messages) == history_len


@pytest.mark.asyncio
async def test_with_existing_history_rollback_restores_original(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """已有对话历史的 loop，错误应回滚到历史长度而非完全清空。"""
    loop = AgentLoop(
        provider=MockStepProvider([]),
        tool_registry=create_default_registry(),
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
    )
    # 模拟已有对话历史
    loop.messages.append(Message(role="user", content="之前的对话"))
    loop.messages.append(Message(role="assistant", content="之前的回复"))
    history_len = len(loop.messages)  # 2

    # 新的 provider 会直接报错
    provider = MockStepProvider([
        [StreamChunk(type="error", text="错误")],
    ])
    loop.provider = provider
    response = await loop.run("新消息")

    assert response is None
    # 历史消息应保留，仅回滚本次添加的消息
    assert len(loop.messages) == history_len
    assert loop.messages[0].content == "之前的对话"
    assert loop.messages[1].content == "之前的回复"


@pytest.mark.asyncio
async def test_run_keeps_single_provider_call_when_planning_disabled(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """显式关闭规划模式时，保持一次 ReAct 调用行为。"""
    app_config.agent.planning.enabled = False
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
async def test_run_creates_plan_before_react_by_default(
    tmp_path: Path, app_config: AppConfig
) -> None:
    """默认配置下，普通任务先生成计划，再进入执行阶段。"""
    config = app_config.model_copy(deep=True)
    config.agent.planning = PlanningConfig()
    provider = MockStepProvider([
        [
            StreamChunk(
                type="text_delta",
                text=(
                    '{"goal":"修复问题","steps":['
                    '{"title":"阅读代码","description":"定位问题。"}'
                    "]}"
                ),
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
        config=config,
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
    provider = MockStepProvider([[StreamChunk(type="error", text="规划失败")]])
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
