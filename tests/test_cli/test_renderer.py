"""测试 StreamingRenderer 渲染器。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from io import StringIO

import pytest
from rich.console import Console

import minicode.cli.renderer as renderer_module
from minicode.cli.renderer import StreamingRenderer
from minicode.cli.theme import MINICODE_THEME
from minicode.providers.base import StreamChunk, UsageInfo


@pytest.fixture
def console() -> Console:
    """创建一个捕获输出的 Console 实例供测试使用。"""
    return Console(file=StringIO(), theme=MINICODE_THEME, highlight=False)


@pytest.fixture
def renderer(console: Console) -> StreamingRenderer:
    """创建一个 StreamingRenderer 实例。"""
    return StreamingRenderer(console)


def _get_output(console: Console) -> str:
    """获取 Console 捕获的输出内容。"""
    return console.file.getvalue()  # type: ignore[attr-defined, no-any-return]


class TestInfoAndError:
    """测试信息与错误显示方法。"""

    def test_show_info_output(self, renderer: StreamingRenderer, console: Console) -> None:
        """show_info 应输出信息文本。"""
        renderer.show_info("测试信息")
        assert "测试信息" in _get_output(console)

    def test_show_error_output(self, renderer: StreamingRenderer, console: Console) -> None:
        """show_error 应输出错误文本。"""
        renderer.show_error("测试错误")
        output = _get_output(console)
        assert "测试错误" in output
        assert "错误" in output

    def test_show_usage_output(self, renderer: StreamingRenderer, console: Console) -> None:
        """show_usage 应显示 token 用量统计。"""
        usage = UsageInfo(input_tokens=10, output_tokens=20, total_tokens=30)
        renderer.show_usage(usage)
        output = _get_output(console)
        assert "10" in output
        assert "20" in output
        assert "30" in output

    def test_show_usage_zero(self, renderer: StreamingRenderer, console: Console) -> None:
        """用量全零时也能正确显示。"""
        usage = UsageInfo(input_tokens=0, output_tokens=0, total_tokens=0)
        renderer.show_usage(usage)
        output = _get_output(console)
        assert "0" in output


class TestUserInput:
    """测试用户输入显示方法。"""

    def test_show_user_input_output(self, renderer: StreamingRenderer, console: Console) -> None:
        """show_user_input 应显示用户输入文本。"""
        renderer.show_user_input("你好，世界！")
        assert "你好，世界！" in _get_output(console)

    def test_show_user_input_empty(self, renderer: StreamingRenderer, console: Console) -> None:
        """空输入也能正常显示。"""
        renderer.show_user_input("")
        output = _get_output(console)
        # 空输入不应抛出异常，至少输出内容
        assert output

    def test_show_user_input_rich_markup_escaped(
        self, renderer: StreamingRenderer, console: Console
    ) -> None:
        """用户输入中的 Rich markup 应被转义，不触发 MarkupError。"""
        renderer.show_user_input("[/]")
        output = _get_output(console)
        assert "[/]" in output

    def test_show_user_input_rich_markup_style_not_rendered(
        self, renderer: StreamingRenderer, console: Console
    ) -> None:
        """用户输入 [red]hi[/] 应显示字面文本，不被渲染为红色。"""
        renderer.show_user_input("[red]hi[/]")
        output = _get_output(console)
        assert "[red]hi[/]" in output

    def test_show_error_rich_markup_escaped(
        self, renderer: StreamingRenderer, console: Console
    ) -> None:
        """错误信息中的 Rich markup 应被转义。"""
        renderer.show_error("token [invalid]")
        output = _get_output(console)
        assert "[invalid]" in output


@pytest.mark.asyncio
class TestStreamAssistantResponse:
    """测试流式渲染方法。"""

    async def _make_stream(self, chunks: list[StreamChunk]) -> AsyncIterator[StreamChunk]:
        """辅助方法：将 StreamChunk 列表转换为异步迭代器。"""
        for chunk in chunks:
            yield chunk

    async def test_text_streaming(self, renderer: StreamingRenderer) -> None:
        """流式文本应正确拼接并返回完整文本。"""
        chunks = [
            StreamChunk(type="text_delta", text="你好，"),
            StreamChunk(type="text_delta", text="世界！"),
            StreamChunk(type="done", usage=None),
        ]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result == "你好，世界！"

    async def test_cumulative_text_chunks_are_not_repeated(
        self, renderer: StreamingRenderer, console: Console
    ) -> None:
        """兼容累计式文本块，避免把已渲染内容重复追加。"""
        repeated_prefix = "以下是 Python 实现的二分查找算法"
        chunks = [
            StreamChunk(type="text_delta", text="以下是 Python"),
            StreamChunk(type="text_delta", text="以下是 Python 实现的"),
            StreamChunk(type="text_delta", text=repeated_prefix),
            StreamChunk(type="done", usage=None),
        ]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result == repeated_prefix
        assert _get_output(console).count("以下是 Python") == 1

    async def test_output_is_not_repeated_when_live_refresh_appends(
        self,
        renderer: StreamingRenderer,
        console: Console,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """即使 Live 刷新退化成追加输出，也不应重复打印完整回复。"""
        class AppendOnlyLive:
            def __init__(self, renderable: object, **kwargs: object) -> None:
                self.console = kwargs["console"]

            def __enter__(self) -> AppendOnlyLive:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def update(self, renderable: object) -> None:
                text = getattr(renderable, "markup", str(renderable))
                self.console.print(text)

        monkeypatch.setattr(renderer_module, "Live", AppendOnlyLive, raising=False)

        chunks = [
            StreamChunk(type="text_delta", text="以下是 Python"),
            StreamChunk(type="text_delta", text=" 实现的"),
            StreamChunk(type="text_delta", text="二分查找算法"),
            StreamChunk(type="done", usage=None),
        ]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))

        assert result == "以下是 Python 实现的二分查找算法"
        assert _get_output(console).count("以下是 Python") == 1

    async def test_single_text_chunk(self, renderer: StreamingRenderer) -> None:
        """单块文本也能正确处理。"""
        chunks = [
            StreamChunk(type="text_delta", text="Hello!"),
            StreamChunk(type="done", usage=None),
        ]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result == "Hello!"

    async def test_no_text_response(self, renderer: StreamingRenderer) -> None:
        """无文本回复时返回 None。"""
        chunks = [
            StreamChunk(type="done", usage=None),
        ]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result is None

    async def test_error_chunk(self, renderer: StreamingRenderer) -> None:
        """错误块应返回 None 并停止流式处理。"""
        chunks = [
            StreamChunk(type="text_delta", text="部分文本"),
            StreamChunk(type="error", text="发生错误"),
        ]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result is None  # 发生错误时不返回文本

    async def test_error_before_text(self, renderer: StreamingRenderer) -> None:
        """还未收到文本就出错时返回 None。"""
        chunks = [StreamChunk(type="error", text="超时")]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result is None

    async def test_error_chunk_markup_escaped(
        self, renderer: StreamingRenderer, console: Console
    ) -> None:
        """错误块中的 Rich markup 应被转义。"""
        chunks = [StreamChunk(type="error", text="[broken]")]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result is None
        output = _get_output(console)
        assert "[broken]" in output

    async def test_text_with_usage(self, renderer: StreamingRenderer, console: Console) -> None:
        """文本回复后应显示用量统计。"""
        usage = UsageInfo(input_tokens=5, output_tokens=10, total_tokens=15)
        chunks = [
            StreamChunk(type="text_delta", text="回复内容"),
            StreamChunk(type="done", usage=usage),
        ]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result == "回复内容"
        output = _get_output(console)
        assert "5" in output
        assert "10" in output
        assert "15" in output

    async def test_markdown_content(
        self, renderer: StreamingRenderer, console: Console
    ) -> None:
        """Markdown 格式文本应使用 Rich Markdown 渲染输出。"""
        md_text = "# 标题\n\n这是**粗体**文本。\n\n- 列表项1\n- 列表项2"
        chunks = [
            StreamChunk(type="text_delta", text=md_text),
            StreamChunk(type="done", usage=None),
        ]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result == md_text
        output = _get_output(console)
        assert "# 标题" not in output
        assert "**粗体**" not in output
        assert "标题" in output
        assert "粗体" in output

    async def test_empty_text_delta(self, renderer: StreamingRenderer) -> None:
        """空的 text_delta 不应影响最终结果。"""
        chunks = [
            StreamChunk(type="text_delta", text="开头"),
            StreamChunk(type="text_delta", text=""),  # 空文本
            StreamChunk(type="text_delta", text="结尾"),
            StreamChunk(type="done", usage=None),
        ]
        result = await renderer.stream_assistant_response(self._make_stream(chunks))
        assert result == "开头结尾"
