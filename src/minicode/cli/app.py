"""MiniCode CLI 主应用程序。

提供 ChatApp 类管理对话主循环：
- prompt_toolkit 接收用户单行输入
- 创建 Provider 并委托 AgentLoop 处理 ReAct 闭环
- Rich 实时渲染 Markdown 回复
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console

from minicode.agent import AgentLoop
from minicode.cli.completer import CommandCompleter
from minicode.cli.renderer import StreamingRenderer
from minicode.cli.theme import MINICODE_THEME
from minicode.commands.base import CommandContext
from minicode.commands.registry import CommandRegistry
from minicode.config.models import AppConfig, ProviderConfig
from minicode.providers.registry import ProviderRegistry
from minicode.session import Session, SessionManager
from minicode.tools import create_default_registry
from minicode.tools.subagent import RunSubagentTool
from minicode.utils.exceptions import ProviderError
from minicode.utils.log import get_logger

logger = get_logger(__name__)


class ChatApp:
    """MiniCode 对话应用主类。

    管理从用户输入到 Agent 回复的完整循环。
    使用 AgentLoop 处理 ReAct 推理与工具调用。
    每轮对话完成后自动保存会话到磁盘。
    """

    def __init__(self, app_config: AppConfig, workspace_root: Path | None = None) -> None:
        self.config = app_config
        self.workspace_root = workspace_root or Path.cwd()
        self.console = Console(theme=MINICODE_THEME, highlight=False)
        self.renderer = StreamingRenderer(self.console)
        self._prompt_session: PromptSession[Any] | None = None
        self._agent_loop: AgentLoop | None = None
        self._session_manager: SessionManager | None = None
        self._current_session: Session | None = None
        self._interrupted: bool = False

    @property
    def session(self) -> PromptSession[Any]:
        """延迟初始化的 PromptSession，带有命令自动补全。"""
        if self._prompt_session is None:
            completer = CommandCompleter(CommandRegistry)
            self._prompt_session = PromptSession(
                completer=completer,
                complete_while_typing=True,
            )
        return self._prompt_session

    async def run(self) -> None:
        """运行对话主循环。

        持续接收用户输入，调用 AgentLoop 处理，直到用户输入 exit/quit 或按 Ctrl+C/D 退出。
        注册 SIGINT 信号处理器，确保中断时优雅保存会话。
        """
        import signal

        self.renderer.show_info("输入 exit 或 Ctrl+C 退出。")

        # 注册 SIGINT 处理器：设置中断标志
        self._interrupted = False

        def _handle_sigint(signum: object, frame: object) -> None:
            self._interrupted = True

        signal.signal(signal.SIGINT, _handle_sigint)

        while True:
            if self._interrupted:
                await self._shutdown_gracefully()
                break

            try:
                with patch_stdout():
                    user_input = await self.session.prompt_async("> ")

                user_input = user_input.strip()
                if not user_input:
                    continue

                should_exit = await self._handle_input(user_input)
                if should_exit:
                    await self._shutdown_gracefully()
                    break

            except (KeyboardInterrupt, EOFError):
                await self._shutdown_gracefully()
                break

    async def _shutdown_gracefully(self) -> None:
        """优雅关闭：保存当前会话 + 清理资源。

        在收到退出信号或全局异常时调用。
        使用 fail-soft 策略：保存失败不阻断退出流程。
        """
        logger.debug("正在优雅关闭...")
        try:
            if self._agent_loop is not None and self._current_session is not None:
                self._current_session.messages = list(self._agent_loop.messages)
                self._current_session.updated_at = datetime.now(UTC)
                self._get_session_manager().save(self._current_session)
                logger.debug("会话已保存", session_id=self._current_session.id)
        except Exception as e:
            logger.debug("优雅关闭时保存会话失败", error=str(e))
        self.renderer.show_info("再见！")

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

        if self.config.agent.subagents.enabled:
            from minicode.agent.subagents.manager import SubagentManager

            subagent_manager = SubagentManager(
                provider=provider,
                parent_registry=tool_registry,
                config=self.config,
                workspace_root=self.workspace_root,
                permission_store=permission_store,
                permission_confirmer=permission_confirmer,
                renderer=self.renderer,
            )
            tool_registry.register_factory(
                name=RunSubagentTool.name,
                factory=lambda: RunSubagentTool(manager=subagent_manager),
                schema=RunSubagentTool.get_static_schema(),
                description=RunSubagentTool.description,
                source="runtime.subagent",
            )

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

    def _get_session_manager(self) -> SessionManager:
        """获取 SessionManager 实例（懒加载并缓存）。

        Returns:
            SessionManager 实例。
        """
        if self._session_manager is None:
            from minicode.session import SessionManager

            self._session_manager = SessionManager(self.workspace_root)
        return self._session_manager

    async def _auto_save(self, agent_loop: AgentLoop) -> None:
        """Agent Loop 每轮完成后自动保存会话到磁盘。

        使用 fail-soft 策略：记录 debug 日志但不会阻断对话流程。

        Args:
            agent_loop: 刚完成一轮的 AgentLoop 实例。
        """
        try:
            manager = self._get_session_manager()
            if self._current_session is None:
                self._current_session = manager.create(
                    model=self.config.default_model,
                    provider=self.config.default_provider,
                    workspace_root=str(self.workspace_root),
                )
            # 同步消息历史到会话（浅拷贝快照）
            self._current_session.messages = list(agent_loop.messages)
            self._current_session.updated_at = datetime.now(UTC)
            manager.save(self._current_session)
        except Exception as e:
            logger.debug("自动保存会话失败", error=str(e))

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
            result = await agent_loop.run(text)
            if result is not None:
                await self._auto_save(agent_loop)
        except ProviderError as e:
            # 回滚本次用户消息
            del agent_loop.messages[history_len:]
            self.renderer.show_error(str(e))
        except Exception as e:
            del agent_loop.messages[history_len:]
            self.renderer.show_error(f"发生未知错误：{e}")
            logger.debug("AgentLoop 处理异常", exc_info=True)

    async def _clear_and_new_session(self) -> None:
        """清空当前 AgentLoop 消息并创建新会话。

        由 /clear 命令调用，也用于 /session delete 删除当前会话时。
        旧会话已在 _auto_save 中保存，此处仅清理上下文和创建新会话。
        """
        agent_loop = self._agent_loop
        # 清空现有消息历史，AgentLoop 会在下一轮自动注入 system prompt
        if agent_loop is not None:
            agent_loop.messages.clear()

        # 创建新会话
        manager = self._get_session_manager()
        self._current_session = manager.create(
            model=self.config.default_model,
            provider=self.config.default_provider,
            workspace_root=str(self.workspace_root),
        )
        logger.debug(
            "已创建新会话",
            session_id=self._current_session.id,
            reason="clear_command",
        )

    async def switch_session(self, session_id: str) -> bool:
        """切换到指定会话。

        保存当前会话后，加载目标会话并替换 AgentLoop 的消息历史。

        Args:
            session_id: 目标会话 ID。

        Returns:
            True 表示切换成功，False 表示失败。
        """
        manager = self._get_session_manager()

        # 保存当前会话
        if self._current_session is not None and self._agent_loop is not None:
            self._current_session.messages = list(self._agent_loop.messages)
            manager.save(self._current_session)

        # 加载目标会话
        target = manager.load(session_id)
        if target is None:
            return False

        # 替换 AgentLoop 消息
        agent_loop = self._get_agent_loop()
        agent_loop.messages.clear()
        agent_loop.messages.extend(target.messages)

        # 更新当前会话引用
        self._current_session = target

        logger.debug(
            "已切换会话",
            session_id=session_id,
            message_count=target.message_count,
        )
        return True

    async def _handle_input(self, text: str) -> bool:
        """处理用户输入，路由到命令或 AgentLoop。

        Args:
            text: 用户输入文本。

        Returns:
            True 表示应退出程序。
        """
        # 向后兼容：保留直接输入 exit/quit 的能力（无斜杠前缀）
        if text.lower() in ("exit", "quit"):
            return True

        if text.startswith("/"):
            return await self._handle_command(text)
        else:
            await self._handle_message(text)
            return False

    async def _handle_command(self, text: str) -> bool:
        """处理斜杠命令。

        1. 解析命令名和参数
        2. 查找命令
        3. 构建 CommandContext
        4. 执行命令
        5. 处理结果

        Args:
            text: 完整的命令文本（含 '/' 前缀）。

        Returns:
            True 表示应退出程序（/quit 命令）。
        """
        # 解析命令名和参数
        cmd_text = text[1:]  # 去掉 '/' 前缀
        if not cmd_text:
            self.renderer.show_error("请输入命令名，输入 /help 查看可用命令。")
            return False

        parts = cmd_text.split(maxsplit=1)
        cmd_name = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""

        # 向后兼容：保留直接输入 exit/quit 的能力
        if cmd_name in ("exit", "quit", "q"):
            return True

        # 查找命令
        command = CommandRegistry.find(cmd_name)
        if command is None:
            self.renderer.show_error(
                f"未知命令：/{cmd_name}。输入 /help 查看可用命令。"
            )
            return False

        # 构建上下文
        ctx = self._build_command_context()

        # 执行命令
        try:
            result = await command.execute(cmd_args, ctx)
        except Exception as e:
            logger.debug("命令执行异常", command=cmd_name, error=str(e), exc_info=True)
            self.renderer.show_error(f"命令执行失败：{e}")
            return False

        agent_loop = self._agent_loop
        try:
            # 历史已由命令修改时，即使结果渲染失败也必须持久化。
            if result.message:
                if result.success:
                    self.renderer.show_info(result.message)
                else:
                    self.renderer.show_error(result.message)
        finally:
            if result.history_changed and agent_loop is not None:
                await self._auto_save(agent_loop)

        return result.should_exit

    def _build_command_context(self) -> CommandContext:
        """构建命令执行上下文。

        Returns:
            CommandContext 实例，包含所有命令需要的依赖。
        """
        return CommandContext(
            app_config=self.config,
            workspace_root=self.workspace_root,
            session_manager=self._get_session_manager(),
            agent_loop=self._agent_loop,  # 可能为 None（首次对话前）
            renderer=self.renderer,
            console=self.console,
            # 注入会话生命周期回调，保持 ChatApp._current_session 与命令操作同步
            notify_session_created=self._on_session_created,
            notify_session_switched=self._on_session_switched,
            notify_session_deleted=self._on_session_deleted,
        )

    async def _on_session_created(self, session: Any) -> None:
        """/clear 创建新会话后的回调，更新 _current_session。"""
        self._current_session = session

    async def _on_session_switched(self, session: Any) -> None:
        """/session switch 切换会话后的回调，更新 _current_session。"""
        self._current_session = session

    async def _on_session_deleted(self, session_id: str) -> None:
        """/session delete 删除会话后的回调。若删除的是当前活跃会话，自动创建新会话。"""
        if self._current_session is not None and self._current_session.id == session_id:
            # 当前会话被删除，重置到新会话
            await self._clear_and_new_session()
