"""PermissionConfirmer 单元测试。

覆盖确认交互的各个分支：allow、deny、always_allow、
非法输入重试、无目标路径时不显示 always allow、Ctrl+C 处理。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from minicode.cli.confirm import ConfirmerResult, PermissionConfirmer
from minicode.permissions.models import PermissionDecision, PermissionLevel


def _make_decision(
    level: PermissionLevel = PermissionLevel.CAUTION,
    tool_name: str = "write_file",
    operation: str = "写入文件",
    summary: str = "write_file：创建新文件 test.txt",
    target_paths: list[Path] | None = None,
    reasons: list[str] | None = None,
) -> PermissionDecision:
    """创建一个测试用 PermissionDecision。"""
    return PermissionDecision(
        level=level,
        tool_name=tool_name,
        operation=operation,
        summary=summary,
        target_paths=target_paths or [],
        reasons=reasons or [],
    )


def _make_confirmer(
    prompt_async_return: str | list[str] | None = None,
) -> tuple[PermissionConfirmer, MagicMock]:
    """创建带有 mock PromptSession 的 PermissionConfirmer。

    Args:
        prompt_async_return: 预设返回值。字符串直接返回，列表依次返回。

    Returns:
        (confirmer, mock_session) 元组。
    """
    mock_session = MagicMock()

    if isinstance(prompt_async_return, list):
        iterator = iter(prompt_async_return)

        async def side_effect(*args: object, **kwargs: object) -> str:
            return next(iterator)
    elif prompt_async_return is not None:
        async def side_effect(*args: object, **kwargs: object) -> str:  # type: ignore[misc]
            return prompt_async_return  # type: ignore[return-value]
    else:
        side_effect = None

    mock_session.prompt_async = AsyncMock(side_effect=side_effect)
    console = MagicMock()
    confirmer = PermissionConfirmer(console=console, prompt_session=mock_session)  # type: ignore[arg-type]
    return confirmer, mock_session


class TestPermissionConfirmer:
    """PermissionConfirmer 交互测试。"""

    @pytest.mark.asyncio
    async def test_confirm_allow(self) -> None:
        """输入 y 返回 allow。"""
        confirmer, _ = _make_confirmer("y")
        decision = _make_decision(target_paths=[Path("/workspace/test.txt")])

        result = await confirmer.confirm(decision)

        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_confirm_deny(self) -> None:
        """输入 n 返回 deny。"""
        confirmer, _ = _make_confirmer("n")
        decision = _make_decision()

        result = await confirmer.confirm(decision)
        assert result.action == "deny"

    @pytest.mark.asyncio
    async def test_confirm_always_allow(self) -> None:
        """输入 a 返回 always_allow（有目标路径时）。"""
        confirmer, _ = _make_confirmer("a")
        decision = _make_decision(target_paths=[Path("/workspace/test.txt")])

        result = await confirmer.confirm(decision)
        assert result.action == "always_allow"

    @pytest.mark.asyncio
    async def test_confirm_invalid_input_retry(self) -> None:
        """非法输入后重新提示，输入 y 最终返回 allow。"""
        confirmer, mock = _make_confirmer(["x", "?", "y"])
        decision = _make_decision(target_paths=[Path("/workspace/test.txt")])

        result = await confirmer.confirm(decision)
        assert result.action == "allow"
        # 验证输出了错误提示
        assert confirmer.console.print.called

    @pytest.mark.asyncio
    async def test_confirm_no_target_paths_no_always_allow(self) -> None:
        """没有目标路径时不显示 always allow 选项。"""
        confirmer, _ = _make_confirmer("n")
        decision = _make_decision(target_paths=[])  # 无目标路径

        result = await confirmer.confirm(decision)
        assert result.action == "deny"

    @pytest.mark.asyncio
    async def test_confirm_no_paths_invalid_a_treated_as_invalid(self) -> None:
        """无目标路径时输入 a 被视为无效输入。"""
        confirmer, _ = _make_confirmer(["a", "n"])
        decision = _make_decision(target_paths=[])

        result = await confirmer.confirm(decision)
        assert result.action == "deny"

    @pytest.mark.asyncio
    async def test_confirm_keyboard_interrupt_returns_deny(self) -> None:
        """Ctrl+C 返回 deny。"""
        mock_session = MagicMock()
        mock_session.prompt_async = AsyncMock(side_effect=KeyboardInterrupt())
        console = MagicMock()
        confirmer = PermissionConfirmer(console=console, prompt_session=mock_session)  # type: ignore[arg-type]
        decision = _make_decision()

        result = await confirmer.confirm(decision)
        assert result.action == "deny"

    @pytest.mark.asyncio
    async def test_confirm_eof_error_returns_deny(self) -> None:
        """Ctrl+D 返回 deny。"""
        mock_session = MagicMock()
        mock_session.prompt_async = AsyncMock(side_effect=EOFError())
        console = MagicMock()
        confirmer = PermissionConfirmer(console=console, prompt_session=mock_session)  # type: ignore[arg-type]
        decision = _make_decision()

        result = await confirmer.confirm(decision)
        assert result.action == "deny"

    @pytest.mark.asyncio
    async def test_confirm_dangerous_shows_red_style(self) -> None:
        """dangerous 级别应显示红色样式。"""
        confirmer, _ = _make_confirmer("y")
        decision = _make_decision(
            level=PermissionLevel.DANGEROUS,
            tool_name="delete_file",
            operation="删除文件",
            summary="delete_file：删除 src/main.py",
            target_paths=[Path("/workspace/src/main.py")],
            reasons=["删除操作不可撤销"],
        )

        result = await confirmer.confirm(decision)
        assert result.action == "allow"
        # 验证输出了权限摘要
        assert confirmer.console.print.called

    @pytest.mark.asyncio
    async def test_confirm_yes_alias(self) -> None:
        """验证 yes 应视为 allow。"""
        confirmer, _ = _make_confirmer("yes")
        decision = _make_decision()

        result = await confirmer.confirm(decision)
        assert result.action == "allow"

    @pytest.mark.asyncio
    async def test_confirm_no_alias(self) -> None:
        """验证 no 应视为 deny。"""
        confirmer, _ = _make_confirmer("no")
        decision = _make_decision()

        result = await confirmer.confirm(decision)
        assert result.action == "deny"

    @pytest.mark.asyncio
    async def test_confirm_always_alias(self) -> None:
        """验证 always 应视为 always_allow。"""
        confirmer, _ = _make_confirmer("always")
        decision = _make_decision(target_paths=[Path("/workspace/test.txt")])

        result = await confirmer.confirm(decision)
        assert result.action == "always_allow"

    def test_confirmer_result_model(self) -> None:
        """ConfirmerResult 数据模型。"""
        r = ConfirmerResult(action="allow")
        assert r.action == "allow"
        assert ConfirmerResult(action="deny").action == "deny"
        assert ConfirmerResult(action="always_allow").action == "always_allow"
