"""/context 命令 —— 查看当前上下文窗口状态。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult


class ContextCommand(BaseCommand):
    """查看当前上下文窗口的统计信息。"""

    name: str = "context"
    aliases: list[str] = ["ctx"]
    description: str = "查看当前上下文窗口状态。"
    usage: str = "/context"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """显示上下文诊断统计。

        通过 AgentLoop 的 last_context_report 获取最新上下文构建报告。

        Args:
            args: 命令参数（忽略）。
            ctx: 命令执行上下文。

        Returns:
            CommandResult，包含上下文统计信息或提示文本。
        """
        agent_loop = ctx.agent_loop
        if agent_loop is None or agent_loop.last_context_report is None:
            return CommandResult(message="尚未开始对话，暂无上下文统计。")

        report = agent_loop.last_context_report

        lines: list[str] = []
        lines.append("上下文窗口统计：")
        lines.append("")
        lines.append(f"  原始消息数：            {report.original_message_count}")
        lines.append(f"  发送消息数：            {report.final_message_count}")
        lines.append(f"  原始估算词元数：        {report.original_estimated_tokens}")
        lines.append(f"  发送估算词元数：        {report.final_estimated_tokens}")
        lines.append(f"  裁剪消息数：            {report.dropped_message_count}")
        lines.append(f"  压缩工具结果数：        {report.compressed_tool_result_count}")

        return CommandResult(message="\n".join(lines))
