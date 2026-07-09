"""/quit 命令 —— 退出 MiniCode。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult


class QuitCommand(BaseCommand):
    """退出 MiniCode 程序。"""

    name: str = "quit"
    aliases: list[str] = ["exit", "q"]
    description: str = "退出 MiniCode"
    usage: str = "/quit"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """执行退出命令。

        Args:
            args: 命令参数（忽略）。
            ctx: 命令执行上下文。

        Returns:
            CommandResult(should_exit=True)。
        """
        return CommandResult(
            should_exit=True,
            message="再见！",
        )
