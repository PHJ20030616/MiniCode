# Task 3.2 Permission Confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在工具执行前接入权限确认交互，支持 allow、deny、always allow this pattern，并把 always allow 规则保存到 workspace 的 `.minicode/permissions.json`。

**Architecture:** `checker.py` 继续只负责参数级风险判定；新增 `PermissionStore` 负责持久化和匹配已授权路径 pattern；新增 CLI confirmer 负责用户交互；`AgentLoop._execute_tools()` 成为统一执行闸口，先检查权限，再决定是否调用 `ToolRegistry.execute_tool()`。`deny` 永远不可绕过，`always allow` 只匹配同一 workspace 内、同一工具、同一路径范围。

**Tech Stack:** Python 3.12, Pydantic, prompt_toolkit, Rich, pytest, pytest-asyncio, pytest-mock.

---

## File Structure

- Create: `src/minicode/permissions/store.py`
  - 定义 `PermissionRule` 和 `PermissionStore`。
  - 读写 `<workspace>/.minicode/permissions.json`。
  - 提供 `is_allowed(decision, workspace_root)` 和 `add_rule_from_decision(decision, workspace_root)`。
- Modify: `src/minicode/permissions/__init__.py`
  - 导出 `PermissionRule`、`PermissionStore`。
- Create: `src/minicode/cli/confirm.py`
  - 定义 `ConfirmationChoice` 和 `PermissionConfirmer`。
  - 用 `PromptSession.prompt_async()` 支持 `[y] allow`、`[n] deny`、`[a] always allow this pattern`。
- Modify: `src/minicode/agent/loop.py`
  - 在 `_execute_tools()` 中调用 `check_permission()`。
  - 对 safe、deny、store 命中、trust_mode、用户 allow/deny/always 分支分别处理。
  - 拒绝时追加 `ToolMessage`，不执行真实工具。
- Modify: `src/minicode/cli/app.py`
  - 创建 `PermissionStore(self.workspace_root)` 和 `PermissionConfirmer(...)`，注入 `AgentLoop`。
- Create: `tests/test_permissions/test_store.py`
  - 覆盖 JSON 存储、同工具匹配、跨工具不匹配、越界/敏感/deny 不匹配。
- Create: `tests/test_cli/test_confirm.py`
  - 覆盖 y/n/a、大小写、空输入重试、非法输入重试。
- Modify: `tests/test_agent/test_loop.py`
  - 覆盖拒绝不执行、always allow 落盘并执行、store 命中跳过确认、trust_mode 跳过确认但 deny 仍拒绝。

---

### Task 1: Permission Store

**Files:**
- Create: `src/minicode/permissions/store.py`
- Modify: `src/minicode/permissions/__init__.py`
- Test: `tests/test_permissions/test_store.py`

- [ ] **Step 1: Write failing tests for persistence and schema**

Add `tests/test_permissions/test_store.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from minicode.permissions.checker import check_permission
from minicode.permissions.models import PermissionLevel
from minicode.permissions.store import PermissionStore


def test_add_rule_writes_permissions_json(tmp_path: Path) -> None:
    decision = check_permission(
        "write_file",
        {"file_path": "src/new.py", "content": "print('x')"},
        tmp_path,
    )
    store = PermissionStore(tmp_path)

    rule = store.add_rule_from_decision(decision, tmp_path)

    data = json.loads((tmp_path / ".minicode" / "permissions.json").read_text())
    assert data["rules"][0]["tool_name"] == "write_file"
    assert data["rules"][0]["path_pattern"] == "src/new.py"
    assert data["rules"][0]["created_at"]
    assert rule.tool_name == "write_file"
    assert rule.path_pattern == "src/new.py"


def test_load_existing_permissions_json(tmp_path: Path) -> None:
    permissions_dir = tmp_path / ".minicode"
    permissions_dir.mkdir()
    (permissions_dir / "permissions.json").write_text(
        json.dumps({
            "rules": [{
                "tool_name": "write_file",
                "path_pattern": "src/*.py",
                "created_at": "2026-07-07T00:00:00Z",
            }]
        }),
        encoding="utf-8",
    )

    store = PermissionStore(tmp_path)

    assert len(store.rules) == 1
    assert store.rules[0].tool_name == "write_file"
    assert store.rules[0].path_pattern == "src/*.py"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_permissions/test_store.py -v
```

Expected: fails because `minicode.permissions.store` does not exist.

- [ ] **Step 3: Implement store model and JSON read/write**

Create `src/minicode/permissions/store.py` with this shape:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path

from pydantic import BaseModel, Field

from minicode.permissions.models import PermissionDecision
from minicode.tools.path_safety import is_sensitive_file, is_within_workspace


class PermissionRule(BaseModel):
    tool_name: str
    path_pattern: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class PermissionStore:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.path = self.workspace_root / ".minicode" / "permissions.json"
        self.rules: list[PermissionRule] = self._load()

    def is_allowed(self, decision: PermissionDecision, workspace_root: Path | None = None) -> bool:
        root = (workspace_root or self.workspace_root).resolve()
        if decision.denied or not decision.requires_confirmation:
            return False
        if not decision.target_paths:
            return False
        safe_rel_paths = self._safe_relative_paths(decision.target_paths, root)
        if not safe_rel_paths:
            return False
        matching_rules = [rule for rule in self.rules if rule.tool_name == decision.tool_name]
        return all(
            any(fnmatchcase(rel_path, rule.path_pattern) for rule in matching_rules)
            for rel_path in safe_rel_paths
        )

    def add_rule_from_decision(
        self,
        decision: PermissionDecision,
        workspace_root: Path | None = None,
    ) -> PermissionRule:
        root = (workspace_root or self.workspace_root).resolve()
        if decision.denied or not decision.requires_confirmation:
            raise ValueError("Only confirmation-required non-deny decisions can be persisted.")
        rel_paths = self._safe_relative_paths(decision.target_paths, root)
        if not rel_paths:
            raise ValueError("Permission decision has no safe workspace target path.")
        path_pattern = rel_paths[0] if len(rel_paths) == 1 else self._common_pattern(rel_paths)
        rule = PermissionRule(tool_name=decision.tool_name, path_pattern=path_pattern)
        self.rules.append(rule)
        self.save()
        return rule

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"rules": [rule.model_dump() for rule in self.rules]}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load(self) -> list[PermissionRule]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return [PermissionRule.model_validate(item) for item in payload.get("rules", [])]

    @staticmethod
    def _common_pattern(rel_paths: list[str]) -> str:
        parents = {str(Path(path).parent).replace("\\", "/") for path in rel_paths}
        return f"{parents.pop()}/*" if len(parents) == 1 else "**/*"

    @staticmethod
    def _safe_relative_paths(paths: list[Path], workspace_root: Path) -> list[str]:
        rel_paths: list[str] = []
        for target in paths:
            resolved = target.resolve()
            if not is_within_workspace(resolved, workspace_root):
                return []
            if is_sensitive_file(resolved):
                return []
            rel_paths.append(resolved.relative_to(workspace_root).as_posix())
        return rel_paths
```

Export in `src/minicode/permissions/__init__.py`:

```python
from minicode.permissions.store import PermissionRule, PermissionStore
```

- [ ] **Step 4: Add matching tests**

Append:

```python
def test_same_tool_same_path_pattern_matches(tmp_path: Path) -> None:
    store = PermissionStore(tmp_path)
    first = check_permission("write_file", {"file_path": "src/a.py", "content": "x"}, tmp_path)
    store.add_rule_from_decision(first, tmp_path)

    second = check_permission("write_file", {"file_path": "src/a.py", "content": "y"}, tmp_path)

    assert store.is_allowed(second, tmp_path) is True


def test_different_tool_does_not_match(tmp_path: Path) -> None:
    store = PermissionStore(tmp_path)
    write = check_permission("write_file", {"file_path": "src/a.py", "content": "x"}, tmp_path)
    store.add_rule_from_decision(write, tmp_path)

    edit = check_permission(
        "edit_file",
        {"file_path": "src/a.py", "old_string": "x", "new_string": "y"},
        tmp_path,
    )

    assert store.is_allowed(edit, tmp_path) is False


def test_deny_decision_never_matches(tmp_path: Path) -> None:
    store = PermissionStore(tmp_path)
    denied = check_permission("write_file", {"file_path": ".env", "content": "SECRET=1"}, tmp_path)

    assert denied.level == PermissionLevel.DENY
    assert store.is_allowed(denied, tmp_path) is False
```

- [ ] **Step 5: Run store tests**

Run:

```bash
uv run pytest tests/test_permissions/test_store.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/minicode/permissions/store.py src/minicode/permissions/__init__.py tests/test_permissions/test_store.py
git commit -m "feat: add permission allow store"
```

---

### Task 2: CLI Confirmation Prompt

**Files:**
- Create: `src/minicode/cli/confirm.py`
- Test: `tests/test_cli/test_confirm.py`

- [ ] **Step 1: Write failing confirmer tests**

Add `tests/test_cli/test_confirm.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from minicode.cli.confirm import ConfirmationChoice, PermissionConfirmer
from minicode.permissions.checker import check_permission


class FakeSession:
    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    async def prompt_async(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0)


class FakeConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, value: object, *args: object, **kwargs: object) -> None:
        self.lines.append(str(value))


@pytest.mark.asyncio
async def test_confirm_y_allows(tmp_path: Path) -> None:
    decision = check_permission("grep", {"pattern": "TODO"}, tmp_path)
    confirmer = PermissionConfirmer(FakeSession(["y"]), FakeConsole())

    result = await confirmer.confirm(decision)

    assert result == ConfirmationChoice.ALLOW


@pytest.mark.asyncio
async def test_confirm_n_denies(tmp_path: Path) -> None:
    decision = check_permission("grep", {"pattern": "TODO"}, tmp_path)
    confirmer = PermissionConfirmer(FakeSession(["n"]), FakeConsole())

    result = await confirmer.confirm(decision)

    assert result == ConfirmationChoice.DENY


@pytest.mark.asyncio
async def test_confirm_a_always_allows(tmp_path: Path) -> None:
    decision = check_permission("write_file", {"file_path": "a.txt", "content": "x"}, tmp_path)
    confirmer = PermissionConfirmer(FakeSession(["a"]), FakeConsole())

    result = await confirmer.confirm(decision)

    assert result == ConfirmationChoice.ALWAYS_ALLOW
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/test_cli/test_confirm.py -v
```

Expected: fails because `minicode.cli.confirm` does not exist.

- [ ] **Step 3: Implement confirmer**

Create `src/minicode/cli/confirm.py`:

```python
from __future__ import annotations

from enum import StrEnum
from typing import Any

from prompt_toolkit import PromptSession
from rich.console import Console
from rich.panel import Panel

from minicode.permissions.models import PermissionDecision


class ConfirmationChoice(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ALWAYS_ALLOW = "always_allow"


class PermissionConfirmer:
    def __init__(
        self,
        session: PromptSession[Any] | None = None,
        console: Console | None = None,
    ) -> None:
        self.session = session or PromptSession()
        self.console = console or Console(highlight=False)

    async def confirm(self, decision: PermissionDecision) -> ConfirmationChoice:
        self.console.print(Panel(self._format_decision(decision), title="Permission required"))
        while True:
            answer = (await self.session.prompt_async("[y] allow  [n] deny  [a] always allow > ")).strip().lower()
            if answer in {"y", "yes"}:
                return ConfirmationChoice.ALLOW
            if answer in {"n", "no"}:
                return ConfirmationChoice.DENY
            if answer in {"a", "always"}:
                return ConfirmationChoice.ALWAYS_ALLOW
            self.console.print("请输入 y、n 或 a。")

    @staticmethod
    def _format_decision(decision: PermissionDecision) -> str:
        lines = [
            f"Tool: {decision.tool_name}",
            f"Level: {decision.level}",
            f"Operation: {decision.operation}",
            f"Summary: {decision.summary}",
        ]
        if decision.target_paths:
            lines.append("Targets:")
            lines.extend(f"- {path}" for path in decision.target_paths)
        if decision.reasons:
            lines.append("Reasons:")
            lines.extend(f"- {reason}" for reason in decision.reasons)
        return "\n".join(lines)
```

- [ ] **Step 4: Add invalid input test**

Append:

```python
@pytest.mark.asyncio
async def test_invalid_input_reprompts(tmp_path: Path) -> None:
    decision = check_permission("grep", {"pattern": "TODO"}, tmp_path)
    session = FakeSession(["bad", "", "Y"])
    console = FakeConsole()
    confirmer = PermissionConfirmer(session, console)

    result = await confirmer.confirm(decision)

    assert result == ConfirmationChoice.ALLOW
    assert len(session.prompts) == 3
    assert any("请输入" in line for line in console.lines)
```

- [ ] **Step 5: Run confirmer tests**

Run:

```bash
uv run pytest tests/test_cli/test_confirm.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/minicode/cli/confirm.py tests/test_cli/test_confirm.py
git commit -m "feat: add permission confirmation prompt"
```

---

### Task 3: AgentLoop Permission Gate

**Files:**
- Modify: `src/minicode/agent/loop.py`
- Test: `tests/test_agent/test_loop.py`

- [ ] **Step 1: Add fake store and confirmer helpers to loop tests**

Append near existing test helpers in `tests/test_agent/test_loop.py`:

```python
from minicode.cli.confirm import ConfirmationChoice
from minicode.permissions.models import PermissionDecision


class FakePermissionStore:
    def __init__(self, allowed: bool = False) -> None:
        self.allowed = allowed
        self.added: list[PermissionDecision] = []

    def is_allowed(self, decision: PermissionDecision, workspace_root: Path | None = None) -> bool:
        return self.allowed

    def add_rule_from_decision(
        self,
        decision: PermissionDecision,
        workspace_root: Path | None = None,
    ) -> object:
        self.added.append(decision)
        return object()


class FakeConfirmer:
    def __init__(self, choice: ConfirmationChoice) -> None:
        self.choice = choice
        self.decisions: list[PermissionDecision] = []

    async def confirm(self, decision: PermissionDecision) -> ConfirmationChoice:
        self.decisions.append(decision)
        return self.choice
```

- [ ] **Step 2: Write failing test for deny not executing tool**

Append:

```python
@pytest.mark.asyncio
async def test_permission_denied_tool_is_not_executed(tmp_path: Path, app_config: AppConfig) -> None:
    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(
                    id="call_write",
                    index=0,
                    name="write_file",
                    arguments='{"file_path": ".env", "content": "SECRET=1"}',
                ),
            ),
            StreamChunk(type="done", usage=_make_usage()),
        ],
        [StreamChunk(type="text_delta", text="写入被拒绝。"), StreamChunk(type="done")],
    ])
    registry = ToolRegistry()
    loop = AgentLoop(
        provider=provider,
        tool_registry=registry,
        renderer=MagicRenderer(),
        config=app_config,
        workspace_root=tmp_path,
        permission_store=FakePermissionStore(),
        permission_confirmer=FakeConfirmer(ConfirmationChoice.ALLOW),
    )

    response = await loop.run("写 .env")

    assert response is not None
    tool_msgs = [m for m in loop.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    assert "权限拒绝" in (tool_msgs[0].content or "")
    assert not (tmp_path / ".env").exists()
```

- [ ] **Step 3: Modify AgentLoop constructor**

In `src/minicode/agent/loop.py`, add imports:

```python
from minicode.cli.confirm import ConfirmationChoice, PermissionConfirmer
from minicode.permissions.checker import check_permission
from minicode.permissions.store import PermissionStore
```

Extend `AgentLoop.__init__`:

```python
permission_store: PermissionStore | None = None,
permission_confirmer: PermissionConfirmer | None = None,
```

Set:

```python
self.permission_store = permission_store or PermissionStore(self.workspace_root)
self.permission_confirmer = permission_confirmer or PermissionConfirmer(console=renderer.console)
```

- [ ] **Step 4: Add permission gate helper**

Add a private method to `AgentLoop`:

```python
async def _authorize_tool(self, name: str, args: dict[str, object]) -> tuple[bool, str | None]:
    decision = check_permission(
        name,
        args,
        self.workspace_root,
        trust_mode=self.config.permissions.trust_mode,
    )
    if decision.denied:
        return False, f"权限拒绝：{decision.summary}"
    if decision.allowed_without_prompt:
        return True, None
    if self.config.permissions.trust_mode:
        return True, None
    if self.permission_store.is_allowed(decision, self.workspace_root):
        return True, None
    choice = await self.permission_confirmer.confirm(decision)
    if choice == ConfirmationChoice.DENY:
        return False, f"用户拒绝执行工具：{decision.summary}"
    if choice == ConfirmationChoice.ALWAYS_ALLOW:
        self.permission_store.add_rule_from_decision(decision, self.workspace_root)
    return True, None
```

- [ ] **Step 5: Use gate before execute_tool**

In `_execute_tools()`, after JSON args parse and before `self.tool_registry.execute_tool(...)`, insert:

```python
allowed, denial_message = await self._authorize_tool(name, args)
if not allowed:
    message = denial_message or f"用户拒绝执行工具：{name}"
    self.renderer.show_error(message)
    self.messages.append(
        ToolMessage(
            content=message,
            tool_call_id=tc.id,
            name=name,
        )
    )
    continue
```

- [ ] **Step 6: Add allow/always/store/trust tests**

Append:

```python
@pytest.mark.asyncio
async def test_permission_user_deny_returns_tool_message(tmp_path: Path, app_config: AppConfig) -> None:
    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(id="call_grep", index=0, name="grep", arguments='{"pattern": "TODO"}'),
            ),
            StreamChunk(type="done", usage=_make_usage()),
        ],
        [StreamChunk(type="text_delta", text="已取消搜索。"), StreamChunk(type="done")],
    ])
    confirmer = FakeConfirmer(ConfirmationChoice.DENY)
    loop = AgentLoop(
        provider, create_default_registry(), MagicRenderer(), app_config, tmp_path,
        permission_store=FakePermissionStore(), permission_confirmer=confirmer,
    )

    response = await loop.run("搜索 TODO")

    assert response is not None
    assert len(confirmer.decisions) == 1
    assert "用户拒绝" in ([m for m in loop.messages if m.role == "tool"][0].content or "")


@pytest.mark.asyncio
async def test_permission_always_allow_saves_rule_and_executes(tmp_path: Path, app_config: AppConfig) -> None:
    (tmp_path / "a.py").write_text("# TODO", encoding="utf-8")
    store = FakePermissionStore()
    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(id="call_grep", index=0, name="grep", arguments='{"pattern": "TODO"}'),
            ),
            StreamChunk(type="done", usage=_make_usage()),
        ],
        [StreamChunk(type="text_delta", text="找到结果。"), StreamChunk(type="done")],
    ])
    loop = AgentLoop(
        provider, create_default_registry(), MagicRenderer(), app_config, tmp_path,
        permission_store=store, permission_confirmer=FakeConfirmer(ConfirmationChoice.ALWAYS_ALLOW),
    )

    response = await loop.run("搜索 TODO")

    assert response is not None
    assert len(store.added) == 1
    assert any("TODO" in (m.content or "") for m in loop.messages if m.role == "tool")


@pytest.mark.asyncio
async def test_permission_store_match_skips_confirm(tmp_path: Path, app_config: AppConfig) -> None:
    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(id="call_grep", index=0, name="grep", arguments='{"pattern": "TODO"}'),
            ),
            StreamChunk(type="done", usage=_make_usage()),
        ],
        [StreamChunk(type="text_delta", text="完成。"), StreamChunk(type="done")],
    ])
    confirmer = FakeConfirmer(ConfirmationChoice.DENY)
    loop = AgentLoop(
        provider, create_default_registry(), MagicRenderer(), app_config, tmp_path,
        permission_store=FakePermissionStore(allowed=True), permission_confirmer=confirmer,
    )

    await loop.run("搜索 TODO")

    assert confirmer.decisions == []


@pytest.mark.asyncio
async def test_trust_mode_skips_confirm_but_deny_still_blocks(tmp_path: Path, app_config: AppConfig) -> None:
    app_config.permissions.trust_mode = True
    provider = MockStepProvider([
        [
            StreamChunk(
                type="tool_call_delta",
                tool_call=PartialToolCall(id="call_write", index=0, name="write_file", arguments='{"file_path": ".env", "content": "x"}'),
            ),
            StreamChunk(type="done", usage=_make_usage()),
        ],
        [StreamChunk(type="text_delta", text="被拒绝。"), StreamChunk(type="done")],
    ])
    confirmer = FakeConfirmer(ConfirmationChoice.ALLOW)
    loop = AgentLoop(
        provider, ToolRegistry(), MagicRenderer(), app_config, tmp_path,
        permission_store=FakePermissionStore(), permission_confirmer=confirmer,
    )

    await loop.run("写敏感文件")

    assert confirmer.decisions == []
    assert not (tmp_path / ".env").exists()
    assert "权限拒绝" in ([m for m in loop.messages if m.role == "tool"][0].content or "")
```

- [ ] **Step 7: Run loop tests**

Run:

```bash
uv run pytest tests/test_agent/test_loop.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/minicode/agent/loop.py tests/test_agent/test_loop.py
git commit -m "feat: gate tool execution with permissions"
```

---

### Task 4: ChatApp Wiring

**Files:**
- Modify: `src/minicode/cli/app.py`
- Test: `tests/test_cli/test_app.py`

- [ ] **Step 1: Write failing wiring test**

Add a focused test in `tests/test_cli/test_app.py` that monkeypatches `AgentLoop` and asserts `permission_store` and `permission_confirmer` are passed:

```python
def test_chat_app_injects_permission_components(tmp_path: Path, app_config: AppConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeAgentLoop:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("minicode.cli.app.AgentLoop", FakeAgentLoop)
    app = ChatApp(app_config, workspace_root=tmp_path)

    app._get_agent_loop()

    assert captured["permission_store"].workspace_root == tmp_path.resolve()
    assert captured["permission_confirmer"] is not None
```

- [ ] **Step 2: Update ChatApp imports and wiring**

In `src/minicode/cli/app.py`, add:

```python
from minicode.cli.confirm import PermissionConfirmer
from minicode.permissions.store import PermissionStore
```

When constructing `AgentLoop`, pass:

```python
permission_store=PermissionStore(self.workspace_root),
permission_confirmer=PermissionConfirmer(console=self.console),
```

- [ ] **Step 3: Run CLI app tests**

Run:

```bash
uv run pytest tests/test_cli/test_app.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/minicode/cli/app.py tests/test_cli/test_app.py
git commit -m "feat: wire permission confirmation into cli"
```

---

### Task 5: Full Verification

**Files:**
- Validate all changed files.

- [ ] **Step 1: Run focused tests**

```bash
uv run pytest tests/test_permissions/test_checker.py tests/test_permissions/test_store.py tests/test_cli/test_confirm.py tests/test_agent/test_loop.py tests/test_cli/test_app.py -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run lint**

```bash
uv run ruff check .
```

Expected: no lint errors.

- [ ] **Step 3: Run type check**

```bash
uv run mypy src/minicode
```

Expected: no type errors.

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest
```

Expected: full suite passes.

- [ ] **Step 5: Manual smoke test**

Run:

```bash
uv run minicode --workspace .
```

Manual flow:
- Ask the model to run a broad grep-style search if available: it should prompt.
- Press `n`: tool does not execute, and model receives a refusal result.
- Repeat and press `a`: `.minicode/permissions.json` is created.
- Repeat the same tool/path range: it should skip confirmation.

- [ ] **Step 6: Final commit**

```bash
git add src/minicode/permissions/store.py src/minicode/permissions/__init__.py src/minicode/cli/confirm.py src/minicode/agent/loop.py src/minicode/cli/app.py tests/test_permissions/test_store.py tests/test_cli/test_confirm.py tests/test_agent/test_loop.py tests/test_cli/test_app.py
git commit -m "feat: add permission confirmation flow"
```

---

## Implementation Prompt

```text
你是 MiniCode 项目的资深 Python 架构师与实现工程师。请根据 `doc/minicode-task-plan.md` 中 Task 3.2 “权限确认交互”的要求实现功能。

背景：
- 项目是 Python 3.12+、uv、Typer、prompt_toolkit、Rich、Pydantic、pytest。
- 现有权限模型在 `src/minicode/permissions/models.py`，权限判定入口在 `src/minicode/permissions/checker.py::check_permission()`。
- 现有 Agent 工具执行入口在 `src/minicode/agent/loop.py::AgentLoop._execute_tools()`，当前会直接调用 `ToolRegistry.execute_tool()`。
- 当前配置已有 `AppConfig.permissions.trust_mode`，trust mode 只能跳过 confirmation，不能绕过 deny。

必须实现：
1. 新增 `src/minicode/permissions/store.py`：
   - `.minicode/permissions.json` 存储 always allow 规则。
   - 每条规则包含 `tool_name`、`path_pattern`、`created_at`。
   - 只允许匹配同一工具和 workspace 内的同一路径范围。
   - `deny`、敏感路径、workspace 外路径永远不能被 always allow 命中。
2. 新增 `src/minicode/cli/confirm.py`：
   - 支持 `[y] allow`、`[n] deny`、`[a] always allow this pattern`。
   - 非法输入要重新提示。
   - 输出权限摘要，至少包含工具名、权限级别、操作摘要、目标路径和原因。
3. 修改 `AgentLoop._execute_tools()`：
   - JSON 参数解析后，工具真实执行前调用 `check_permission()`。
   - `safe` 直接执行。
   - `deny` 不执行工具，追加 `ToolMessage`，内容说明“权限拒绝”，并将结果返回给模型。
   - `caution/dangerous` 在非 trust mode 下先查 `PermissionStore`，未命中则调用 confirmer。
   - 用户选择 deny 时不执行工具，追加拒绝 `ToolMessage`。
   - 用户选择 always allow 时先写入 store，再执行当前工具。
   - `trust_mode=True` 跳过 caution/dangerous 的交互，但 `deny` 仍拒绝。
4. 修改 `ChatApp._get_agent_loop()`，注入 `PermissionStore(self.workspace_root)` 和 `PermissionConfirmer(console=self.console)`。
5. 补充测试：
   - `tests/test_permissions/test_store.py`
   - `tests/test_cli/test_confirm.py`
   - `tests/test_agent/test_loop.py`
   - 必要时更新 `tests/test_cli/test_app.py`

约束：
- 不要重构无关模块。
- 不要让 always allow 按工具名全局放行。
- 不要让 `.minicode/permissions.json` 存储绝对路径；使用 workspace 相对 POSIX path pattern。
- 不要绕过现有 `check_permission()`；它是唯一权限风险判定入口。
- 拒绝结果必须返回给模型，而不是只显示给用户。
- 使用 TDD：先写失败测试，再实现。

验收命令：
1. `uv run pytest tests/test_permissions/test_store.py tests/test_cli/test_confirm.py tests/test_agent/test_loop.py tests/test_cli/test_app.py -v`
2. `uv run ruff check .`
3. `uv run mypy src/minicode`
4. `uv run pytest`

请按小步提交实现，并在完成后总结修改文件、关键行为和验证结果。
```

---

## Self-Review

- Spec coverage: Task 3.2 的三个要求均覆盖：交互支持 y/n/a，always allow 保存到 `.minicode/permissions.json`，规则包含工具名、路径 pattern、创建时间；验收标准覆盖拒绝不执行并返回模型、always allow 只匹配同工具同安全路径范围。
- Placeholder scan: 无 TBD/TODO/implement later；每个实现任务都有具体文件、测试代码、实现形状、命令和期望结果。
- Type consistency: `PermissionDecision`、`PermissionStore`、`PermissionConfirmer`、`ConfirmationChoice` 在计划中命名一致；AgentLoop 注入参数命名保持一致。
