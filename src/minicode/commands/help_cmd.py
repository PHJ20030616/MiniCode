"""/help 命令 —— 显示可用命令列表。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.commands.registry import CommandRegistry


class HelpCommand(BaseCommand):
    """显示所有可用命令及其用法。"""

    name: str = "help"
    description: str = "显示帮助信息"
    usage: str = "/help"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """遍历已注册命令，格式化输出帮助信息。

        通过 CommandRegistry.list_all() 动态获取命令列表，
        新增命令无需修改 /help 代码。

        Args:
            args: 命令参数（忽略）。
            ctx: 命令执行上下文。

        Returns:
            CommandResult，message 包含格式化后的帮助文本。
        """
        commands = sorted(
            CommandRegistry.list_all(), key=lambda c: c.name
        )

        if not commands:
            return CommandResult(message="没有可用的命令。")

        lines: list[str] = []
        lines.append("可用命令：")
        lines.append("")

        for cmd in commands:
            alias_str = f"（{'、'.join('/' + a for a in cmd.aliases)}）" if cmd.aliases else ""
            lines.append(f"  /{cmd.name} {alias_str}")
            lines.append(f"    {cmd.description}")
            if cmd.usage:
                lines.append(f"    用法：{cmd.usage}")
            lines.append("")

        lines.append("输入 /<命令名> 执行命令，或直接输入文本与 AI 对话。")

        return CommandResult(message="\n".join(lines))
