# MiniCode Prompt 组织架构设计

> 日期：2026-07-20
> 状态：已确认

## 1. 背景

MiniCode 当前用于 LLM 调用的 prompt 数量不多，但已经分散在多个 Agent
实现模块中：

- `src/minicode/agent/system_prompt.py`：主 Agent 系统提示词。
- `src/minicode/agent/planner.py`：任务规划提示词。
- `src/minicode/agent/compaction.py`：上下文摘要提示词和摘要包装说明。
- `src/minicode/agent/subagents/prompts.py`：子代理系统提示词。

这些 prompt 直接嵌入各自的业务模块，并混合了静态文案、条件判断、业务模型
访问和消息组装逻辑。随着 Agent 能力增加，这种组织方式会导致以下问题：

- 难以发现项目中有哪些 prompt，以及每个 prompt 的用途。
- 公共规则和格式约束无法复用。
- prompt 修改需要理解执行器、Provider 或工具注册器等无关实现。
- 测试主要依附业务模块，缺少 prompt 自身的契约测试。
- 后续增加新 Agent 场景时容易继续复制字符串拼接模式。

本次只建立代码内的统一组织架构，不支持通过 `.minicode/` 文件覆盖或追加
prompt。外部定制属于后续独立功能。

## 2. 目标

本次改造需要达成以下目标：

1. 所有发送给 LLM 的核心 prompt 都能从统一包中发现。
2. 按主 Agent、规划、压缩和子代理四个场景分模块维护。
3. prompt 构建逻辑使用纯函数和简单输入模型，不依赖 Agent 执行器。
4. 统一可选章节、段落间距和动态列表的组合方式。
5. 保持现有 prompt 语义和调用行为，避免在架构迁移中混入策略调整。
6. 为静态内容、动态内容和条件章节建立直接的契约测试。
7. 保留必要的旧导入入口，降低迁移风险。

## 3. 非目标

本次不实现以下能力：

- 工作区或全局 prompt 覆盖。
- prompt 热加载。
- prompt 版本选择、A/B 测试或运行时注册中心。
- Jinja2 等第三方模板引擎。
- prompt 效果评测框架。
- 主 Agent、规划器、压缩器或子代理的行为策略调整。
- Provider 消息协议重构。

## 4. 方案选择

采用按场景分模块的 `minicode.prompts` 包，而不是单一大文件或运行时模板
注册中心。

单一文件虽然迁移成本最低，但会把现有分散问题变成新的集中拥挤问题。运行时
注册中心适合外部覆盖、版本管理和动态发现，但当前没有这些需求，会引入多余的
标识符、查找失败和生命周期管理。

领域化包与当前项目规模匹配：入口统一，同时让每类 prompt 保持清晰边界。

## 5. 模块结构

```text
src/minicode/prompts/
  __init__.py
  composition.py
  models.py
  main_agent.py
  planning.py
  compaction.py
  subagent.py
```

### 5.1 `composition.py`

提供无业务含义的文本组合函数：

- 清理章节首尾空白。
- 忽略 `None` 和空章节。
- 使用固定的双换行连接章节。
- 渲染名称与描述组成的列表。

该模块不解析模板语法，也不对内容执行通用 `str.format()`。JSON 示例包含大量
花括号，显式构建比通用模板替换更容易审查，也能减少转义错误。

### 5.2 `models.py`

定义 prompt 构建所需的轻量不可变输入模型，例如：

```python
@dataclass(frozen=True)
class ToolPromptInfo:
    name: str
    description: str
```

这些模型只表达 prompt 所需数据，不引用 `ToolRegistry`、`SubagentTask`、
`Message` 或 Provider 类型。

### 5.3 `main_agent.py`

负责主 Agent prompt 的以下章节：

- 身份和基础行为。
- 可用工具列表及使用说明。
- `remember` 工具规则。
- 子代理委派准则。
- 用户记忆及可信度警告。

公开构建函数接收工具描述、记忆内容和功能开关。调用方必须先完成工具过滤，
prompt 层不会访问注册器或配置对象。

### 5.4 `planning.py`

负责任务规划阶段的系统提示词和 JSON 输出契约。初次迁移保持现有内容不变。
若未来需要把 `max_steps` 写入提示词，可将常量升级为带明确参数的构建函数，
无需修改规划器之外的其他 prompt 模块。

### 5.5 `compaction.py`

负责：

- 历史摘要系统提示词。
- 摘要请求中的关注说明和历史快照边界。
- 压缩摘要注入主会话时的包装前缀。

历史消息序列化仍属于 `agent.compaction`，因为它依赖内部 `Message` 字段和
压缩协议。prompt 包只接收已经序列化好的历史快照字符串并生成文本。

### 5.6 `subagent.py`

负责子代理身份、角色信息、允许工具、任务边界和最终 JSON 结果契约。构建函数
只接收字符串和字符串序列，不依赖 `SubagentTask` 或枚举模型。

## 6. 依赖方向

依赖关系固定为：

```text
Agent / Planner / Compactor / Subagent Runner
                    |
                    v
             minicode.prompts
                    |
                    v
              Python 标准库
```

`minicode.prompts` 不反向依赖 Agent、Provider、工具、配置、会话或 CLI 层。
这保证 prompt 可以独立导入、测试和复用。

## 7. 公开 API

`minicode.prompts.__init__` 只导出稳定入口，调用方不需要了解具体文件：

```python
from minicode.prompts import (
    PLANNING_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_WRAPPER_PREFIX,
    ToolPromptInfo,
    build_main_agent_prompt,
    build_subagent_prompt,
    build_summary_user_prompt,
)
```

静态 prompt 使用大写常量；包含运行时数据的 prompt 使用 `build_*_prompt`
函数。内部章节常量不从包根目录导出。

## 8. 兼容与迁移

### 8.1 主 Agent

保留 `src/minicode/agent/system_prompt.py`，但将其改为适配层：

1. 从 `ToolRegistry` 提取经过记忆开关过滤的工具。
2. 转换为 `ToolPromptInfo`。
3. 调用 `build_main_agent_prompt()`。

`build_system_prompt()` 的签名保持不变，现有调用方和测试无需同步迁移。

### 8.2 规划器

把 `PLANNING_SYSTEM_PROMPT` 移到 `minicode.prompts.planning`，规划器和
`AgentLoop` 从统一 prompt 包导入。为减少无意义的兼容面，不再由
`agent.planner` 拥有该常量。

### 8.3 上下文压缩

把摘要静态规则、包装前缀和用户 prompt 构建迁移到
`minicode.prompts.compaction`。`agent.compaction` 继续负责：

- 序列化历史快照。
- 创建 `Message`。
- 调用 Provider。
- 校验摘要结果。

### 8.4 子代理

保留 `agent.subagents.prompts.build_subagent_system_prompt()` 作为适配层，
将 `SubagentTask` 展开后调用 `build_subagent_prompt()`。现有 Runner 不需要
了解新输入模型。

## 9. 数据流

### 9.1 主 Agent

```text
ToolRegistry + memory settings
  -> agent.system_prompt adapter
  -> ToolPromptInfo sequence
  -> build_main_agent_prompt()
  -> AgentLoop.system_prompt
  -> context builder
  -> Provider
```

### 9.2 上下文压缩

```text
old Message sequence
  -> agent.compaction._history_snapshot()
  -> serialized snapshot
  -> build_summary_user_prompt()
  -> system/user Message pair
  -> Provider
```

### 9.3 子代理

```text
SubagentTask + allowed tools
  -> subagent prompt adapter
  -> scalar prompt inputs
  -> build_subagent_prompt()
  -> SubagentRunner context
  -> Provider
```

## 10. 格式与安全约束

- 动态章节必须通过显式参数注入，不读取全局状态。
- `None` 或纯空白可选内容不得生成空标题或多余分隔。
- 工具名称和描述按调用方提供的顺序渲染。
- 历史快照继续放在显式 `<history_snapshot>` 边界内。
- 压缩关注说明继续放在 `<focus>` 边界内，并保留固定规则优先声明。
- 用户记忆继续带有“不完整或过期”的警告。
- 子代理最终结果继续要求只输出 JSON 对象。
- 本次不改变 prompt 文案的语言、规则强度或输出结构。

## 11. 测试策略

新增 `tests/test_prompts/`：

- `test_composition.py`：空章节过滤、段落间距和列表顺序。
- `test_main_agent.py`：无工具、工具列表、记忆开关、记忆注入和子代理规则。
- `test_planning.py`：规划角色、禁止工具和 JSON 输出结构。
- `test_compaction.py`：系统安全规则、关注说明、历史边界和包装前缀。
- `test_subagent.py`：任务信息、允许工具、执行要求和结果 JSON 契约。

现有测试继续承担集成契约：

- `tests/test_agent/test_system_prompt.py`
- `tests/test_agent/test_planner.py`
- `tests/test_agent/test_compaction.py`
- `tests/test_agent/test_subagent_runner.py`
- `tests/test_agent/test_loop.py`
- `tests/test_memory/test_integration.py`

实现使用 TDD：先新增 prompt 包契约测试并确认因模块缺失而失败，再实现最小
代码，最后迁移调用方并运行完整回归。

## 12. 验收标准

完成后必须满足：

1. `src/minicode` 中所有核心 LLM prompt 均定义在 `minicode.prompts`。
2. Agent 业务模块不再包含大段系统 prompt 文案。
3. prompt 包不依赖 Agent、Provider、工具、配置、会话或 CLI 模块。
4. `build_system_prompt()` 和 `build_subagent_system_prompt()` 保持兼容。
5. 主 Agent、规划、压缩和子代理现有行为测试保持通过。
6. 新增 prompt 单元测试覆盖所有条件章节和动态边界。
7. `uv run pytest`、`uv run ruff check .` 和
   `uv run mypy src/minicode` 全部通过。

## 13. 后续演进

本次架构为后续能力保留清晰扩展点，但不提前实现：

- 工作区追加 prompt。
- 完整模板覆盖与回退。
- prompt 版本和变更记录。
- 模型或 Provider 专用 prompt。
- 离线效果评测和快照对比。

这些能力应建立在本次纯函数构建器和稳定测试契约之上，避免重新耦合到 Agent
执行逻辑。
