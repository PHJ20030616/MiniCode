"""斜杠命令自动补全器。

提供基于 prompt_toolkit Completer 接口的命令名补全。
当用户输入以 '/' 开头时，从 CommandRegistry 获取所有已注册命令，
通过前缀匹配筛选候选，展示给用户选择。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

if TYPE_CHECKING:
    from minicode.commands.registry import CommandRegistry


class CommandCompleter(Completer):
    """为 '/' 开头的输入提供命令补全。

    只在输入以 '/' 开头时激活，根据已输入的内容做前缀匹配，
    返回匹配的命令名（包括别名）作为补全候选。

    Args:
        registry: CommandRegistry 类（使用其类方法 list_all）。
    """

    def __init__(self, registry: type[CommandRegistry]) -> None:
        self._registry = registry

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """生成补全候选。

        如果输入不以 '/' 开头，直接返回（不产生候选）。
        否则去掉 '/' 前缀，遍历所有已注册命令匹配命令名和别名。

        Args:
            document: 当前输入文档。
            complete_event: 补全事件信息。

        Yields:
            Completion: 补全候选，每个代表一条命令。
        """
        text = document.text
        if not text.startswith("/"):
            return

        # 去掉 '/' 前缀，转为小写做前缀匹配
        partial = text[1:].lower()
        commands = self._registry.list_all()

        for cmd in commands:
            # 检查命令名和所有别名，匹配时统一使用命令名作为补全文本
            names = [cmd.name] + cmd.aliases
            for name in names:
                if name.lower().startswith(partial):
                    yield Completion(
                        f"/{cmd.name}",
                        start_position=-len(text),
                        display=f"/{cmd.name}",
                    )
                    break  # 每个命令只产生一个候选，避免名称和别名重复匹配
