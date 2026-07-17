"""斜杠命令的抽象基类与数据模型。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    pass


class CommandResult(BaseModel):
    """命令执行结果。

    由 BaseCommand.execute() 返回，ChatApp 根据此结果决定后续行为。
    """

    should_exit: bool = False
    """是否退出程序（仅 /quit 为 True）。"""
    message: str | None = None
    """显示给用户的文本消息。None 表示无输出。"""
    success: bool = True
    """命令是否执行成功。失败时 message 包含错误描述。"""
    history_changed: bool = False
    """命令是否修改了 AgentLoop 历史；ChatApp 据此统一保存会话。"""


class CommandContext(BaseModel):
    """命令执行时注入的上下文。

    包含命令所需的所有外部依赖，通过依赖注入方式传入，
    确保命令可测试、不直接访问全局状态。

    使用 arbitrary_types_allowed 以支持 Rich Console 等非 Pydantic 类型。
    """

    model_config = {"arbitrary_types_allowed": True}

    app_config: Any
    """当前应用配置（只读）。"""
    workspace_root: Path
    """工作区根路径。"""
    session_manager: Any
    """会话管理器实例。"""
    agent_loop: Any = None
    """当前 AgentLoop（首次对话前为 None）。"""
    renderer: Any
    """流式渲染器。"""
    console: Any
    """Rich Console 实例，用于交互式 UI 组件。"""

    notify_session_created: Any = None
    """/clear 新会话回调 (async session -> None)，保持 ChatApp._current_session 同步。"""
    notify_session_switched: Any = None
    """/session switch 回调 (async session -> None)，保持 ChatApp._current_session 同步。"""
    notify_session_deleted: Any = None
    """/session delete 回调 (async session_id -> None)，删除当前会话时重置 ChatApp 状态。"""


class BaseCommand(ABC):
    """斜杠命令抽象基类。

    所有命令必须继承此类并实现 execute 方法。
    命令通过 CommandRegistry.register() 注册。
    """

    name: str = ""
    """命令主名称，不含斜杠前缀。如 'session' 对应 '/session'。"""
    aliases: list[str] = []
    """命令别名列表，如 ['s'] 对应 '/s'。"""
    description: str = ""
    """命令简述，用于 /help 列表展示。"""
    usage: str = ""
    """命令用法示例，如 '/session switch <id>'。"""

    @abstractmethod
    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """执行命令。

        Args:
            args: 命令参数（不含命令名本身）。
                  如 '/session switch abc' → 'switch abc'
            ctx: 命令执行上下文。

        Returns:
            CommandResult 描述执行结果。
        """
        ...
