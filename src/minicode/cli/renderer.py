"""MiniCode 流式渲染器。

Claude Code 极简风格，去除 Panel 边框，使用 > 前缀展示用户输入，
助手回复直接渲染 Markdown。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape

from minicode.providers.base import StreamChunk, UsageInfo


class StreamingRenderer:
    """流式渲染器，负责将 LLM 流式输出实时渲染到终端。"""

    def __init__(self, console: Console) -> None:
        self.console = console

    def show_info(self, message: str) -> None:
        """显示一条系统信息。"""
        self.console.print(f"[info]{message}[/]")

    def show_error(self, message: str) -> None:
        """显示一条错误信息。"""
        self.console.print(f"[error]错误：{escape(message)}[/]")

    def show_usage(self, usage: UsageInfo) -> None:
        """显示 token 用量统计。"""
        self.console.print(
            f"[usage]输入: {usage.input_tokens} · 输出: {usage.output_tokens} · "
            f"总计: {usage.total_tokens}[/]"
        )

    def show_user_input(self, text: str) -> None:
        """Claude Code 风格，用 > 前缀显示用户输入。"""
        self.console.print()
        self.console.print(f"[bold]> {escape(text)}[/]")
        self.console.print()

    @staticmethod
    def _split_text_delta(buffer: str, text: str) -> tuple[str, str]:
        """返回合并后的完整文本和本次需要输出的新增文本。"""
        if text == buffer:
            return buffer, ""
        if buffer and text.startswith(buffer):
            return text, text[len(buffer):]
        return buffer + text, text

    async def stream_assistant_response(
        self,
        stream: AsyncIterator[StreamChunk],
    ) -> str | None:
        """流式渲染助手回复，并返回完整回复文本。

        Args:
            stream: Provider 返回的流式响应迭代器。

        Returns:
            完整回复文本。无文本回复时返回 None。
        """
        buffer = ""
        final_usage: UsageInfo | None = None
        has_text = False

        async for chunk in stream:
            if chunk.type == "text_delta" and chunk.text:
                next_buffer, output_text = self._split_text_delta(buffer, chunk.text)
                if not output_text:
                    continue
                has_text = True
                buffer = next_buffer

            elif chunk.type == "done":
                final_usage = chunk.usage
                break

            elif chunk.type == "error":
                if has_text:
                    self.console.print(Markdown(buffer))
                self.console.print(f"[red]{escape(chunk.text or '未知错误')}[/]")
                return None

        if has_text:
            self.console.print(Markdown(buffer))
        if final_usage:
            self.show_usage(final_usage)

        return buffer if has_text else None
