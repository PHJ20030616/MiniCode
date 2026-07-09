"""/clear 命令 —— 清除对话上下文并创建新会话。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.utils.log import get_logger

logger = get_logger(__name__)


class ClearCommand(BaseCommand):
    """清除当前对话上下文，保存旧会话并创建新会话。"""

    name: str = "clear"
    description: str = "清除对话上下文并创建新会话"
    usage: str = "/clear"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """清除 AgentLoop 消息历史并创建新会话。

        旧会话已在此前通过 _auto_save 保存到磁盘。
        新会话创建后自动成为当前活跃会话。

        Args:
            args: 命令参数（忽略）。
            ctx: 命令执行上下文，包含 agent_loop 和 session_manager。

        Returns:
            CommandResult 包含操作结果消息。
        """
        # 清空 AgentLoop 消息
        if ctx.agent_loop is not None:
            message_count = len(ctx.agent_loop.messages)
            ctx.agent_loop.messages.clear()
            logger.debug(
                "上下文已清除",
                cleared_messages=message_count,
            )
        else:
            logger.debug("无活跃 AgentLoop，仅创建新会话")

        # 创建新会话
        new_session = ctx.session_manager.create(
            model=ctx.app_config.default_model,
            provider=ctx.app_config.default_provider,
            workspace_root=str(ctx.workspace_root),
        )

        # 通知 ChatApp 更新 _current_session
        if ctx.notify_session_created is not None:
            await ctx.notify_session_created(new_session)

        return CommandResult(
            message=f"上下文已清除，新会话已创建。（{new_session.id[:8]}）",
        )
