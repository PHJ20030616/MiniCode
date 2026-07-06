"""ReAct Agent Loop 测试。

测试覆盖：
1. 无工具直接回答（纯文本对话）
2. 一次 read_file 后回答（单工具调用）
3. 多工具串行调用
4. 工具错误返回给模型
5. 超过最大轮次截断
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from minicode.agent.loop import AgentLoop
from minicode.config.models import AgentConfig, AppConfig, PermissionsConfig, ProviderConfig
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
from minicode.tools.registry import ToolRegistry

# ─── Mock Provider ─────────────────────────────────────────────────


class MockStepProvider(BaseProvider):
    """逐步返回预设响应的 Mock Provider。

    每次调用 chat 时从 responses 列表中返回下一组 StreamChunk。
    用于模拟 ReAct 的多轮交互。
    """

    def __init__(self, responses: list[list[StreamChunk]]) -> None:
        self.responses = responses
        self.call_count = 0

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
        agent=AgentConfig(max_rounds=8, stream=True),
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

    def show_info(self, message: str) -> None:
        pass

    def show_error(self, message: str) -> None:
        pass

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
