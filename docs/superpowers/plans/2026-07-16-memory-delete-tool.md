# 记忆删除工具开发计划

## 任务概述

**目标：** 为 MiniCode 增加 Agent 可调用的记忆删除工具，使 Agent 能够在用户明确要求时主动删除记忆。

**背景：** 
- 当前项目已实现完整的记忆系统（`MemoryManager`），包括 `add`、`get`、`delete`、`list_memories` 方法
- 用户可以通过 `/memory delete <name>` 命令删除记忆
- Agent 可以通过 `remember` 工具创建记忆
- **缺失功能：** Agent 无法主动删除记忆，当用户说"忘记..."、"删除记忆..."时，Agent 无法通过工具调用完成

**预期成果：**
- 新增 `forget` 工具，供 Agent 在用户明确表达删除意图时调用
- Agent 可以通过工具删除指定名称的记忆
- 删除后自动刷新 AgentLoop 的系统提示词，确保记忆立即失效
- 完整的单元测试覆盖

---

## 设计决策

### 1. 工具命名
- **工具名：** `forget`
- **语义：** 与 `remember` 工具对称，符合自然语言习惯
- **触发场景：** 用户说"忘记..."、"删除记忆..."、"不要记住..."、"移除记忆..."等

### 2. 工具参数设计

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "要删除的记忆唯一标识名，如 'reply-lang'"
    }
  },
  "required": ["name"],
  "additionalProperties": false
}
```

**设计理由：**
- 只需要 `name` 参数，简洁明确
- 不需要确认参数，因为用户意图已经明确（"删除..."是明确指令）
- 如果记忆不存在，返回友好提示而非错误

### 3. 工具描述（给 LLM 的 description）

```text
删除指定名称的长期记忆。
当用户明确表达「忘记...」「删除记忆...」「不要记住...」「移除记忆...」等语义时调用此工具。
仅在用户明确要求删除时使用，不要主动删除记忆。
```

### 4. 安全策略
- **权限级别：** `caution`（谨慎操作）
- **理由：** 删除是有影响的操作，但不如 `shell` 工具危险，且可通过 `/memory add` 恢复
- **名称验证：** 复用 `MemoryManager._validate_memory_name()` 验证名称合法性，防止路径穿越

### 5. 错误处理
- 名称包含非法字符 → 返回验证错误
- 记忆不存在 → 返回友好提示（`success=True`，因为目标已达成）
- 文件删除失败 → 返回失败结果
- 工作区路径未设置 → 返回配置错误

### 6. AgentLoop 集成
- 删除成功后，如果传入了 `agent_loop` 参数，调用 `agent_loop.reload_memory()` 刷新系统提示词
- 这与 `/memory delete` 命令的行为保持一致

---

## 实施步骤

### 阶段 1：TDD 测试先行

#### 任务 1.1：创建测试文件

**新建文件：** `tests/test_tools/test_forget.py`

**测试场景清单：**

1. ✅ **测试删除存在的记忆**
   - 创建一个记忆
   - 调用 `forget` 工具删除
   - 验证返回 `success=True`
   - 验证记忆文件已被删除
   - 验证索引文件已更新

2. ✅ **测试删除不存在的记忆**
   - 调用 `forget` 工具删除不存在的记忆
   - 验证返回 `success=True`（目标已达成）
   - 验证输出包含友好提示

3. ✅ **测试非法名称验证**
   - 尝试删除名称包含 `../`、`/`、`.` 等非法字符的记忆
   - 验证返回 `success=False`
   - 验证错误消息包含"非法字符"

4. ✅ **测试工作区路径未设置**
   - 创建 `Forget` 工具实例，`workspace_root=None`
   - 调用 `execute`
   - 验证返回 `success=False`
   - 验证错误消息包含"工作区根路径未设置"

5. ✅ **测试删除后索引更新**
   - 创建多个记忆
   - 删除其中一个
   - 验证索引文件仅移除被删除的记忆

6. ✅ **测试参数类型验证**
   - 传入非字符串的 `name`（如 `None`、空字符串）
   - 验证返回参数错误

**测试代码框架：**

```python
"""forget 工具测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from minicode.memory.manager import MemoryManager
from minicode.memory.models import MemoryMetadata, MemoryScope, MemorySource, MemoryType
from minicode.tools.forget import Forget


@pytest.fixture
def memory_workspace(tmp_path: Path) -> Path:
    """创建临时记忆工作区。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def sample_memory_metadata() -> MemoryMetadata:
    """创建示例记忆元数据。"""
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    return MemoryMetadata(
        name="test-memory",
        description="测试记忆",
        created_at=now,
        updated_at=now,
        source=MemorySource.USER,
        scope=MemoryScope.WORKSPACE,
        confidence=0.9,
        type=MemoryType.PROJECT,
    )


async def test_forget_existing_memory(
    memory_workspace: Path,
    sample_memory_metadata: MemoryMetadata,
) -> None:
    """测试删除存在的记忆。"""
    # 创建记忆
    manager = MemoryManager(memory_workspace)
    manager.add(sample_memory_metadata, "这是测试内容")
    
    # 验证记忆存在
    memory_path = memory_workspace / ".minicode" / "memory" / "test-memory.md"
    assert memory_path.exists()
    
    # 删除记忆
    tool = Forget(workspace_root=memory_workspace)
    result = await tool.execute(name="test-memory")
    
    # 验证结果
    assert result.success is True
    assert "已删除" in result.output or "已忘记" in result.output
    
    # 验证文件已删除
    assert not memory_path.exists()
    
    # 验证索引已更新
    assert manager.get("test-memory") is None


async def test_forget_nonexistent_memory(memory_workspace: Path) -> None:
    """测试删除不存在的记忆（应返回友好提示）。"""
    tool = Forget(workspace_root=memory_workspace)
    result = await tool.execute(name="nonexistent")
    
    # 目标已达成，返回 success
    assert result.success is True
    assert "不存在" in result.output or "未找到" in result.output


async def test_forget_invalid_name(memory_workspace: Path) -> None:
    """测试非法记忆名称。"""
    tool = Forget(workspace_root=memory_workspace)
    
    invalid_names = ["../etc/passwd", "test/name", "name..", ""]
    for invalid_name in invalid_names:
        result = await tool.execute(name=invalid_name)
        assert result.success is False
        assert "非法字符" in result.output or "参数" in result.output


async def test_forget_no_workspace(sample_memory_metadata: MemoryMetadata) -> None:
    """测试工作区路径未设置。"""
    tool = Forget(workspace_root=None)
    result = await tool.execute(name="test")
    
    assert result.success is False
    assert "工作区" in result.output


async def test_forget_updates_index(
    memory_workspace: Path,
    sample_memory_metadata: MemoryMetadata,
) -> None:
    """测试删除后索引正确更新。"""
    manager = MemoryManager(memory_workspace)
    
    # 创建三个记忆
    for i in range(3):
        meta = MemoryMetadata(
            name=f"memory-{i}",
            description=f"记忆 {i}",
            created_at=sample_memory_metadata.created_at,
            updated_at=sample_memory_metadata.updated_at,
            source=MemorySource.USER,
            scope=MemoryScope.WORKSPACE,
            confidence=0.9,
            type=MemoryType.PROJECT,
        )
        manager.add(meta, f"内容 {i}")
    
    # 验证三个记忆都在索引中
    entries = manager.list_memories()
    assert len(entries) == 3
    
    # 删除中间的记忆
    tool = Forget(workspace_root=memory_workspace)
    result = await tool.execute(name="memory-1")
    assert result.success is True
    
    # 验证索引只剩两个
    entries = manager.list_memories()
    assert len(entries) == 2
    assert "memory-1" not in [e["name"] for e in entries]
    assert "memory-0" in [e["name"] for e in entries]
    assert "memory-2" in [e["name"] for e in entries]


async def test_forget_invalid_parameter_types(memory_workspace: Path) -> None:
    """测试无效参数类型。"""
    tool = Forget(workspace_root=memory_workspace)
    
    # None
    result = await tool.execute(name=None)
    assert result.success is False
    
    # 空字符串
    result = await tool.execute(name="")
    assert result.success is False
    
    # 纯空格
    result = await tool.execute(name="   ")
    assert result.success is False
```

#### 任务 1.2：运行失败测试

**命令：**
```powershell
.venv\Scripts\python.exe -m pytest tests/test_tools/test_forget.py -v
```

**预期结果：** 所有测试失败，因为 `Forget` 工具尚不存在

---

### 阶段 2：实现 Forget 工具

#### 任务 2.1：创建工具文件

**新建文件：** `src/minicode/tools/forget.py`

**实现要求：**

```python
"""记忆删除工具 — 供 Agent 在用户明确表达删除意图时调用。

允许大模型在识别到用户自然语言中的删除意图后，
通过此工具删除指定名称的长期记忆。

触发场景示例：
- "忘记我之前说的..."
- "删除关于...的记忆"
- "不要记住..."
- "移除记忆..."
"""
from __future__ import annotations

from minicode.memory.manager import MemoryManager
from minicode.tools.base import BaseTool, ToolResult


class Forget(BaseTool):
    """删除指定名称的长期记忆。

    仅在用户明确表达「忘记…」「删除记忆…」「不要记住…」「移除记忆…」
    等语义时调用此工具。不要主动删除记忆。
    """

    name: str = "forget"
    description: str = (
        "删除指定名称的长期记忆。"
        "当用户说「忘记…」「删除记忆…」「不要记住…」「移除记忆…」等时调用此工具。"
        "仅在用户明确要求删除时使用，不要主动删除记忆。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要删除的记忆唯一标识名，如 'reply-lang'",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    risk_level: str = "caution"  # 谨慎操作，但可恢复

    async def execute(self, **kwargs: object) -> ToolResult:
        """执行记忆删除。

        Args:
            **kwargs: 必须包含 name 参数。

        Returns:
            删除结果。
        """
        # ── 参数提取与验证 ──
        name = kwargs.get("name")
        
        if not isinstance(name, str) or not name.strip():
            return ToolResult(
                success=False,
                output="参数 name 必须是有效的非空字符串",
            )
        
        name = name.strip()
        
        # ── 工作区路径检查 ──
        if self.workspace_root is None:
            return ToolResult(
                success=False,
                output="工作区根路径未设置",
            )
        
        # ── 名称合法性验证 ──
        try:
            MemoryManager._validate_memory_name(name)
        except ValueError as e:
            return ToolResult(success=False, output=str(e))
        
        # ── 执行删除 ──
        try:
            manager = MemoryManager(self.workspace_root)
            deleted = manager.delete(name)
            
            if not deleted:
                # 记忆不存在，但目标已达成
                return ToolResult(
                    success=True,
                    output=f"记忆「{name}」不存在或已被删除。",
                )
            
            return ToolResult(
                success=True,
                output=f"已忘记记忆「{name}」。",
            )
        
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"删除记忆失败：{e}",
            )
```

**关键设计点：**
1. ✅ 使用 `risk_level="caution"` — 需要权限确认
2. ✅ 名称验证复用 `MemoryManager._validate_memory_name()`
3. ✅ 记忆不存在时返回 `success=True`（幂等操作）
4. ✅ 异常捕获确保不会崩溃
5. ✅ 输出消息使用中文，符合用户体验

---

#### 任务 2.2：注册工具

**修改文件：** `src/minicode/tools/__init__.py`

**修改内容：**

找到现有的工具注册部分，添加：

```python
from minicode.tools.forget import Forget

# ... 其他导入 ...

# 在适当位置添加注册
registry.register(Forget)
```

**验证位置：** 确保在 `registry.register(Remember)` 附近注册，保持语义对称

---

### 阶段 3：测试验证

#### 任务 3.1：运行单元测试

**命令：**
```powershell
.venv\Scripts\python.exe -m pytest tests/test_tools/test_forget.py -v
```

**预期结果：** 所有测试通过

---

#### 任务 3.2：运行工具注册测试

**命令：**
```powershell
.venv\Scripts\python.exe -m pytest tests/test_tools/test_registry.py -v
```

**预期结果：** 工具注册测试通过，`forget` 工具出现在工具列表中

---

#### 任务 3.3：集成测试

**手动测试步骤：**

1. 启动 MiniCode：
```powershell
uv run minicode
```

2. 创建一个记忆：
```
记住：我喜欢用简洁的代码风格
```

3. 验证记忆已创建：
```
/memory list
```

4. 让 Agent 删除记忆：
```
忘记关于代码风格的记忆
```

5. 验证记忆已删除：
```
/memory list
```

**预期行为：**
- Agent 应该识别到删除意图
- 调用 `forget` 工具
- 返回成功消息
- 记忆列表中不再包含该记忆

---

### 阶段 4：质量保证

#### 任务 4.1：运行完整测试套件

**命令：**
```powershell
.venv\Scripts\python.exe -m pytest
```

**预期结果：** 所有测试通过，无新增失败

---

#### 任务 4.2：运行覆盖率检查

**命令：**
```powershell
.venv\Scripts\python.exe -m pytest --cov=minicode --cov-report=term-missing
```

**预期结果：** 
- `src/minicode/tools/forget.py` 覆盖率 ≥ 90%
- 整体覆盖率不低于当前基线（94%）

---

#### 任务 4.3：代码质量检查

**Ruff 检查：**
```powershell
.venv\Scripts\python.exe -m ruff check src/minicode/tools/forget.py tests/test_tools/test_forget.py
```

**Mypy 检查：**
```powershell
.venv\Scripts\python.exe -m mypy src/minicode/tools/forget.py
```

**预期结果：** 无错误，无警告

---

### 阶段 5：文档更新

#### 任务 5.1：更新 CLAUDE.md

**修改文件：** `E:\AllProject\MiniCode\CLAUDE.md`

**修改位置：** 在 "## Risk Levels" 部分或工具列表部分

**添加内容：**

```markdown
### 记忆工具

- 🟡 `remember` (caution) — 创建长期记忆
- 🟡 `forget` (caution) — 删除长期记忆
```

---

#### 任务 5.2：更新 README（可选）

如果 README 中有工具列表，同步更新。

---

## 验收标准

### 功能验收
- ✅ Agent 能够识别用户的删除记忆意图
- ✅ Agent 调用 `forget` 工具成功删除记忆
- ✅ 记忆文件被正确删除
- ✅ 索引文件被正确更新
- ✅ 删除不存在的记忆时返回友好提示
- ✅ 非法名称被正确拒绝

### 测试验收
- ✅ 所有单元测试通过
- ✅ 工具注册测试通过
- ✅ 覆盖率 ≥ 90%
- ✅ 无测试警告

### 质量验收
- ✅ Ruff 检查通过
- ✅ Mypy 检查通过
- ✅ 代码风格符合项目规范
- ✅ 中文注释和文档完整

### 安全验收
- ✅ 名称验证防止路径穿越
- ✅ 权限级别设置为 `caution`
- ✅ 异常处理完善，不会崩溃

---

## 风险与限制

### 已知限制
1. **不支持批量删除** — 每次只能删除一个记忆
2. **无撤销功能** — 删除后无法恢复（但可以通过 `/memory add` 手动重建）
3. **不刷新 AgentLoop** — 当前实现不会自动调用 `agent_loop.reload_memory()`

### 未来优化方向
1. **批量删除** — 支持 `names` 参数接受列表
2. **模糊匹配** — 支持通配符或模糊查询
3. **软删除** — 移动到回收站而非直接删除
4. **自动刷新** — 集成 AgentLoop 刷新机制

---

## 时间估算

| 阶段 | 预计时间 |
|------|---------|
| 阶段 1：编写测试 | 30 分钟 |
| 阶段 2：实现工具 | 20 分钟 |
| 阶段 3：测试验证 | 15 分钟 |
| 阶段 4：质量保证 | 15 分钟 |
| 阶段 5：文档更新 | 10 分钟 |
| **总计** | **90 分钟** |

---

## 提交策略

建议单次提交，包含：
- 新增文件：`src/minicode/tools/forget.py`
- 新增文件：`tests/test_tools/test_forget.py`
- 修改文件：`src/minicode/tools/__init__.py`
- 修改文件：`CLAUDE.md`（可选）

**提交消息示例：**
```
feat: 添加记忆删除工具 (forget)

- 新增 forget 工具供 Agent 删除记忆
- 支持用户说"忘记..."时主动删除
- 添加完整单元测试覆盖
- 权限级别设为 caution
```

---

## 自检清单

实施前请确认：
- [ ] 已阅读并理解当前记忆系统实现（`MemoryManager`、`Remember` 工具）
- [ ] 已确认 `risk_level="caution"` 符合项目权限策略
- [ ] 已准备好临时工作区用于测试
- [ ] 已确认测试环境中 pytest、pytest-asyncio 可用

实施后请确认：
- [ ] 所有测试通过
- [ ] 代码质量检查通过
- [ ] 手动集成测试通过
- [ ] 文档已更新
- [ ] 已提交代码

---

## 参考资料

- **相关文件：**
  - `src/minicode/memory/manager.py` — MemoryManager 实现
  - `src/minicode/tools/remember.py` — 记忆创建工具
  - `src/minicode/commands/memory_cmd.py` — `/memory` 命令实现
  - `tests/test_memory/test_manager.py` — MemoryManager 测试参考

- **开发规范：**
  - CLAUDE.md — 项目开发规范
  - doc/minicode-task-plan_2.0.md — 2.0 版本任务计划

---

**计划创建时间：** 2026-07-16  
**计划版本：** 1.0  
**预期完成时间：** 1.5 小时  
**优先级：** 中等（功能完善）
