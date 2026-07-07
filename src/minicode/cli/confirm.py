"""权限确认交互模块。

提供 PermissionConfirmer 类，在 caution/dangerous 级别
工具调用时显示权限摘要并等待用户确认。

支持三种选择：
  - [y] allow：允许本次执行
  - [n] deny：拒绝本次执行
  - [a] always allow：始终允许此路径模式（仅在有目标路径时显示）
"""

from __future__ import annotations

from typing import Literal

from prompt_toolkit import PromptSession
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from minicode.permissions.models import PermissionDecision, PermissionLevel


class ConfirmerResult(BaseModel):
    """用户确认结果。

    Attributes:
        action: 用户选择的操作
    """

    action: Literal["allow", "deny", "always_allow"]


class PermissionConfirmer:
    """权限确认交互处理器。

    在非 trust mode 下，当工具权限为 caution 或 dangerous 时，
    显示权限摘要并询问用户是否允许执行。

    用法::

        confirmer = PermissionConfirmer(console=console)
        result = await confirmer.confirm(decision)
    """

    def __init__(
        self,
        console: Console,
        prompt_session: PromptSession[str] | None = None,
    ) -> None:
        """初始化 PermissionConfirmer。

        Args:
            console: Rich Console 实例，用于输出权限摘要。
            prompt_session: prompt_toolkit PromptSession 实例，
                用于异步获取用户输入。未提供时在首次需要时自动创建。
        """
        self.console = console
        self._prompt_session = prompt_session

    @property
    def _session(self) -> PromptSession[str]:
        """延迟初始化的 PromptSession。"""
        if self._prompt_session is None:
            self._prompt_session = PromptSession()
        return self._prompt_session

    async def confirm(self, decision: PermissionDecision) -> ConfirmerResult:
        """显示权限确认信息并等待用户选择。

        显示内容包括：
        - 工具名
        - 风险级别（caution 黄色 / dangerous 红色）
        - 操作摘要
        - 目标路径
        - 判断原因

        Args:
            decision: 权限判断结果。

        Returns:
            用户选择结果 ConfirmerResult。
        """
        self._show_permission_summary(decision)
        return await self._prompt_user(decision)

    def _show_permission_summary(self, decision: PermissionDecision) -> None:
        """在控制台显示权限摘要面板。"""
        # 风险级别样式
        level_style_map = {
            PermissionLevel.SAFE: "green",
            PermissionLevel.CAUTION: "yellow",
            PermissionLevel.DANGEROUS: "red",
            PermissionLevel.DENY: "bold red",
        }
        level_label_map = {
            PermissionLevel.CAUTION: "需确认（低风险）",
            PermissionLevel.DANGEROUS: "需确认（高风险）",
        }

        style = level_style_map.get(decision.level, "")
        label = level_label_map.get(decision.level, decision.level.value)

        # 构建摘要表格
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("字段", style="bold", width=12)
        table.add_column("值")

        table.add_row("工具", decision.tool_name)
        table.add_row("风险", Text(label, style=style))
        table.add_row("操作", decision.operation)
        table.add_row("摘要", decision.summary)

        if decision.target_paths:
            paths_str = "\n".join(str(p) for p in decision.target_paths)
            table.add_row("目标路径", paths_str)

        if decision.reasons:
            reasons_str = "\n".join(f"• {r}" for r in decision.reasons)
            table.add_row("原因", reasons_str)

        panel = Panel(
            table,
            title="权限确认",
            border_style="yellow",
        )
        self.console.print(panel)

    async def _prompt_user(self, decision: PermissionDecision) -> ConfirmerResult:
        """等待用户输入并返回选择结果。

        有目标路径时显示 [y/n/a] 三个选项，
        无目标路径时仅显示 [y/n] 两个选项。

        Args:
            decision: 权限判断结果。

        Returns:
            用户选择结果。
        """
        has_path = bool(decision.target_paths)

        while True:
            if has_path:
                prompt_text = "请选择 [y] 允许  [n] 拒绝  [a] 始终允许: "
            else:
                prompt_text = "请选择 [y] 允许  [n] 拒绝: "

            try:
                user_input = await self._session.prompt_async(prompt_text)
                user_input = user_input.strip().lower()
            except (EOFError, KeyboardInterrupt):
                # Ctrl+C/D 视为拒绝
                return ConfirmerResult(action="deny")

            if user_input in ("y", "yes"):
                return ConfirmerResult(action="allow")
            elif user_input in ("n", "no"):
                return ConfirmerResult(action="deny")
            elif user_input in ("a", "always") and has_path:
                return ConfirmerResult(action="always_allow")
            else:
                self.console.print(Text("无效输入，请重新选择。", style="red"))
