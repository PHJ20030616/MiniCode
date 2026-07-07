"""MiniCode CLI 主应用程序。

提供 ChatApp 类管理对话主循环：
- prompt_toolkit 接收用户单行输入
- 创建 Provider 并委托 AgentLoop 处理 ReAct 闭环
- Rich 实时渲染 Markdown 回复
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from minicode.agent import AgentLoop
from minicode.cli.renderer import StreamingRenderer
from minicode.cli.theme import MINICODE_THEME
from minicode.config.models import AppConfig, ProviderConfig
from minicode.providers.registry import ProviderRegistry
from minicode.tools import create_default_registry
from minicode.utils.exceptions import ProviderError
from minicode.utils.log import get_logger

logger = get_logger(__name__)


class ChatApp:
    """MiniCode 对话应用主类。

    管理从用户输入到 Agent 回复的完整循环。
    使用 AgentLoop 处理 ReAct 推理与工具调用。
    """

    def __init__(self, app_config: AppConfig, workspace_root: Path | None = None) -> None:
        self.config = app_config
        self.workspace_root = workspace_root or Path.cwd()
        self.console = Console(theme=MINICODE_THEME, highlight=False)
        self.renderer = StreamingRenderer(self.console)
        self._prompt_session: PromptSession[Any] | None = None
        self._agent_loop: AgentLoop | None = None

    @property
    def session(self) -> PromptSession[Any]:
        """延迟初始化的 PromptSession。"""
        if self._prompt_session is None:
            self._prompt_session = PromptSession()
        return self._prompt_session

    async def run(self) -> None:
        """运行对话主循环。

        持续接收用户输入，调用 AgentLoop 处理，直到用户输入 exit/quit 或按 Ctrl+C/D 退出。
        """
        self.renderer.show_info("输入 exit 或 Ctrl+C 退出。")

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

    def _get_agent_loop(self) -> AgentLoop:
        """获取 AgentLoop 实例（懒加载并缓存）。

        首次调用时创建 Provider 和 ToolRegistry，然后构建 AgentLoop。
        同时创建 PermissionStore 和 PermissionConfirmer 用于权限确认。
        后续调用复用缓存的实例。

        Returns:
            AgentLoop 实例。

        Raises:
            ProviderError: Provider 未注册或配置不完整时。
        """
        if self._agent_loop is not None:
            return self._agent_loop

        provider_name = self.config.default_provider
        provider_config: ProviderConfig | None = self.config.providers.get(provider_name)

        if not provider_config:
            raise ProviderError(
                f"提供商 '{provider_name}' 未在配置中定义。\n"
                f"可用的提供商：{', '.join(self.config.providers.keys())}"
            )

        provider = ProviderRegistry.get(
            provider_name,
            model=self.config.default_model,
            api_key=provider_config.api_key,
            base_url=provider_config.base_url,
        )

        tool_registry = create_default_registry()
        # 注入权限组件
        from minicode.cli.confirm import PermissionConfirmer
        from minicode.permissions.store import PermissionStore

        permission_store = PermissionStore(self.workspace_root)
        permission_confirmer = PermissionConfirmer(console=self.console)

        # 创建 AgentLoop
        self._agent_loop = AgentLoop(
            provider=provider,
            tool_registry=tool_registry,
            renderer=self.renderer,
            config=self.config,
            workspace_root=self.workspace_root,
            permission_store=permission_store,
            permission_confirmer=permission_confirmer,
        )
        return self._agent_loop

    async def _handle_message(self, text: str) -> None:
        """处理一条用户消息。

        确认 AgentLoop 可用后执行 ReAct 循环。
        任何异常都会回滚本次消息，不污染对话历史。

        Args:
            text: 用户输入文本。
        """
        # 先确认 AgentLoop 可用
        try:
            agent_loop = self._get_agent_loop()
        except ProviderError as e:
            self.renderer.show_error(str(e))
            return
        except Exception as e:
            self.renderer.show_error(f"发生未知错误：{e}")
            logger.debug("获取 AgentLoop 异常", exc_info=True)
            return

        # 记录当前历史长度，用于异常回滚
        history_len = len(agent_loop.messages)

        try:
            await agent_loop.run(text)
        except ProviderError as e:
            # 回滚本次用户消息
            del agent_loop.messages[history_len:]
            self.renderer.show_error(str(e))
        except Exception as e:
            del agent_loop.messages[history_len:]
            self.renderer.show_error(f"发生未知错误：{e}")
            logger.debug("AgentLoop 处理异常", exc_info=True)
