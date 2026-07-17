"""/context 命令 —— 查看当前上下文窗口状态。"""

from __future__ import annotations

from minicode.agent.context_models import CompactionTrigger
from minicode.commands.base import BaseCommand, CommandContext, CommandResult


class ContextCommand(BaseCommand):
    """查看当前上下文窗口的统计信息。"""

    name: str = "context"
    aliases: list[str] = ["ctx"]
    description: str = "查看当前上下文窗口状态。"
    usage: str = "/context"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """显示上下文诊断统计。

        Args:
            args: 命令参数（忽略）。
            ctx: 命令执行上下文。

        Returns:
            CommandResult，包含上下文统计信息或提示文本。
        """
        agent_loop = ctx.agent_loop
        if agent_loop is None:
            return CommandResult(message="尚未开始对话，暂无上下文统计。")

        usage = agent_loop.get_context_usage()
        compaction_config = agent_loop.config.agent.context.compaction
        lines = [
            "上下文窗口状态：",
            (
                f"当前占用：{usage.estimated_tokens:,} / "
                f"{usage.max_input_tokens:,} 词元（{usage.occupancy_ratio:.1%}）"
            ),
            f"自动压缩阈值：{compaction_config.trigger_ratio:.1%}",
            f"压缩目标：{compaction_config.target_ratio:.1%}",
            f"当前消息数：{usage.message_count:,}",
        ]

        report = agent_loop.last_compaction_report
        if report is None:
            lines.append("最近压缩：无")
            return CommandResult(message="\n".join(lines))

        trigger_label = (
            "自动"
            if report.trigger == CompactionTrigger.AUTOMATIC
            else "手动"
        )
        lines.extend(
            [
                f"最近压缩：{trigger_label}",
                (
                    "压缩时间："
                    f"{report.created_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
                ),
                f"压缩前消息数：{report.before_message_count:,}",
                f"已清理工具结果数：{report.cleared_tool_result_count:,}",
                (
                    "当前未消费工具结果数："
                    f"{usage.unconsumed_tool_result_count:,}"
                ),
                f"总结重试：{'是' if report.retry_used else '否'}",
            ]
        )

        return CommandResult(message="\n".join(lines))
