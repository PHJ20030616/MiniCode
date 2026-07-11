"""/memory 命令 —— 管理 MiniCode 记忆系统。

用法：
    /memory list          → 列出所有记忆
    /memory add <name> <content>  → 添加记忆
    /memory show <name>   → 查看记忆详情
    /memory delete <name> → 删除指定记忆
"""
from __future__ import annotations

from datetime import UTC, datetime

from minicode.commands.base import BaseCommand, CommandContext, CommandResult
from minicode.memory.manager import MemoryManager
from minicode.memory.models import MemoryMetadata, MemoryScope, MemorySource, MemoryType
from minicode.tools.remember import _check_sensitive


class MemoryCommand(BaseCommand):
    """管理 MiniCode 记忆系统：列表、添加、查看、删除。"""

    name: str = "memory"
    aliases: list[str] = ["m"]
    description: str = "管理记忆（列表/添加/查看/删除）"
    usage: str = "/memory [list|add <name> <content>|show <name>|delete <name>]"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """根据子命令分发到对应处理逻辑。

        Args:
            args: 子命令和参数。
            ctx: 命令执行上下文。

        Returns:
            CommandResult 描述执行结果。
        """
        # memory 禁用时所有子命令返回禁用提示
        app_config = ctx.app_config
        if app_config is not None and not app_config.memory.enabled:
            return CommandResult(
                success=False,
                message="记忆系统已禁用，请在配置中启用后再试。",
            )

        args = args.strip()

        if not args:
            return await self._handle_list(ctx)

        parts = args.split(maxsplit=1)
        subcmd = parts[0].lower()
        sub_args = parts[1] if len(parts) > 1 else ""

        if subcmd == "list":
            return await self._handle_list(ctx)
        elif subcmd == "add":
            return await self._handle_add(sub_args, ctx)
        elif subcmd == "show":
            return await self._handle_show(sub_args, ctx)
        elif subcmd == "delete":
            return await self._handle_delete(sub_args, ctx)
        else:
            return CommandResult(
                success=False,
                message=(
                    f"未知的 memory 子命令：{subcmd}。\n"
                    f"可用子命令：list、add <name> <content>、show <name>、delete <name>\n"
                    f"输入 /help 查看帮助。"
                ),
            )

    # ─── 子命令处理 ─────────────────────────────────────────

    async def _handle_list(self, ctx: CommandContext) -> CommandResult:
        """列出所有记忆。"""
        manager = MemoryManager(ctx.workspace_root)
        entries = manager.list_memories()

        if not entries:
            return CommandResult(message="没有保存的记忆。")

        lines: list[str] = ["记忆列表：", ""]
        for i, entry in enumerate(entries, 1):
            name = entry.get("name", "?")
            desc = entry.get("description", "")
            lines.append(f"  {i:2d}. [{name}] — {desc}")

        return CommandResult(message="\n".join(lines))

    async def _handle_add(self, args: str, ctx: CommandContext) -> CommandResult:
        """添加一条新记忆。

        格式：/memory add <name> <content>
        """
        if not args:
            return CommandResult(
                success=False,
                message="用法：/memory add <name> <content>\n"
                "例如：/memory add reply-language 用户喜欢用中文回答",
            )

        # 解析 name 和 content：第一个空格分隔的单词为 name，其余为 content
        parts = args.split(maxsplit=1)
        name = parts[0].strip()
        content = parts[1].strip() if len(parts) > 1 else ""

        if not content:
            return CommandResult(
                success=False,
                message="用法：/memory add <name> <content>\n"
                "content 不能为空。",
            )

        # 验证 name
        try:
            MemoryManager._validate_memory_name(name)
        except ValueError as e:
            return CommandResult(success=False, message=str(e))

        # 安全检查：拒绝敏感信息
        error_msg = _check_sensitive(name=name, content=content, description=content[:80])
        if error_msg:
            return CommandResult(success=False, message=error_msg)

        now = datetime.now(UTC)
        metadata = MemoryMetadata(
            name=name,
            description=content[:80],
            created_at=now,
            updated_at=now,
            source=MemorySource.MANUAL,
            scope=MemoryScope.WORKSPACE,
            confidence=0.9,
            type=MemoryType.PROJECT,
        )

        try:
            manager = MemoryManager(ctx.workspace_root)
            manager.add(metadata, content)
            # 刷新当前 AgentLoop 的系统提示词
            if ctx.agent_loop is not None:
                ctx.agent_loop.reload_memory()
            return CommandResult(message=f"已记住：{content}")
        except Exception as e:
            return CommandResult(success=False, message=f"保存记忆失败：{e}")

    async def _handle_show(self, name: str, ctx: CommandContext) -> CommandResult:
        """查看指定记忆的详细信息。"""
        if not name:
            return CommandResult(
                success=False,
                message="用法：/memory show <name>\n"
                "例如：/memory show reply-language",
            )

        try:
            manager = MemoryManager(ctx.workspace_root)
            memory = manager.get(name)
        except ValueError as e:
            return CommandResult(success=False, message=str(e))

        if memory is None:
            return CommandResult(
                success=False,
                message=f"未找到记忆「{name}」。\n"
                f"输入 /memory list 查看所有记忆。",
            )

        meta = memory.metadata
        lines = [
            f"名称：{meta.name}",
            f"描述：{meta.description or '（无描述）'}",
            f"作用域：{meta.scope.value}",
            f"类型：{meta.type.value}",
            f"来源：{meta.source.value}",
            f"置信度：{meta.confidence}",
            f"创建时间：{meta.created_at.isoformat()}",
            f"更新时间：{meta.updated_at.isoformat()}",
            "",
            f"内容：{memory.content}",
        ]
        return CommandResult(message="\n".join(lines))

    async def _handle_delete(self, name: str, ctx: CommandContext) -> CommandResult:
        """删除指定记忆。"""
        if not name:
            return CommandResult(
                success=False,
                message="用法：/memory delete <name>\n"
                "例如：/memory delete reply-language",
            )

        try:
            manager = MemoryManager(ctx.workspace_root)
            deleted = manager.delete(name)
        except ValueError as e:
            return CommandResult(success=False, message=str(e))

        if not deleted:
            return CommandResult(
                success=False,
                message=f"记忆「{name}」不存在，无需删除。",
            )

        # 刷新当前 AgentLoop 的系统提示词
        if ctx.agent_loop is not None:
            ctx.agent_loop.reload_memory()

        return CommandResult(message=f"记忆「{name}」已删除。")
