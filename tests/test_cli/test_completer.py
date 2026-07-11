"""测试 CommandCompleter 自动补全。"""

from __future__ import annotations

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.completion import CompleteEvent

from minicode.cli.completer import CommandCompleter
from minicode.commands.base import BaseCommand, CommandContext, CommandResult


class _StubCommand(BaseCommand):
    """用于测试的桩命令。"""
    name: str = "testcmd"
    aliases: list[str] = ["tc"]
    description: str = "测试命令"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        return CommandResult(message="ok")


class _AnotherStub(BaseCommand):
    """另一个测试命令。"""
    name: str = "another"
    aliases: list[str] = []
    description: str = "另一个命令"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        return CommandResult(message="ok")


@pytest.fixture
def stub_registry() -> type:
    """返回一个注册了桩命令的 CommandRegistry（通过 clear + register）。"""
    from minicode.commands.registry import CommandRegistry

    CommandRegistry._commands.clear()
    CommandRegistry._aliases.clear()
    CommandRegistry.register(_StubCommand())
    CommandRegistry.register(_AnotherStub())
    return CommandRegistry


def test_no_completions_for_normal_text(stub_registry: type) -> None:
    """不以 / 开头的输入不应触发补全。"""
    completer = CommandCompleter(stub_registry)
    doc = Document(text="hello world")
    completions = list(completer.get_completions(doc, CompleteEvent()))
    assert completions == []


def test_all_commands_on_slash(stub_registry: type) -> None:
    """输入 / 应返回所有命令。"""
    completer = CommandCompleter(stub_registry)
    doc = Document(text="/")
    completions = list(completer.get_completions(doc, CompleteEvent()))
    texts = [c.text for c in completions]
    assert "/testcmd" in texts
    assert "/another" in texts


def test_prefix_filter(stub_registry: type) -> None:
    """输入 /te 应只匹配 testcmd。"""
    completer = CommandCompleter(stub_registry)
    doc = Document(text="/te")
    completions = list(completer.get_completions(doc, CompleteEvent()))
    texts = [c.text for c in completions]
    assert "/testcmd" in texts
    assert "/another" not in texts


def test_alias_matching(stub_registry: type) -> None:
    """输入 /tc 应通过别名匹配到 testcmd。"""
    completer = CommandCompleter(stub_registry)
    doc = Document(text="/tc")
    completions = list(completer.get_completions(doc, CompleteEvent()))
    texts = [c.text for c in completions]
    assert "/testcmd" in texts


def test_no_match_returns_empty(stub_registry: type) -> None:
    """不匹配任何命令时应返回空列表。"""
    completer = CommandCompleter(stub_registry)
    doc = Document(text="/xyz")
    completions = list(completer.get_completions(doc, CompleteEvent()))
    assert completions == []


def test_case_insensitive_matching(stub_registry: type) -> None:
    """前缀匹配应不区分大小写。"""
    completer = CommandCompleter(stub_registry)
    doc = Document(text="/TE")
    completions = list(completer.get_completions(doc, CompleteEvent()))
    texts = [c.text for c in completions]
    assert "/testcmd" in texts


def test_start_position_correct(stub_registry: type) -> None:
    """Completion.start_position 应等于 -len(document.text)，确保替换整个输入。"""
    completer = CommandCompleter(stub_registry)
    doc = Document(text="/te")
    completions = list(completer.get_completions(doc, CompleteEvent()))
    for c in completions:
        assert c.start_position == -3
