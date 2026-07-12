# 命令自动补全系统 — 设计规格说明书

> **版本**: v0.3  
> **创建日期**: 2026-07-11  
> **状态**: 设计完成，待审核  

---

## 1. 概述

在输入 `/` 前缀时，CLI 输入行下方自动显示可用命令的下拉候选列表，用户继续打字可过滤候选，使用 Tab/上下键选择。类似 Claude Code 的命令补全体验。

### 1.1 动机

当前用户必须完整输入 `/help`、`/clear` 等命令名，没有提示。输入 `/` 后显示候选列表可以：
- 降低用户记忆成本
- 加快操作速度
- 发现不熟悉的命令（如 `/session`、`/memory`）

---

## 2. 交互行为

| 输入内容 | 显示候选 |
|---|---|
| `/` | 所有命令：`/quit` `/help` `/clear` `/session` `/config` `/memory` |
| `/c` | 过滤为：`/clear` `/config` |
| `/se` | 过滤为：`/session` |
| `/unknown` | 无候选，下拉列表消失 |
| 不以 `/` 开头 | 不激活补全 |

- 候选列表只显示命令名称（不含描述）
- 匹配规则：命令名或别名的**前缀匹配**（不区分大小写）
- Tab 键轮换候选，上下键移动选择，回车确认
- 继续打字时实时过滤

---

## 3. 实现方案

### 3.1 架构

利用 `prompt_toolkit` 内置的 `Completer` 和 `Completion` 接口，新增单个模块 `completer.py`，与现有 `CommandRegistry` 集成。

### 3.2 新增文件

**`src/minicode/cli/completer.py`**

```python
class CommandCompleter(Completer):
    """为 / 命令提供自动补全。只在输入以 / 开头时激活。"""

    def __init__(self, registry: type[CommandRegistry]) -> None:
        self._registry = registry

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text
        if not text.startswith("/"):
            return

        partial = text[1:].lower()
        commands = self._registry.list_all()

        for cmd in commands:
            names = [cmd.name] + cmd.aliases
            for name in names:
                if name.startswith(partial):
                    yield Completion(
                        f"/{name}",
                        start_position=-len(text),
                        display=f"/{name}",
                    )
```

### 3.3 修改文件

**`src/minicode/cli/app.py`**

- 导入 `CommandCompleter` 和 `CommandRegistry`
- 在 `ChatApp.session` 属性的 `PromptSession()` 创建时传入 `completer=CommandCompleter(CommandRegistry)` 和 `complete_while_typing=True`

### 3.4 核心参数解释

| prompt_toolkit 参数 | 值 | 作用 |
|---|---|---|
| `completer` | `CommandCompleter(CommandRegistry)` | 自定义补全逻辑 |
| `complete_while_typing` | `True` | 每次按键都重新计算候选（实时过滤） |

---

## 4. 测试要点

- 输入 `/` 后返回所有命令
- 输入 `/c` 仅返回以 c 开头的命令（`/clear`, `/config`）
- 输入 `/se` 匹配 `/session`
- 别名匹配（如 `/q` 匹配 `/quit`）
- 普通文本输入不触发补全
- 空命令名（仅 `/`）显示全部

---

## 5. 版本边界

- **本需求实现**：命令名静态列表补全
- **后续可扩展**：子命令补全（如 `/session ` 后提示 `list/switch/delete`）、参数补全、历史命令补全

---

## 6. 不在此范围

- 不显示命令描述
- 不做模糊/子串匹配
- 不涉及子命令级的补全
