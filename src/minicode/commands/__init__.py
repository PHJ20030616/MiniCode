"""斜杠命令系统。

提供命令抽象、注册、路由的完整基础设施。
所有命令通过 CommandRegistry.register() 注册后，
由 ChatApp 的输入路由自动分发。
"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.commands.registry import CommandRegistry

__all__ = [
    "BaseCommand",
    "CommandContext",
    "CommandResult",
    "CommandRegistry",
    "register_all_commands",
]


def register_all_commands() -> None:
    """注册所有 v0.3 斜杠命令（幂等，可多次调用）。

    在 main.py 启动时调用一次。
    后续版本新增命令时在此函数中添加对应注册行。
    """
    # 先清除已有注册，确保幂等
    CommandRegistry._commands.clear()
    CommandRegistry._aliases.clear()

    from minicode.commands.clear_cmd import ClearCommand
    from minicode.commands.config_cmd import ConfigCommand
    from minicode.commands.help_cmd import HelpCommand
    from minicode.commands.memory_cmd import MemoryCommand
    from minicode.commands.quit_cmd import QuitCommand
    from minicode.commands.session_cmd import SessionCommand

    CommandRegistry.register(QuitCommand())
    CommandRegistry.register(HelpCommand())
    CommandRegistry.register(ClearCommand())
    CommandRegistry.register(SessionCommand())
    CommandRegistry.register(ConfigCommand())
    CommandRegistry.register(MemoryCommand())
