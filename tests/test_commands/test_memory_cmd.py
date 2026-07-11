"""/memory 命令测试。

覆盖以下场景：
- /memory list 空/非空
- /memory add 正常/非法名称/空内容
- /memory show 正常/不存在
- /memory delete 正常/不存在/路径穿越
"""
from __future__ import annotations

from pathlib import Path

import pytest

from minicode.commands.base import CommandContext
from minicode.commands.memory_cmd import MemoryCommand
from minicode.config.models import AppConfig, MemoryConfig
from minicode.memory.manager import MemoryManager
from minicode.memory.models import MemoryMetadata, MemoryScope, MemorySource, MemoryType

pytestmark = pytest.mark.asyncio


@pytest.fixture
def command() -> MemoryCommand:
    return MemoryCommand()


@pytest.fixture
def ctx(tmp_path: Path) -> CommandContext:
    """创建指向临时目录的命令上下文。"""
    return CommandContext(
        app_config=None,
        workspace_root=tmp_path,
        session_manager=None,
        renderer=None,
        console=None,
    )


@pytest.fixture
def ctx_with_memory_disabled(tmp_path: Path) -> CommandContext:
    """创建 memory.enabled=False 的命令上下文。"""
    config = AppConfig(memory=MemoryConfig(enabled=False))
    return CommandContext(
        app_config=config,
        workspace_root=tmp_path,
        session_manager=None,
        renderer=None,
        console=None,
    )


@pytest.fixture
def ctx_with_memory_enabled(tmp_path: Path) -> CommandContext:
    """创建 memory.enabled=True 的命令上下文。"""
    config = AppConfig(memory=MemoryConfig(enabled=True))
    return CommandContext(
        app_config=config,
        workspace_root=tmp_path,
        session_manager=None,
        renderer=None,
        console=None,
    )


def _add_test_memory(workspace_root: Path, name: str, content: str) -> None:
    """辅助函数：向临时目录添加一条记忆。"""
    from datetime import UTC, datetime

    manager = MemoryManager(workspace_root)
    now = datetime.now(UTC)
    meta = MemoryMetadata(
        name=name,
        description=content[:40],
        created_at=now,
        updated_at=now,
        source=MemorySource.MANUAL,
        scope=MemoryScope.WORKSPACE,
        confidence=0.9,
        type=MemoryType.PROJECT,
    )
    manager.add(meta, content)


class TestMemoryCommandList:
    """列出记忆测试。"""

    async def test_list_empty(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """空列表返回提示信息。"""
        result = await command.execute("list", ctx)
        assert result.success
        assert "没有保存的记忆" in (result.message or "")

    async def test_list_non_empty(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """非空列表显示所有记忆。"""
        _add_test_memory(ctx.workspace_root, "test-note", "测试内容")
        _add_test_memory(ctx.workspace_root, "another-note", "另一条内容")

        result = await command.execute("list", ctx)
        assert result.success
        assert "test-note" in (result.message or "")
        assert "another-note" in (result.message or "")

    async def test_list_default_no_args(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """无参数时等同于 list。"""
        _add_test_memory(ctx.workspace_root, "default-note", "默认内容")
        result = await command.execute("", ctx)
        assert result.success
        assert "default-note" in (result.message or "")


class TestMemoryCommandAdd:
    """添加记忆测试。"""

    async def test_add_normal(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """正常添加一条记忆。"""
        result = await command.execute('add test-note 这是一条测试记忆', ctx)
        assert result.success
        assert "已记住" in (result.message or "")

        # 验证实际写入
        manager = MemoryManager(ctx.workspace_root)
        memory = manager.get("test-note")
        assert memory is not None
        assert "这是一条测试记忆" in memory.content

    async def test_add_invalid_name(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """非法名称被拒绝。"""
        result = await command.execute("add ../escape-path 内容", ctx)
        assert not result.success
        assert "包含非法字符" in (result.message or "")

    async def test_add_empty_content(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """空内容被拒绝。"""
        result = await command.execute("add test-note", ctx)
        assert not result.success
        assert "content 不能为空" in (result.message or "")

    async def test_add_no_args(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """无参数时提示用法。"""
        result = await command.execute("add", ctx)
        assert not result.success
        assert "用法" in (result.message or "")

    async def test_add_name_with_dots_rejected(
        self, command: MemoryCommand, ctx: CommandContext
    ) -> None:
        """含点的名称被拒绝。"""
        result = await command.execute("add my.note 内容", ctx)
        # name = "my.note"（点不在合法字符集 [a-zA-Z0-9_-] 中）
        assert not result.success
        assert "包含非法字符" in (result.message or "")


class TestMemoryCommandShow:
    """查看记忆测试。"""

    async def test_show_normal(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """正常查看一条记忆。"""
        _add_test_memory(ctx.workspace_root, "show-test", "显示内容")
        result = await command.execute("show show-test", ctx)
        assert result.success
        assert "show-test" in (result.message or "")
        assert "显示内容" in (result.message or "")

    async def test_show_nonexistent(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """不存在的记忆返回错误。"""
        result = await command.execute("show nonexistent", ctx)
        assert not result.success
        assert "未找到" in (result.message or "")

    async def test_show_no_name(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """无参数时提示用法。"""
        result = await command.execute("show", ctx)
        assert not result.success
        assert "用法" in (result.message or "")

    async def test_show_path_traversal_raises(
        self, command: MemoryCommand, ctx: CommandContext
    ) -> None:
        """路径穿越被拒绝。"""
        result = await command.execute("show ../etc/passwd", ctx)
        assert not result.success
        assert "包含非法字符" in (result.message or "")


class TestMemoryCommandDelete:
    """删除记忆测试。"""

    async def test_delete_normal(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """正常删除一条记忆。"""
        _add_test_memory(ctx.workspace_root, "delete-test", "待删除")
        result = await command.execute("delete delete-test", ctx)
        assert result.success
        assert "已删除" in (result.message or "")

        # 验证实际已删除
        manager = MemoryManager(ctx.workspace_root)
        memory = manager.get("delete-test")
        assert memory is None

    async def test_delete_nonexistent(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """不存在的记忆返回提示。"""
        result = await command.execute("delete not-exists", ctx)
        assert not result.success
        assert "不存在" in (result.message or "")

    async def test_delete_path_traversal(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """路径穿越被拒绝。"""
        result = await command.execute("delete ../../outside", ctx)
        assert not result.success
        assert "包含非法字符" in (result.message or "")

    async def test_delete_no_name(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """无参数时提示用法。"""
        result = await command.execute("delete", ctx)
        assert not result.success
        assert "用法" in (result.message or "")


class TestMemoryCommandUnknown:
    """未知子命令测试。"""

    async def test_unknown_subcommand(self, command: MemoryCommand, ctx: CommandContext) -> None:
        """未知子命令返回错误。"""
        result = await command.execute("unknown sub", ctx)
        assert not result.success
        assert "未知的 memory 子命令" in (result.message or "")


class TestMemoryCommandDisabled:
    """memory.enabled=False 时的命令测试。"""

    async def test_list_when_disabled(self, command: MemoryCommand) -> None:
        """memory 禁用时 list 返回禁用提示。"""
        config = AppConfig(memory=MemoryConfig(enabled=False))
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path("/tmp/test"),
            session_manager=None,
            renderer=None,
            console=None,
        )
        result = await command.execute("list", ctx)
        assert not result.success
        assert "记忆系统已禁用" in (result.message or "")

    async def test_add_when_disabled(self, command: MemoryCommand) -> None:
        """memory 禁用时 add 返回禁用提示。"""
        config = AppConfig(memory=MemoryConfig(enabled=False))
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path("/tmp/test"),
            session_manager=None,
            renderer=None,
            console=None,
        )
        result = await command.execute("add test-note 内容", ctx)
        assert not result.success
        assert "记忆系统已禁用" in (result.message or "")

    async def test_show_when_disabled(self, command: MemoryCommand) -> None:
        """memory 禁用时 show 返回禁用提示。"""
        config = AppConfig(memory=MemoryConfig(enabled=False))
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path("/tmp/test"),
            session_manager=None,
            renderer=None,
            console=None,
        )
        result = await command.execute("show test-note", ctx)
        assert not result.success
        assert "记忆系统已禁用" in (result.message or "")

    async def test_delete_when_disabled(self, command: MemoryCommand) -> None:
        """memory 禁用时 delete 返回禁用提示。"""
        config = AppConfig(memory=MemoryConfig(enabled=False))
        ctx = CommandContext(
            app_config=config,
            workspace_root=Path("/tmp/test"),
            session_manager=None,
            renderer=None,
            console=None,
        )
        result = await command.execute("delete test-note", ctx)
        assert not result.success
        assert "记忆系统已禁用" in (result.message or "")


class TestMemoryCommandSensitiveInfo:
    """/memory add 敏感信息检测测试。"""

    async def test_add_rejects_chinese_password(
        self, command: MemoryCommand, ctx_with_memory_enabled: CommandContext
    ) -> None:
        """中文「密码」在 add 中应被拒绝。"""
        result = await command.execute("add test-note 我的密码是 abc123", ctx_with_memory_enabled)
        assert not result.success
        assert "拒绝保存" in (result.message or "")

    async def test_add_rejects_chinese_miyao(
        self, command: MemoryCommand, ctx_with_memory_enabled: CommandContext
    ) -> None:
        """中文「密钥」在 add 中应被拒绝。"""
        result = await command.execute("add my-key 这是项目密钥", ctx_with_memory_enabled)
        assert not result.success
        assert "拒绝保存" in (result.message or "")

    async def test_add_rejects_sk_pattern(
        self, command: MemoryCommand, ctx_with_memory_enabled: CommandContext
    ) -> None:
        """sk-xxx 模式在 add 中应被拒绝。"""
        result = await command.execute(
            "add openai-key 我的 key 是 sk-proj-abc123", ctx_with_memory_enabled
        )
        assert not result.success
        assert "拒绝保存" in (result.message or "")
