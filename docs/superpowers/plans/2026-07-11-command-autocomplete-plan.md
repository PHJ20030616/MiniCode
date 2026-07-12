# 命令自动补全系统 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 CLI 中输入 `/` 后显示可用命令的下拉候选列表，支持前缀过滤和别名匹配。

**Architecture:** 利用 `prompt_toolkit` 内置 `Completer` 接口，新增 `CommandCompleter` 类（单文件），与现有 `CommandRegistry` 集成。只显示命令名，不显示描述。

**Tech Stack:** Python 3.12+, prompt_toolkit, pytest, pytest-asyncio

## Global Constraints

- 候选列表只显示命令名（不含描述）
- 匹配规则：命令名或别名的**前缀匹配**（不区分大小写）
- Tab 键轮换候选，上下键移动选择，回车确认
- 输入不以 `/` 开头时不激活补全
- 完整实现代码必须含有必要的中文注释
- 遵循项目已有的测试结构（tests/ 镜像 src/）

---

### Task 1: 实现 CommandCompleter

**Files:**
- Create: `src/minicode/cli/completer.py`
- Test: `tests/test_cli/test_completer.py`

**Interfaces:**
- Consumes: `CommandRegistry`（类本身，通过类方法 `list_all()` 和 `find()`）
- Produces: `CommandCompleter` 类，构造参数为 `CommandRegistry` 类对象

- [ ] **Step 1: 编写测试文件**

```python
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
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
uv run pytest tests/test_cli/test_completer.py -v
```

Expected: FAIL — ModuleNotFoundError for `minicode.cli.completer`

- [ ] **Step 3: 实现 CommandCompleter**

```python
"""斜杠命令自动补全器。

提供基于 prompt_toolkit Completer 接口的命令名补全。
当用户输入以 '/' 开头时，从 CommandRegistry 获取所有已注册命令，
通过前缀匹配筛选候选，展示给用户选择。
"""

from __future__ import annotations

from collections.abc import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document


class CommandCompleter(Completer):
    """为 '/' 开头的输入提供命令补全。

    只在输入以 '/' 开头时激活，根据已输入的内容做前缀匹配，
    返回匹配的命令名（包括别名）作为补全候选。

    Args:
        registry: CommandRegistry 类（使用其类方法 list_all）。
    """

    def __init__(self, registry: type) -> None:
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
            # 检查命令名和所有别名
            names = [cmd.name] + cmd.aliases
            for name in names:
                if name.startswith(partial):
                    yield Completion(
                        f"/{name}",
                        start_position=-len(text),
                        display=f"/{name}",
                    )
```

- [ ] **Step 4: 运行测试，验证通过**

```bash
uv run pytest tests/test_cli/test_completer.py -v
```

Expected: ALL PASS

- [ ] **Step 5: 运行 lint 和 type check**

```bash
uv run ruff check src/minicode/cli/completer.py
uv run mypy src/minicode/cli/completer.py
```

Expected: clean

- [ ] **Step 6: 提交**

```bash
git add tests/test_cli/test_completer.py src/minicode/cli/completer.py
git commit -m "feat: 实现 CommandCompleter 命令自动补全器"
```

---

### Task 2: 将 CommandCompleter 注入到 ChatApp

**Files:**
- Modify: `src/minicode/cli/app.py`

**Interfaces:**
- Consumes: `CommandCompleter` 类（来自 Task 1），`CommandRegistry` 类
- Produces: 修改 `ChatApp.session` 属性，为 `PromptSession` 传入 `completer` 参数

- [ ] **Step 1: 验证现有测试全部通过**

```bash
uv run pytest tests/test_cli/test_app.py -v
```

Expected: ALL PASS（基线确认）

- [ ] **Step 2: 在 app.py 顶部添加导入**

在 `src/minicode/cli/app.py` 的导入区域追加：

```python
from minicode.cli.completer import CommandCompleter
from minicode.commands.registry import CommandRegistry
```

- [ ] **Step 3: 修改 ChatApp.session 属性**

将：

```python
@property
def session(self) -> PromptSession[Any]:
    \"\"\"延迟初始化的 PromptSession。\"\"\"
    if self._prompt_session is None:
        self._prompt_session = PromptSession()
    return self._prompt_session
```

改为：

```python
@property
def session(self) -> PromptSession[Any]:
    \"\"\"延迟初始化的 PromptSession，带有命令自动补全。\"\"\"
    if self._prompt_session is None:
        completer = CommandCompleter(CommandRegistry)
        self._prompt_session = PromptSession(
            completer=completer,
            complete_while_typing=True,
        )
    return self._prompt_session
```

- [ ] **Step 4: 验证现有测试仍全部通过**

```bash
uv run pytest tests/test_cli/test_app.py -v
```

Expected: ALL PASS

- [ ] **Step 5: 运行完整测试套件**

```bash
uv run pytest -v
```

Expected: ALL PASS

- [ ] **Step 6: 运行 lint 和 type check**

```bash
uv run ruff check .
uv run mypy src/minicode
```

Expected: clean

- [ ] **Step 7: 提交**

```bash
git add src/minicode/cli/app.py
git commit -m "feat: 为 PromptSession 注入 CommandCompleter 实现命令自动补全"
```

---

### 验证清单

1. 输入 `/` 后出现所有命令候选列表
2. 输入 `/c` 后列表过滤为 `clear`, `config`（以及它们的别名）
3. 输入 `/se` 后只显示 `session`
4. 输入 `/q` 通过别名匹配到 `quit`
5. 普通文本输入不触发补全
6. 无匹配时候选列表消失
7. Tab/上下键/回车交互正常
8. 全部现有测试通过
9. ruff lint 和 mypy type check 通过
