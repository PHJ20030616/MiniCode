"""MiniCode CLI 主应用程序。

提供 ChatApp 类管理对话主循环：
- prompt_toolkit 接收用户单行输入
- 创建 Provider 并调用流式对话
- Rich 实时渲染 Markdown 回复
"""

from __future__ import annotations

from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from minicode.cli.renderer import StreamingRenderer
from minicode.cli.theme import MINICODE_THEME
from minicode.config.models import AppConfig, ProviderConfig
from minicode.providers.base import Message
from minicode.providers.registry import ProviderRegistry
from minicode.utils.exceptions import ProviderError
from minicode.utils.log import get_logger

logger = get_logger(__name__)


class ChatApp:
    """MiniCode 对话应用主类。

    管理从用户输入到模型回复的完整循环。
    """

    def __init__(self, app_config: AppConfig) -> None:
        self.config = app_config
        self.console = Console(theme=MINICODE_THEME, highlight=False)
        self.renderer = StreamingRenderer(self.console)
        self._prompt_session: PromptSession[Any] | None = None
        self._provider: Any | None = None
        self.messages: list[Message] = []

    @property
    def session(self) -> PromptSession[Any]:
        """延迟初始化的 PromptSession。"""
        if self._prompt_session is None:
            self._prompt_session = PromptSession()
        return self._prompt_session

    async def run(self) -> None:
        """运行对话主循环。

        持续接收用户输入，调用模型回复，直到用户输入 exit/quit 或按 Ctrl+C/D 退出。
        """
        self.renderer.show_info(
            "输入 exit 或 Ctrl+C 退出。"
        )

        while True:
            try:
                with patch_stdout():
                    user_input = await self.session.prompt_async("> ")

                user_input = user_input.strip()
                if not user_input:
                    continue

                if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
                    self.renderer.show_info("再见！")
                    break

                await self._handle_message(user_input)

            except (KeyboardInterrupt, EOFError):
                self.renderer.show_info("\n再见！")
                break

    def _get_provider(self) -> Any:
        """获取 Provider 实例（懒加载并缓存）。

        Returns:
            BaseProvider 实例。

        Raises:
            ProviderError: Provider 未注册或配置不完整时。
        """
        if self._provider is not None:
            return self._provider

        provider_name = self.config.default_provider
        provider_config: ProviderConfig | None = self.config.providers.get(
            provider_name
        )

        if not provider_config:
            raise ProviderError(
                f"提供商 '{provider_name}' 未在配置中定义。\n"
                f"可用的提供商：{', '.join(self.config.providers.keys())}"
            )

        self._provider = ProviderRegistry.get(
            provider_name,
            model=self.config.default_model,
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
        )
        return self._provider

    async def _handle_message(self, text: str) -> None:
        """处理一条用户消息。

        确认 Provider 可用后将用户消息加入历史，调用模型获取流式回复并渲染。
        任何异常都会回滚本次消息，不污染对话历史。

        Args:
            text: 用户输入文本。
        """
        # 先确认 Provider 可用，再操作历史
        try:
            provider = self._get_provider()
        except ProviderError as e:
            self.renderer.show_error(str(e))
            return
        except Exception as e:
            self.renderer.show_error(f"发生未知错误：{e}")
            logger.debug("获取 Provider 异常", exc_info=True)
            return

        # 记录当前历史长度，用于异常回滚
        history_len = len(self.messages)
        self.messages.append(Message(role="user", content=text))

        try:
            stream = provider.chat(
                messages=self.messages,
                stream=self.config.agent.stream,
                max_tokens=self.config.max_tokens,
            )

            response = await self.renderer.stream_assistant_response(stream)

            if response is not None:
                self.messages.append(Message(role="assistant", content=response))
            else:
                # 空回复或流式错误：回滚本次用户消息
                del self.messages[history_len:]

        except ProviderError as e:
            del self.messages[history_len:]
            self.renderer.show_error(str(e))
        except Exception as e:
            del self.messages[history_len:]
            self.renderer.show_error(f"发生未知错误：{e}")
            logger.debug("对话处理异常", exc_info=True)
