# 工具执行并发优化计划

**制定日期**: 2026-07-16  
**目标版本**: v0.2.1  
**优先级**: 中  
**预计工作量**: 4-6小时

## 一、优化目标

当前工具执行是完全串行的，当模型一次调用多个工具时（尤其是多个读操作），会产生不必要的延迟。本优化借鉴 Claude Code 的分块并发策略，在保持写操作安全性的前提下，加速读操作的执行。

**核心原则**：
- 读操作可并发，写操作必须串行
- 块间串行执行，保持工具调用的整体顺序语义
- 并发上限为 3，避免资源竞争和权限确认混乱

## 二、分块策略

### 2.1 工具分类

将所有工具分为两类：

| 类别 | 工具列表 | 特征 |
|------|---------|------|
| **读工具** | `read_file`, `grep`, `glob`, `list_directory` | 只读取数据，无副作用，可并发 |
| **写工具** | `write_file`, `edit_file`, `bash`, `remember`, `forget`, `run_subagent` | 有副作用或需要权限确认，必须串行 |

### 2.2 分块规则

按照工具调用的原始顺序，将 `tool_calls` 列表分成多个块（block）：

1. **相邻的读工具**可以分到同一块 → 块内并发执行
2. **每个写工具**独占一块 → 块内串行执行（实际只有一个工具）
3. **块与块之间**串行执行，保持原始调用顺序

**示例**：

```
原始调用顺序: [read_file, read_file, grep, write_file, read_file, glob, bash, read_file]

分块结果:
  Block 0: [read_file, read_file, grep]       ← 读工具块，并发执行
  Block 1: [write_file]                       ← 写工具块，串行执行
  Block 2: [read_file, glob]                  ← 读工具块，并发执行
  Block 3: [bash]                             ← 写工具块，串行执行
  Block 4: [read_file]                        ← 读工具块，并发执行（只有1个工具）
```

## 三、实现方案

### 3.1 新增数据结构

在 `src/minicode/agent/loop.py` 中新增：

```python
from enum import Enum
from dataclasses import dataclass

class ToolCategory(Enum):
    """工具类别"""
    READ = "read"    # 只读工具，可并发
    WRITE = "write"  # 写工具，必须串行

@dataclass
class ToolBlock:
    """工具执行块"""
    category: ToolCategory
    tool_calls: list[ToolCall]
```

### 3.2 工具分类配置

在 `AgentLoop` 类中添加类变量：

```python
class AgentLoop:
    # 只读工具集合（可并发）
    READ_TOOLS: set[str] = {
        "read_file",
        "grep", 
        "glob",
        "list_directory",
    }
    
    # 其余工具视为写工具（必须串行）
    # 包括: write_file, edit_file, bash, remember, forget, run_subagent
```

### 3.3 分块函数

```python
def _partition_tool_calls(self, tool_calls: list[ToolCall]) -> list[ToolBlock]:
    """将工具调用列表按读/写分块。
    
    规则：
    1. 相邻的读工具分到同一块
    2. 每个写工具独占一块
    3. 保持原始调用顺序
    
    Args:
        tool_calls: 原始工具调用列表
        
    Returns:
        分块后的 ToolBlock 列表
    """
    if not tool_calls:
        return []
    
    blocks: list[ToolBlock] = []
    current_read_batch: list[ToolCall] = []
    
    for tc in tool_calls:
        tool_name = tc.function.name
        
        if tool_name in self.READ_TOOLS:
            # 读工具：加入当前批次
            current_read_batch.append(tc)
        else:
            # 写工具：先提交当前读批次（如果有），然后写工具独占一块
            if current_read_batch:
                blocks.append(ToolBlock(
                    category=ToolCategory.READ,
                    tool_calls=current_read_batch
                ))
                current_read_batch = []
            
            blocks.append(ToolBlock(
                category=ToolCategory.WRITE,
                tool_calls=[tc]
            ))
    
    # 处理末尾的读批次
    if current_read_batch:
        blocks.append(ToolBlock(
            category=ToolCategory.READ,
            tool_calls=current_read_batch
        ))
    
    return blocks
```

### 3.4 修改 `_execute_tools` 方法

替换现有的 `_execute_tools` 实现：

```python
async def _execute_tools(self, tool_calls: list[ToolCall]) -> None:
    """按分块策略执行工具调用。
    
    - 读工具块：并发执行（上限3）
    - 写工具块：串行执行
    - 块间：串行执行
    """
    blocks = self._partition_tool_calls(tool_calls)
    
    for block_idx, block in enumerate(blocks):
        logger.debug(
            f"执行工具块 {block_idx + 1}/{len(blocks)}",
            category=block.category.value,
            count=len(block.tool_calls)
        )
        
        if block.category == ToolCategory.READ:
            # 读工具块：并发执行
            await self._execute_read_block(block.tool_calls)
        else:
            # 写工具块：串行执行（实际只有1个工具）
            for tc in block.tool_calls:
                await self._execute_single_tool(tc)
```

### 3.5 新增读工具块并发执行方法

```python
async def _execute_read_block(self, tool_calls: list[ToolCall]) -> None:
    """并发执行一组读工具调用。
    
    - 并发上限为 3
    - 按原始顺序追加 ToolMessage 到 self.messages
    - 即使某个工具失败，其他工具仍继续执行
    
    Args:
        tool_calls: 读工具调用列表
    """
    if not tool_calls:
        return
    
    # 并发上限为 3
    semaphore = asyncio.Semaphore(3)
    
    async def _execute_one(tc: ToolCall) -> tuple[int, ToolMessage]:
        """执行单个读工具，返回 (原始索引, ToolMessage)"""
        async with semaphore:
            name = tc.function.name
            
            # 解析参数
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError as e:
                logger.debug("工具参数解析失败", tool=name, error=str(e))
                return (
                    tool_calls.index(tc),
                    ToolMessage(
                        content=f"参数解析错误：{e}",
                        tool_call_id=tc.id,
                        name=name,
                    )
                )
            
            # 权限检查（读工具通常是 safe，但仍需检查）
            decision = check_permission(
                tool_name=name,
                arguments=args,
                workspace_root=self.workspace_root,
                trust_mode=self.config.permissions.trust_mode,
            )
            
            if decision.denied:
                return (
                    tool_calls.index(tc),
                    ToolMessage(
                        content=f"权限拒绝：{decision.summary}",
                        tool_call_id=tc.id,
                        name=name,
                    )
                )
            
            # 执行工具
            logger.debug("并发执行读工具", tool=name, args=args)
            try:
                result = await self.tool_registry.execute_tool(
                    name=name,
                    args=args,
                    workspace_root=self.workspace_root,
                )
            except Exception as e:
                logger.debug("工具执行异常", tool=name, error=str(e), exc_info=True)
                return (
                    tool_calls.index(tc),
                    ToolMessage(
                        content=f"工具执行失败：{e}",
                        tool_call_id=tc.id,
                        name=name,
                    )
                )
            
            # 构造 ToolMessage
            if not result.success:
                error_detail = result.error or result.output
                logger.debug("工具执行失败", tool=name, error=error_detail[:100])
            
            return (
                tool_calls.index(tc),
                ToolMessage(
                    content=result.output,
                    tool_call_id=tc.id,
                    name=name,
                )
            )
    
    # 并发执行所有读工具
    if len(tool_calls) > 1:
        self.renderer.console.print(
            Text(f"\n── 正在并发执行 {len(tool_calls)} 个读工具 ──", style="dim")
        )
    
    results = await asyncio.gather(*(_execute_one(tc) for tc in tool_calls))
    
    # 按原始顺序排序并追加到 messages
    results_sorted = sorted(results, key=lambda x: x[0])
    for _, tool_msg in results_sorted:
        self.messages.append(tool_msg)
    
    # 显示执行完成信息
    success_count = sum(
        1 for _, msg in results_sorted 
        if not msg.content.startswith("参数解析错误") 
        and not msg.content.startswith("权限拒绝")
        and not msg.content.startswith("工具执行失败")
    )
    self.renderer.show_info(
        f"读工具执行完成：{success_count}/{len(tool_calls)} 成功"
    )
```

### 3.6 保留现有的 `_execute_single_tool` 方法

该方法用于写工具的串行执行，保持不变。

## 四、测试计划

### 4.1 单元测试

在 `tests/test_agent/test_loop_concurrency.py` 新增：

```python
import pytest
from minicode.agent.loop import AgentLoop, ToolBlock, ToolCategory
from minicode.providers.base import ToolCall, FunctionCall

@pytest.mark.asyncio
async def test_partition_tool_calls_all_read():
    """测试：全是读工具 → 1个块"""
    loop = AgentLoop(...)  # 省略初始化
    calls = [
        ToolCall(id="1", function=FunctionCall(name="read_file", arguments="{}")),
        ToolCall(id="2", function=FunctionCall(name="grep", arguments="{}")),
        ToolCall(id="3", function=FunctionCall(name="glob", arguments="{}")),
    ]
    blocks = loop._partition_tool_calls(calls)
    assert len(blocks) == 1
    assert blocks[0].category == ToolCategory.READ
    assert len(blocks[0].tool_calls) == 3

@pytest.mark.asyncio
async def test_partition_tool_calls_all_write():
    """测试：全是写工具 → 每个独占一块"""
    loop = AgentLoop(...)
    calls = [
        ToolCall(id="1", function=FunctionCall(name="write_file", arguments="{}")),
        ToolCall(id="2", function=FunctionCall(name="bash", arguments="{}")),
    ]
    blocks = loop._partition_tool_calls(calls)
    assert len(blocks) == 2
    assert all(b.category == ToolCategory.WRITE for b in blocks)
    assert all(len(b.tool_calls) == 1 for b in blocks)

@pytest.mark.asyncio
async def test_partition_tool_calls_mixed():
    """测试：读写混合 → 正确分块"""
    loop = AgentLoop(...)
    calls = [
        ToolCall(id="1", function=FunctionCall(name="read_file", arguments="{}")),
        ToolCall(id="2", function=FunctionCall(name="grep", arguments="{}")),
        ToolCall(id="3", function=FunctionCall(name="write_file", arguments="{}")),
        ToolCall(id="4", function=FunctionCall(name="glob", arguments="{}")),
        ToolCall(id="5", function=FunctionCall(name="bash", arguments="{}")),
    ]
    blocks = loop._partition_tool_calls(calls)
    
    # 期望分块: [read+grep], [write], [glob], [bash]
    assert len(blocks) == 4
    assert blocks[0].category == ToolCategory.READ
    assert len(blocks[0].tool_calls) == 2
    assert blocks[1].category == ToolCategory.WRITE
    assert len(blocks[1].tool_calls) == 1
    assert blocks[2].category == ToolCategory.READ
    assert len(blocks[2].tool_calls) == 1
    assert blocks[3].category == ToolCategory.WRITE
    assert len(blocks[3].tool_calls) == 1

@pytest.mark.asyncio
async def test_execute_read_block_order():
    """测试：并发执行读工具后，ToolMessage 顺序与原始调用顺序一致"""
    # 使用 MockProvider 和实际工具
    # 验证即使工具完成时间不同，messages 仍按原始顺序追加
    ...

@pytest.mark.asyncio
async def test_execute_read_block_semaphore():
    """测试：并发上限为 3"""
    # 模拟5个慢读工具
    # 验证同时运行的不超过3个
    ...
```

### 4.2 集成测试

```python
@pytest.mark.asyncio
async def test_agent_loop_with_concurrency():
    """集成测试：完整 ReAct 循环中的并发优化"""
    # 构造模型响应：一次返回多个读工具调用
    # 验证：
    # 1. 工具执行正确
    # 2. 响应时间明显短于串行执行
    # 3. 最终对话历史顺序正确
    ...
```

### 4.3 手动测试场景

```bash
# 场景1：多个读工具
uv run minicode
> 请读取 README.md、src/minicode/__init__.py 和 pyproject.toml 这三个文件

# 预期：
# - 显示 "正在并发执行 3 个读工具"
# - 执行时间显著短于串行
# - 所有文件内容正确返回

# 场景2：读写混合
> 请读取 README.md，然后在 test.txt 中写入摘要，最后用 grep 搜索 "MiniCode"

# 预期：
# - read_file 单独执行
# - write_file 串行执行，可能有权限确认
# - grep 单独执行
# - 最终结果正确

# 场景3：全写工具
> 在 a.txt 写入 "hello"，在 b.txt 写入 "world"，然后执行 bash ls

# 预期：
# - 三个工具完全串行执行
# - 每个都可能触发权限确认
# - 执行顺序严格保持
```

## 五、风险与注意事项

### 5.1 风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 并发读取导致文件锁冲突 | 低 | 读操作不持有排他锁，OS 层面支持并发读 |
| 权限确认逻辑复杂化 | 中 | 读工具通常是 `safe`，无需确认；少数需确认的在并发前已检查 |
| 错误处理不完善 | 中 | 每个工具独立捕获异常，失败不影响其他工具 |
| 消息顺序错乱 | 高 | 通过索引排序保证追加顺序与调用顺序一致 |

### 5.2 注意事项

1. **run_subagent 保持现有逻辑**：已有专门的批量并发处理，不纳入本次优化
2. **READ_TOOLS 集合需维护**：新增读工具时，记得加入集合
3. **并发上限 3 的依据**：
   - 避免过多并发导致权限确认界面混乱
   - 避免 I/O 竞争（尤其是 grep 扫描大目录）
   - 与 subagent 并发上限保持一致
4. **向后兼容**：如果 `tool_calls` 只有1个工具，行为与优化前完全一致

## 六、实施步骤

1. **修改 `src/minicode/agent/loop.py`**：
   - 新增 `ToolCategory` 枚举和 `ToolBlock` 数据类
   - 新增 `READ_TOOLS` 类变量
   - 实现 `_partition_tool_calls()` 方法
   - 实现 `_execute_read_block()` 方法
   - 修改 `_execute_tools()` 方法

2. **编写单元测试**：
   - 在 `tests/test_agent/` 新增 `test_loop_concurrency.py`
   - 覆盖分块逻辑、并发执行、消息顺序等核心场景

3. **手动测试**：
   - 运行 CLI，验证多读工具场景
   - 验证读写混合场景
   - 验证错误处理

4. **文档更新**：
   - 在 `doc/minicode-design.md` 的 "数据流" 章节补充并发优化说明
   - 在 `CLAUDE.md` 的 "Key Design Decisions" 补充一条

5. **性能基准测试（可选）**：
   - 对比优化前后的执行时间
   - 记录到 `doc/benchmarks/` 目录

## 七、预期效果

- **性能提升**：当模型一次调用 3 个以上读工具时，执行时间减少 50%-70%
- **用户体验**：显示 "正在并发执行 N 个读工具"，用户感知更快
- **代码质量**：新增约 150 行代码，测试覆盖率保持 ≥80%
- **向后兼容**：对单工具调用或全写工具场景，行为与优化前一致

## 八、后续优化方向

1. **动态并发上限**：根据工具类型调整（如 grep 占用高，可降低并发数）
2. **更细粒度的工具分类**：区分 "快读"（read_file）和 "慢读"（grep 大目录）
3. **工具执行超时**：为每个工具设置超时，避免慢工具阻塞整个块
4. **工具执行统计**：记录每个工具的执行时间，用于后续优化决策

---

**计划制定人**: Claude (MiniCode Agent)  
**待审查**: 是  
**审查通过后可执行**: 是
