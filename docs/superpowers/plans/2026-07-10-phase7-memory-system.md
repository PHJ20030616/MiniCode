# Phase 7：记忆系统 — 实现计划

**Goal:** 实现 Markdown + YAML frontmatter 记忆系统，支持 CRUD、自动索引、优先级排序注入到 Agent system prompt。

**Architecture:** `models.py`（纯数据模型）→ `manager.py`（文件 I/O、索引、排序）→ `system_prompt.py`（接收记忆内容注入）。模式参考 `session/` 模块。

**Global Constraints:** 用户可见文字用中文；重要代码加中文注释；`from __future__ import annotations`；structlog 日志；fail-soft I/O；记忆目录 `.minicode/memory/`；MEMORY.md 格式 `- [Title](file.md) — desc`；`get_all_content()` 默认上限 8000 字符。

---

### Task 1：记忆配置项

**Files:** `src/minicode/config/models.py` + `loader.py`
- `models.py`: 在 `PermissionsConfig` 后添加 `MemoryConfig(BaseModel)`，字段 `enabled: bool = True`、`max_chars: int = 8000`；`AppConfig` 追加 `memory: MemoryConfig = Field(default_factory=MemoryConfig)`
- `loader.py`: `_get_defaults()` 中 `"permissions"` 之后添加 `"memory": {"enabled": True, "max_chars": 8000}`
- **Tests:** `tests/test_config/test_memory_config.py` — 验证默认值、自定义值、AppConfig 包含 memory 属性

### Task 2：记忆数据模型

**Files:** 创建 `src/minicode/memory/models.py`；更新 `memory/__init__.py`

核心模型：

```python
import re, yaml
from enum import Enum
from pydantic import BaseModel, Field

class MemorySource(str, Enum): USER = "user"; CONVERSATION = "conversation"; MANUAL = "manual"
class MemoryScope(str, Enum): GLOBAL = "global"; WORKSPACE = "workspace"
class MemoryType(str, Enum): USER = "user"; PROJECT = "project"; REFERENCE = "reference"; FEEDBACK = "feedback"

class MemoryMetadata(BaseModel):
    name: str
    description: str = ""
    created_at: datetime
    updated_at: datetime
    source: MemorySource = MemorySource.USER
    scope: MemoryScope = MemoryScope.GLOBAL
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    type: MemoryType = MemoryType.USER

class Memory(BaseModel):
    metadata: MemoryMetadata
    content: str = ""

    FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)

    @classmethod
    def parse_file(cls, content: str) -> tuple[dict, str]:
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match: return {}, content
        try: fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError: return {}, content
        return fm, match.group(2)

    @classmethod
    def format_file(cls, metadata: MemoryMetadata, body: str) -> str:
        return f"---\n{yaml.dump({k: v.isoformat() if isinstance(v, datetime) else (v.value if hasattr(v, 'value') else v) for k, v in {...}.items()}, allow_unicode=True, default_flow_style=False, sort_keys=False)}---\n\n{body}"
```

- **Tests:** `tests/test_memory/test_models.py` — MemoryMetadata 必填/全字段/验证（confidence 0-1、source/scope/type 枚举）、Memory 构造、frontmatter 解析（正常/无 frontmatter/空内容/损坏）、格式化后能被重新解析

### Task 3：MemoryManager 核心 CRUD

**Files:** 创建 `src/minicode/memory/manager.py`

```python
class MemoryManager:
    INDEX_FILENAME = "MEMORY.md"

    def __init__(self, workspace_root: Path):
        self._memory_dir = workspace_root / ".minicode" / "memory"
        self._index_path = self._memory_dir / self.INDEX_FILENAME

    def add(self, metadata: MemoryMetadata, content: str) -> Path:
        self._validate_memory_name(metadata.name)  # re.fullmatch(r"[a-zA-Z0-9_-]+")
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        file_path = self._memory_dir / f"{metadata.name}.md"
        file_path.write_text(Memory.format_file(metadata, content), encoding="utf-8")
        self._update_index(metadata)
        return file_path

    def get(self, name: str) -> Memory | None:
        path = self._memory_dir / f"{name}.md"
        return Memory.from_file_content(path.read_text("utf-8")) if path.exists() else None

    def delete(self, name: str) -> bool:
        path = self._memory_dir / f"{name}.md"
        deleted = path.unlink() or True if path.exists() else False
        self._remove_from_index(name)
        return deleted

    def list_memories(self) -> list[dict]:
        # 从 MEMORY.md 解析索引行，返回 [{name, filename, description}]
        return self._load_index()
```

索引维护方法：`_load_index()` 用正则匹配 `- [name](file.md) — desc` 行；`_save_index()` 重建 MEMORY.md；`_update_index()` / `_remove_from_index()` 修改后保存。

- **Tests:** `tests/test_memory/test_manager.py` — add 创建文件、get 正确解析、get 不存在返回 None、delete 删除文件/不存在返回 False、delete 路径穿越拒绝、list 空/非空、特殊字符名称验证

### Task 4：get_all_content() 与优先级排序

在 `MemoryManager` 中追加：

```python
def get_all_content(self, max_chars: int = 8000, workspace: str | None = None) -> str:
    # 1. 遍历 .md 文件（排除 MEMORY.md）
    # 2. 解析每个文件，跳过损坏文件并记录 debug 日志
    # 3. 检测同名不同 scope 的冲突 → debug 日志
    # 4. 排序：(scope 匹配 workspace ? 0 : 1, -updated_at.timestamp(), -confidence)
    # 5. 格式化：f"--- 记忆：{name} ({scope}, {confidence}) ---\n{body}"
    # 6. 拼接，超过 max_chars 时截断到最后一个换行 + "...（截断）"
    # 7. 无记忆返回 ""
```

- **Tests:** 追加 `TestGetAllContent` — 空目录返回空、单条/多条记忆格式化、scope 过滤（global vs workspace）、max_chars 截断、损坏文件跳过不影响

### Task 5：System Prompt 注入

**Files:** `src/minicode/agent/system_prompt.py`、`src/minicode/agent/loop.py`

- `build_system_prompt(tool_registry, memory_content=None)`：若 `memory_content` 非空，在末尾追加 `"\n\n---\n## 用户记忆\n\n{memory_content}\n\n> ⚠️ 用户记忆，可能不完整或过期。请以当前对话上下文为准。"`

- `AgentLoop.__init__()`：导入 `MemoryManager`，在构建 system_prompt 前：

```python
memory_content: str | None = None
if config.memory.enabled:
    mm = MemoryManager(self.workspace_root)
    memory_content = mm.get_all_content(max_chars=config.memory.max_chars, workspace=str(self.workspace_root))
self.system_prompt = build_system_prompt(tool_registry, memory_content=memory_content)
```

- **Tests:** `tests/test_agent/test_system_prompt.py` — 无记忆/空记忆不注入、有记忆时含"用户记忆"和"可能不完整或过期"
- **集成测试:** `tests/test_memory/test_integration.py` — 完整链路：add → get_all_content → build_system_prompt → 验证

---

## 文件变更汇总

| 文件 | 操作 |
|------|------|
| `src/minicode/memory/__init__.py` | 修改（更新导出） |
| `src/minicode/memory/models.py` | 创建 |
| `src/minicode/memory/manager.py` | 创建 |
| `src/minicode/config/models.py` | 修改（+MemoryConfig） |
| `src/minicode/config/loader.py` | 修改（+memory 默认值） |
| `src/minicode/agent/system_prompt.py` | 修改（+memory_content 参数） |
| `src/minicode/agent/loop.py` | 修改（+MemoryManager 加载） |
| `tests/test_memory/__init__.py` | 创建 |
| `tests/test_memory/test_models.py` | 创建 |
| `tests/test_memory/test_manager.py` | 创建 |
| `tests/test_memory/test_integration.py` | 创建 |
| `tests/test_config/test_memory_config.py` | 创建 |

## 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| MemoryManager 位置 | AgentLoop 自主创建 | 降低 ChatApp 耦合 |
| 存储格式 | Markdown + YAML frontmatter | 与 Claude Code 兼容 |
| Frontmatter 解析 | 正则 + yaml.safe_load | PyYAML 已在依赖中 |
| 冲突处理 | debug 日志记录，不自动删除 | 任务计划要求 |
| 排序策略 | scope 匹配 > updated_at > confidence | 任务计划要求 |
