"""命令注册器单元测试。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult


class _StubCommand(BaseCommand):
    """用于测试的桩命令。"""

    name: str = "stub"
    description: str = "测试命令"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        return CommandResult(message=f"stub executed with: {args}")
