"""斜杠命令注册中心。

采用类方法注册模式（参考 ProviderRegistry），
管理所有斜杠命令的注册、查找和列表功能。
"""

from __future__ import annotations

from minicode.commands.base import BaseCommand


class CommandRegistry:
    """斜杠命令注册中心。

    管理所有可用命令：
    - 通过 name 或 alias 查找命令
    - 列出所有已注册命令
    - 检测名称和别名冲突

    用法：
        cmd = QuitCommand()
        CommandRegistry.register(cmd)
        result = CommandRegistry.find("quit")
    """

    _commands: dict[str, BaseCommand] = {}
    """命令名 → 命令实例 映射。"""
    _aliases: dict[str, str] = {}
    """别名 → 命令名 映射。"""

    @classmethod
    def register(cls, command: BaseCommand) -> BaseCommand:
        """注册一个命令。

        检查命令名和别名的唯一性，冲突时抛出 ValueError。

        Args:
            command: 要注册的 BaseCommand 实例。

        Returns:
            注册成功的命令实例（原样返回，可用作装饰器）。

        Raises:
            ValueError: 命令名或别名已存在时抛出。
        """
        # 检查命令名冲突
        if command.name in cls._commands:
            raise ValueError(
                f"命令名 '{command.name}' 已注册。"
            )

        # 检查别名冲突
        for alias in command.aliases:
            if alias in cls._aliases:
                existing = cls._aliases[alias]
                raise ValueError(
                    f"别名 '{alias}' 已被命令 '{existing}' 注册。"
                )

        # 注册命令
        cls._commands[command.name] = command

        # 注册别名
        for alias in command.aliases:
            cls._aliases[alias] = command.name

        return command

    @classmethod
    def find(cls, name_or_alias: str) -> BaseCommand | None:
        """按名称或别名查找命令。

        Args:
            name_or_alias: 命令名或别名（不含 '/' 前缀）。

        Returns:
            找到的 BaseCommand 实例，未找到时返回 None。
        """
        # 优先按命令名查找
        if name_or_alias in cls._commands:
            return cls._commands[name_or_alias]

        # 按别名查找
        if name_or_alias in cls._aliases:
            cmd_name = cls._aliases[name_or_alias]
            return cls._commands.get(cmd_name)

        return None

    @classmethod
    def list_all(cls) -> list[BaseCommand]:
        """返回所有已注册的命令列表。

        Returns:
            已注册的 BaseCommand 实例列表。
        """
        return list(cls._commands.values())
