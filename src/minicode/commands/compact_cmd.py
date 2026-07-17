"""/compact 命令 —— 手动压缩当前旧上下文。"""

from __future__ import annotations

from minicode.agent.compaction import format_compaction_report
from minicode.commands.base import BaseCommand, CommandContext, CommandResult


class CompactCommand(BaseCommand):
    """手动压缩当前 AgentLoop 的旧历史上下文。"""

    name: str = "compact"
    description: str = "手动压缩当前旧上下文。"
    usage: str = "/compact [关注说明]"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """执行手动上下文压缩。"""
        agent_loop = ctx.agent_loop
        if agent_loop is None:
            return CommandResult(
                success=False,
                message="尚未开始对话，无法压缩上下文。",
            )

        focus = args.strip() or None
        result = await agent_loop.compact_context(focus)
        if not result.changed:
            return CommandResult(message="当前没有可压缩的历史上下文。")

        message = (
            format_compaction_report(result.report)
            if result.report is not None
            else "上下文已压缩。"
        )
        return CommandResult(message=message, history_changed=True)
