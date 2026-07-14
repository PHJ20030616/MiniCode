"""/session 命令 —— 会话列表、切换、删除及交互式键盘选择。"""

from __future__ import annotations

import asyncio
import re
import shutil
from contextlib import suppress

from prompt_toolkit.input import create_input
from prompt_toolkit.keys import Keys
from rich.live import Live
from rich.table import Table

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.utils.log import get_logger

logger = get_logger(__name__)


def _get_display_summary(s: dict) -> str:
    """获取会话的显示概要（向后兼容）。

    优先使用 summary 字段，不存在时降级使用 name，两者都为空时返回固定文本。

    Args:
        s: 会话摘要字典。

    Returns:
        显示用的概要字符串。
    """
    summary: str | None = s.get("summary")  # type: ignore[type-arg]
    if summary:
        return summary
    name: str | None = s.get("name")  # type: ignore[type-arg]
    if name:
        return name
    return "（无概要）"


def _compute_scroll_offset(
    selected_idx: int,
    scroll_offset: int,
    visible_count: int,
    total_count: int,
) -> int:
    """根据选中索引计算新的滚动偏移量。

    Args:
        selected_idx: 当前选中的索引。
        scroll_offset: 当前窗口起始偏移。
        visible_count: 窗口可见条目数。
        total_count: 总条目数。

    Returns:
        计算后的新滚动偏移量。
    """
    if total_count <= 0 or visible_count <= 0:
        return 0

    max_offset = max(0, total_count - visible_count)
    if selected_idx < scroll_offset:
        scroll_offset = selected_idx
    elif selected_idx >= scroll_offset + visible_count:
        scroll_offset = selected_idx - visible_count + 1
    return max(0, min(scroll_offset, max_offset))


class SessionCommand(BaseCommand):
    """管理 MiniCode 会话：列表、切换、删除。

    用法：
        /session              → 交互式方向键选择会话
        /session list         → 列出所有会话
        /session switch <id>  → 切换到指定会话
        /session delete <id>  → 删除指定会话
    """

    name: str = "session"
    aliases: list[str] = ["s"]
    description: str = "管理会话（列表/切换/删除）"
    usage: str = "/session [list|switch <id>|delete <id>]"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """根据子命令分发到对应处理逻辑。

        Args:
            args: 子命令和参数。
            ctx: 命令执行上下文。

        Returns:
            CommandResult 描述执行结果。
        """
        args = args.strip()

        # 无参数 → 交互式选择
        if not args:
            return await self._handle_interactive(ctx)

        # 解析子命令
        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower()
        sub_args = parts[1] if len(parts) > 1 else ""

        if subcmd == "list":
            return await self._handle_list(ctx)
        elif subcmd == "switch":
            return await self._handle_switch(sub_args, ctx)
        elif subcmd == "delete":
            return await self._handle_delete(sub_args, ctx)
        else:
            return CommandResult(
                success=False,
                message=(
                    f"未知的 session 子命令：{subcmd}。\n"
                    f"可用子命令：list、switch <id>、delete <id>\n"
                    f"直接输入 /session 可交互式选择会话。"
                ),
            )

    # ─── 子命令处理 ─────────────────────────────────────────

    async def _handle_list(self, ctx: CommandContext) -> CommandResult:
        """列出所有会话摘要。"""
        sessions = ctx.session_manager.list_sessions()

        if not sessions:
            return CommandResult(message="没有保存的会话。")

        lines: list[str] = []
        lines.append(f"会话列表（共 {len(sessions)} 条）：")
        lines.append("")

        for i, s in enumerate(sessions, 1):
            summary = _get_display_summary(s)
            lines.append(f"  {i:2d}. {summary}")

        return CommandResult(message="\n".join(lines))

    async def _handle_switch(self, target_id: str, ctx: CommandContext) -> CommandResult:
        """切换到指定会话。"""
        if not target_id:
            return CommandResult(
                success=False,
                message="用法：/session switch <会话ID>\n"
                        "提示：输入 /session list 查看所有会话，"
                        "或输入 /session 交互式选择。",
            )

        return await self._do_switch(target_id, ctx)

    async def _handle_delete(self, target_id: str, ctx: CommandContext) -> CommandResult:
        """删除指定会话。"""
        if not target_id:
            return CommandResult(
                success=False,
                message="用法：/session delete <会话ID>\n"
                        "提示：输入 /session list 查看所有会话。",
            )

        # 解析 ID（支持前缀匹配）
        resolved_id, error = self._resolve_session_id(target_id, ctx)
        if error:
            return CommandResult(success=False, message=error)
        assert resolved_id is not None  # 此时 error 为 None，resolved_id 必不为 None

        deleted = ctx.session_manager.delete(resolved_id)
        if not deleted:
            return CommandResult(
                success=False,
                message=f"会话 {resolved_id[:8]} 不存在，无需删除。",
            )

        # 通知 ChatApp 更新当前会话状态
        if ctx.notify_session_deleted is not None:
            await ctx.notify_session_deleted(resolved_id)

        return CommandResult(
            message=f"会话 {resolved_id[:8]} 已删除。",
        )

    # ─── 交互式选择器 ───────────────────────────────────────

    async def _handle_interactive(self, ctx: CommandContext) -> CommandResult:
        """启动交互式方向键会话选择器。"""
        sessions = ctx.session_manager.list_sessions()

        if not sessions:
            return CommandResult(message="没有保存的会话。")

        selected_id = await self._interactive_select(sessions, ctx)

        if selected_id is None:
            return CommandResult(message="已取消。")

        return await self._do_switch(selected_id, ctx)

    async def _interactive_select(
        self, sessions: list[dict], ctx: CommandContext
    ) -> str | None:
        """使用 Rich Live + prompt_toolkit 输入实现方向键交互式选择。

        在终端内渲染会话列表，支持 ↑↓ 导航、Enter 确认、Esc 取消。
        当会话数超出终端高度时，视口自动跟随选中项滚动。

        Args:
            sessions: 会话摘要列表。
            ctx: 命令执行上下文。

        Returns:
            选中的 session_id，取消时返回 None。
        """
        total = len(sessions)
        selected_idx = 0
        scroll_offset = 0
        done: asyncio.Event = asyncio.Event()
        result: str | None = None

        def _clamp_scroll_offset() -> None:
            """根据终端高度调整 scroll_offset，确保选中项在可见区域内。"""
            nonlocal scroll_offset
            terminal_lines = shutil.get_terminal_size().lines
            # 表头约 3 行 + 上下折叠提示各 1 行 + 余量 1 行
            visible_count = max(5, terminal_lines - 6)
            scroll_offset = _compute_scroll_offset(
                selected_idx, scroll_offset, visible_count, total,
            )

        def build_table() -> Table:
            """构建当前选择状态和视口位置的 Rich Table。"""
            nonlocal scroll_offset
            _clamp_scroll_offset()

            terminal_lines = shutil.get_terminal_size().lines
            visible_count = max(5, terminal_lines - 6)
            end = min(scroll_offset + visible_count, total)

            table = Table(
                title=(
                    f"会话历史（↑↓ 选择，Enter 加载，Esc 取消）"
                    f" — {scroll_offset + 1}-{end} / 共 {total} 条"
                ),
                title_style="bold",
                show_header=True,
                header_style="bold",
            )
            table.add_column("#", style="dim", width=4)
            table.add_column("概要", style="cyan")

            # 上方折叠提示
            if scroll_offset > 0:
                table.add_row(
                    "…", f"以上 {scroll_offset} 条",
                    style="dim",
                )

            for i, s in enumerate(sessions[scroll_offset:end]):
                actual_idx = scroll_offset + i
                prefix = ">" if actual_idx == selected_idx else " "
                style = "reverse" if actual_idx == selected_idx else ""
                summary = _get_display_summary(s)

                if style:
                    table.add_row(
                        f"{prefix} {actual_idx + 1}",
                        summary,
                        style=style,
                    )
                else:
                    table.add_row(
                        f"  {actual_idx + 1}",
                        summary,
                    )

            # 下方折叠提示
            remaining = total - end
            if remaining > 0:
                table.add_row(
                    "…", f"以下 {remaining} 条",
                    style="dim",
                )

            return table

        async def listen_keys() -> None:
            """监听键盘事件，更新选中索引和视口偏移。"""
            nonlocal selected_idx, result, scroll_offset

            input_obj = create_input()
            loop = asyncio.get_running_loop()
            try:
                with input_obj.raw_mode():
                    while not done.is_set():
                        keys = await loop.run_in_executor(None, input_obj.read_keys)
                        for key_press in keys:
                            if key_press.key == Keys.Up:
                                selected_idx = (selected_idx - 1) % total
                                _clamp_scroll_offset()
                            elif key_press.key == Keys.Down:
                                selected_idx = (selected_idx + 1) % total
                                _clamp_scroll_offset()
                            elif key_press.key in (Keys.Enter, Keys.ControlM):
                                result = sessions[selected_idx].get("id")
                                done.set()
                                return
                            elif (
                                key_press.key == Keys.Escape
                                or key_press.key == Keys.ControlC
                            ):
                                done.set()
                                return
            finally:
                done.set()

        # 启动键盘监听任务
        listener_task = asyncio.create_task(listen_keys())

        with Live(
            build_table(),
            console=ctx.console,
            refresh_per_second=10,
            transient=True,
        ) as live:
            while not done.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(done.wait(), timeout=0.1)
                live.update(build_table())

        # 清理
        listener_task.cancel()
        with suppress(asyncio.CancelledError):
            await listener_task

        return result

    # ─── 内部方法 ───────────────────────────────────────────

    async def _do_switch(self, target_id: str, ctx: CommandContext) -> CommandResult:
        """执行会话切换的实际逻辑。

        Args:
            target_id: 目标会话 ID（支持完整 32 位 ID 或唯一前缀）。
            ctx: 命令执行上下文。

        Returns:
            CommandResult 描述切换结果。
        """
        # 解析 ID（支持前缀匹配）
        resolved_id, error = self._resolve_session_id(target_id, ctx)
        if error:
            return CommandResult(success=False, message=error)
        assert resolved_id is not None  # 此时 error 为 None，resolved_id 必不为 None

        # 加载目标会话
        target = ctx.session_manager.load(resolved_id)
        if target is None:
            return CommandResult(
                success=False,
                message=f"会话 {resolved_id[:8]} 不存在。",
            )

        # 替换 AgentLoop 消息
        if ctx.agent_loop is not None:
            ctx.agent_loop.messages.clear()
            ctx.agent_loop.messages.extend(target.messages)

        # 通知 ChatApp 更新 _current_session
        if ctx.notify_session_switched is not None:
            await ctx.notify_session_switched(target)

        logger.info(
            "会话已切换",
            session_id=resolved_id[:8],
            message_count=target.message_count,
        )

        return CommandResult(
            message=(
                f"已切换到会话：{target.name}\n"
                f"  ID     : {resolved_id[:8]}...\n"
                f"  消息数 : {target.message_count}\n"
                f"  模型   : {target.model}"
            ),
        )

    def _resolve_session_id(
        self, raw_id: str, ctx: CommandContext
    ) -> tuple[str | None, str | None]:
        """解析会话 ID，支持完整 32 位 hex ID 或唯一前缀匹配。

        Args:
            raw_id: 用户输入的 ID（可能是完整 ID 或前缀）。
            ctx: 命令执行上下文。

        Returns:
            (resolved_id, error_message) 二元组。
            成功时 error_message 为 None，失败时 resolved_id 为 None。
        """
        # 完整 32 位 hex ID → 直接使用
        if re.fullmatch(r"[0-9a-f]{32}", raw_id):
            return raw_id, None

        # 尝试前缀匹配
        sessions = ctx.session_manager.list_sessions()
        matches = [s["id"] for s in sessions if s["id"].startswith(raw_id)]

        if not matches:
            return None, f"未找到以 '{raw_id}' 开头的会话。"
        if len(matches) > 1:
            ids = ", ".join(m[:8] for m in matches)
            return None, f"前缀 '{raw_id}' 匹配到多个会话：{{{ids}}}，请输入更长的前缀。"
        return matches[0], None
