# Subagent 功能开发方案

> **计划版本:** v1.0
> **创建日期:** 2026-07-14
> **目标阶段:** Agent 编排能力升级
> **设计原则:** 并行优先、实例隔离、权限集中、结果有序汇总

## 1. 背景与目标

MiniCode 当前已经具备单 Agent ReAct 循环、工具注册器、权限确认、上下文预算、记忆系统、会话保存和 plan-before-execute 能力。缺口在于：复杂任务仍由同一个 `AgentLoop` 串行承担，主上下文会被大量探索性工具结果污染，且无法把“搜索、分析、实现、验证”等工作单元交给相互隔离的工作者执行。

Subagent 功能的目标是为 MiniCode 增加可控的任务委派能力：

- 主 Agent 可以把明确边界的子任务交给一个或多个 subagent。
- Subagent 使用独立消息历史、独立上下文预算和受限工具集执行任务。
- Subagent 只把结构化结果摘要回传给主 Agent，避免把完整探索过程灌入主上下文。
- 所有文件读写、命令执行、记忆写入等能力仍复用现有权限体系。
- 初版即支持多个 subagent 并行运行；为此必须同步改造工具注册器，消除共享工具实例带来的并发冲突。

## 2. 现有架构约束

### 2.1 当前主链路

```text
ChatApp._handle_message()
  -> AgentLoop.run(user_input)
     -> build_messages(messages, system_prompt, context_config)
     -> provider.chat(messages, tools_schema)
     -> _process_stream()
     -> _execute_tools(tool_calls)
        -> _check_tool_permission()
        -> ToolRegistry.execute_tool()
```

关键事实：

- `ChatApp` 缓存单个 `AgentLoop`，会话保存直接快照 `agent_loop.messages`。
- `AgentLoop.run()` 会在当前 loop 的 `messages` 上追加 user、assistant、tool 消息。
- `ToolRegistry` 目前持有工具实例，`execute_tool()` 执行前会把 `workspace_root` 写入工具实例；这是并行 subagent 必须先解决的共享状态问题。
- 权限判断集中在 `permissions.checker.check_permission()` 和 `PermissionConfirmer`。
- `AgentConfig` 已包含 `context` 与 `planning` 子配置，适合继续扩展 `subagents` 子配置。
- plan-before-execute 已经能在普通任务前生成计划，subagent 可以作为计划后的执行编排层。

### 2.2 对 subagent 的直接影响

| 约束 | 设计影响 |
| --- | --- |
| 单个工具实例会被写入 `workspace_root` | 必须把 `ToolRegistry` 改为工具工厂/描述符模式，每次执行创建独立工具实例，subagent 并行运行时不共享可变工具对象 |
| 权限确认需要交互 | Subagent 不能绕过权限；需要在确认 UI 中标明 subagent 名称和任务来源 |
| 主会话只保存主 `AgentLoop.messages` | Subagent 详细历史不应直接进入主会话；需要单独保存运行记录或只保存摘要 |
| 上下文裁剪按消息列表工作 | Subagent 应有独立 `ContextConfig`，避免抢占主 Agent 上下文预算 |
| plan-before-execute 已存在 | 主 Agent 可先规划，再调用 subagent；也可由模型通过工具主动委派 |
| `AgentLoop._execute_tools()` 当前串行执行工具 | 需要识别同一轮中的多个 `run_subagent` 调用，用 `asyncio.TaskGroup` 并行执行，并按原 tool_call 顺序追加结果消息 |

## 3. 功能范围

### 3.1 MVP 包含

| 能力 | 行为 |
| --- | --- |
| Subagent 配置 | `agent.subagents.enabled/max_agents/max_rounds/max_context_tokens/max_result_chars` |
| 任务委派工具 | 新增 `run_subagent` 工具，主 Agent 可传入任务、角色、允许工具和输出要求 |
| 并行调度 | 新增 `SubagentManager.run_many()`，同一轮多个 subagent 任务使用 `asyncio.TaskGroup` 并行执行 |
| 独立执行器 | 新增 `SubagentRunner`，每个 runner 创建独立消息历史、独立工具实例和独立上下文预算 |
| 工具注册器改造 | `ToolRegistry` 从持有工具实例改为持有工具工厂/描述符，执行时创建新实例，支持并行安全 |
| 受限工具集 | 按配置和调用参数过滤工具 schema，默认只允许只读工具 |
| 结果摘要 | 返回 `SubagentResult`，包含状态、摘要、发现、变更、验证建议和错误信息 |
| 运行记录 | 在 `.minicode/subagents/runs/` 保存每次运行的元数据和精简 transcript |
| CLI 可观测 | 调用时显示中文状态，例如“正在启动子代理：代码检索” |
| 测试覆盖 | 覆盖配置、工具参数校验、隔离执行、权限透传、失败处理和上下文不污染 |

### 3.2 MVP 不包含

| 暂不实现 | 原因 |
| --- | --- |
| 子任务自动拆分器 | 先由主模型调用 `run_subagent` 显式委派，避免新增不可预测调度层 |
| Subagent 之间互相通信 | 初版只允许主 Agent 汇总，减少状态复杂度 |
| 独立 Git worktree | Python CLI 项目内先保持同工作区，后续可接入 worktree 隔离 |
| 长期后台任务 | 先在一轮 ReAct 内同步完成，避免引入任务队列和恢复语义 |

## 4. 用户体验设计

### 4.1 主 Agent 自动委派

当 `agent.subagents.enabled=true` 时，主 Agent 的工具列表中出现 `run_subagent`。模型可在复杂任务中调用：

```json
{
  "name": "代码检索",
  "task": "查找当前项目中 AgentLoop 执行工具和权限确认的调用链，输出关键文件与改造点。",
  "role": "researcher",
  "allowed_tools": ["read_file", "grep_files", "glob_files"],
  "output_schema": "summary_findings",
  "max_rounds": 6
}
```

终端显示：

```text
── 正在启动子代理：代码检索 ──
任务：查找当前项目中 AgentLoop 执行工具和权限确认的调用链...
状态：已完成，用时 18.4s，工具调用 5 次
```

工具返回给主 Agent 的内容为中文摘要，不返回完整对话：

```text
子代理「代码检索」已完成。

结论：
1. AgentLoop._execute_tools 是工具执行入口。
2. 权限确认位于 AgentLoop._check_tool_permission。
3. ToolRegistry 已按工具工厂模式创建独立实例，支持并行 subagent 安全执行。

关键文件：
- src/minicode/agent/loop.py
- src/minicode/tools/registry.py
- src/minicode/permissions/checker.py
```

### 4.2 手动命令

MVP 可选新增 `/subagent` 命令，优先级低于工具能力。建议第二阶段实现：

```text
/subagent run researcher "梳理 tools 目录所有写操作工具"
/subagent list
/subagent show <run_id>
```

命令面向用户文案必须使用中文：

- “Subagent 功能未启用，请在配置中设置 agent.subagents.enabled=true。”
- “未找到运行记录。”
- “子代理执行失败：已达到最大轮次。”

## 5. 架构设计

### 5.1 新增模块

```text
src/minicode/agent/subagents/
  __init__.py
  models.py          # 配置、任务、结果、运行记录模型
  prompts.py         # subagent 系统提示词和结果格式提示词
  runner.py          # 单个 subagent 执行器
  manager.py         # 并行创建 runner、调度 run_many、保存运行记录
  tool_filter.py     # 工具白名单、角色默认工具集、schema 过滤

src/minicode/tools/subagent.py
  RunSubagentTool    # 主 Agent 调用的委派工具

src/minicode/commands/subagent_cmd.py
  SubagentCommand    # 第二阶段可选 CLI 命令
```

### 5.2 核心对象关系

```text
AgentLoop
  owns ToolRegistry
  owns Provider
  owns PermissionStore / PermissionConfirmer
  registers RunSubagentTool
        |
        v
SubagentManager
  creates SubagentRunner per task
  runs multiple runners concurrently
  saves SubagentRunRecord
        |
        v
SubagentRunner
  owns isolated messages
  owns isolated ToolRegistry scope
  reuses provider
  reuses permission components
```

### 5.3 为什么不是直接嵌套 `AgentLoop.run()`

直接在工具内部创建另一个完整 `AgentLoop` 看起来省事，但会带来几个问题：

- `AgentLoop.run()` 默认会把用户输入追加到它自己的 `messages`，但没有明确的 subagent 结果结构。
- 主 loop 和子 loop 都会使用同一个 renderer，流式输出会混在一起。
- 工具调用中的权限确认缺少 subagent 来源标识。
- `AgentLoop` 包含 plan-before-execute、memory reload、context report 等主会话语义，subagent 不一定全部需要。

建议抽出 `SubagentRunner`，复用 provider、context builder、tool executor 等能力，但让它拥有独立的运行策略和输出模型。`SubagentManager` 负责并行调度多个 runner，并保证结果按原始 tool_call 顺序回填到主消息历史。后续如果发现重复过多，再把 `AgentLoop` 与 `SubagentRunner` 共同依赖的 ReAct 执行内核下沉为 `ReactExecutor`。

### 5.4 工具注册器并行安全改造

当前 `ToolRegistry.register()` 会在注册时创建工具实例，并在执行时修改该实例的 `workspace_root`。并行 subagent 下，这会导致多个 runner 同时修改同一个工具对象。改造目标是：注册器只保存“如何创建工具”，执行时创建新的工具实例。

建议数据结构：

```python
class ToolDescriptor(BaseModel):
    """工具注册描述。"""

    model_config = {"arbitrary_types_allowed": True}

    name: str
    schema: dict
    factory: Callable[[], BaseTool]
    source: str = "builtin"


class ToolRegistry:
    def __init__(self) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}

    def register(self, tool_cls: type[BaseTool]) -> type[BaseTool]:
        probe = tool_cls()
        self._descriptors[probe.name] = ToolDescriptor(
            name=probe.name,
            schema=probe.get_tool_schema(),
            factory=tool_cls,
            source=f"{tool_cls.__module__}.{tool_cls.__qualname__}",
        )
        return tool_cls

    def register_factory(
        self,
        name: str,
        factory: Callable[[], BaseTool],
        schema: dict,
        source: str = "runtime",
    ) -> None:
        self._descriptors[name] = ToolDescriptor(
            name=name,
            schema=schema,
            factory=factory,
            source=source,
        )

    def create_tool(self, name: str) -> BaseTool:
        return self._descriptors[name].factory()
```

执行策略：

```python
async def execute_tool(self, name: str, args: dict, workspace_root: Path) -> ToolResult:
    tool = self.create_tool(name)
    tool.workspace_root = workspace_root
    return await tool.execute(**args)
```

这样每次工具调用都有独立工具实例，`workspace_root`、运行时缓存、临时状态都不会跨 subagent 共享。

兼容要求：

- `get_tools_schema()` 从 descriptor 返回 schema，不再依赖活跃工具实例。
- `tool_names` 从 descriptor key 返回。
- `get_tool()` 若仍被测试或旧代码使用，可以临时返回 `create_tool(name)`，但文档标记为“返回新实例，不保证对象身份稳定”。
- `register_tool(tool)` 需要改为兼容包装：保存该实例的 schema，并用 `lambda: copy.deepcopy(tool)` 或显式 factory 创建新实例。对于含运行时依赖的工具，优先使用 `register_factory()`。
- `RunSubagentTool` 使用 `register_factory()` 注册，factory 每次创建一个绑定同一 `SubagentManager` 的新工具对象。

Subagent 过滤不再创建“过滤视图”共享主 registry，而是创建只含允许 descriptor 的 scoped registry：

```python
scoped_registry = parent_registry.scope(allowed_tools)
```

`scope()` 返回新的 `ToolRegistry`，但 descriptor 中的 factory 仍然创建全新工具实例；因此多个 scoped registry 可以并行执行。

### 5.5 并行调度与结果顺序

模型在同一轮可能返回多个 `run_subagent` tool calls。`AgentLoop._execute_tools()` 需要特殊处理：

```text
1. 按原始顺序遍历 tool_calls
2. 将 run_subagent 调用分组
3. 非 subagent 工具按现有逻辑执行
4. 同一批 run_subagent 用 SubagentManager.run_many() 并行执行
5. 并行结果完成后，按原始 tool_call 顺序追加 ToolMessage
```

结果顺序必须稳定：即使第二个 subagent 先完成，也不能先追加到 `messages`，否则 provider 下一轮看到的 tool_call 对应关系可能错乱。

权限确认需要集中排队：

- safe 工具可直接并行执行。
- caution/dangerous 工具的确认请求进入 `PermissionPromptQueue`。
- UI 层一次只显示一个确认 prompt。
- 用户确认后，对应 subagent 才继续执行。

MVP 可以先实现最小队列：

```python
class PermissionPromptQueue:
    def __init__(self, confirmer: PermissionConfirmer) -> None:
        self._lock = asyncio.Lock()
        self._confirmer = confirmer

    async def confirm(self, decision: PermissionDecision) -> ConfirmerResult:
        async with self._lock:
            return await self._confirmer.confirm(decision)
```

## 6. 数据模型

### 6.1 配置模型

文件：`src/minicode/agent/subagents/models.py`

```python
class SubagentConfig(BaseModel):
    """Subagent 编排配置。"""

    enabled: bool = False
    """是否向主 Agent 暴露 run_subagent 工具。"""

    max_agents: int = Field(default=3, ge=1, le=8)
    """单个主任务最多允许启动的 subagent 数。"""

    concurrency: int = Field(default=3, ge=1, le=8)
    """单个主任务内允许同时运行的 subagent 数。默认等于 max_agents 上限内的并行调度。"""

    max_rounds: int = Field(default=8, ge=1, le=20)
    """单个 subagent 的最大 ReAct 轮次。"""

    max_context_tokens: int = Field(default=12000, gt=0)
    """Subagent 独立上下文预算，默认小于主 Agent。"""

    max_result_chars: int = Field(default=8000, gt=0)
    """返回给主 Agent 的结果最大字符数，防止污染主上下文。"""

    default_allowed_tools: list[str] = Field(
        default_factory=lambda: ["read_file", "grep_files", "glob_files"]
    )
    """默认只读工具集。涉及写入、shell、记忆的工具必须显式允许。"""

    allow_write_tools: bool = False
    """是否允许 subagent 使用写操作工具。MVP 默认关闭。"""
```

修改：`src/minicode/config/models.py`

```python
class AgentConfig(BaseModel):
    ...
    subagents: SubagentConfig = Field(default_factory=SubagentConfig)
```

修改：`src/minicode/config/loader.py`

新增环境变量映射：

```python
MINICODE_SUBAGENTS_ENABLED
MINICODE_SUBAGENTS_MAX_AGENTS
MINICODE_SUBAGENTS_CONCURRENCY
MINICODE_SUBAGENTS_MAX_ROUNDS
MINICODE_SUBAGENTS_MAX_CONTEXT_TOKENS
MINICODE_SUBAGENTS_MAX_RESULT_CHARS
MINICODE_SUBAGENTS_ALLOW_WRITE_TOOLS
```

### 6.2 任务与结果模型

```python
class SubagentRole(str, Enum):
    RESEARCHER = "researcher"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    TESTER = "tester"
    GENERAL = "general"


class SubagentTask(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    task: str = Field(min_length=1, max_length=8000)
    role: SubagentRole = SubagentRole.GENERAL
    allowed_tools: list[str] | None = None
    max_rounds: int | None = Field(default=None, ge=1, le=20)
    output_schema: str = "summary_findings"


class SubagentResult(BaseModel):
    run_id: str
    name: str
    role: SubagentRole
    status: Literal["completed", "failed", "cancelled", "max_rounds"]
    summary: str
    findings: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    tool_call_count: int = 0
    elapsed_ms: int = 0
```

### 6.3 运行记录模型

```python
class SubagentRunRecord(BaseModel):
    run_id: str
    parent_session_id: str | None = None
    parent_message_index: int | None = None
    name: str
    role: SubagentRole
    task: str
    status: str
    created_at: datetime
    completed_at: datetime | None = None
    allowed_tools: list[str]
    started_order: int
    completed_order: int | None = None
    result: SubagentResult | None = None
    transcript: list[Message] = Field(default_factory=list)
```

保存路径：

```text
.minicode/subagents/runs/YYYYMMDD/<run_id>.json
```

保存策略：

- 默认保存元数据、任务、结果和压缩 transcript。
- transcript 中 tool 输出超过阈值时使用现有上下文压缩策略或新增 `_compress_text()`。
- 不把 subagent 完整 transcript 追加到主 `AgentLoop.messages`。

## 7. 工具设计：`run_subagent`

### 7.1 工具 schema

文件：`src/minicode/tools/subagent.py`

```python
class RunSubagentTool(BaseTool):
    name = "run_subagent"
    description = (
        "启动一个隔离的子代理执行明确边界的子任务。"
        "适合代码检索、方案评审、测试分析等任务。"
        "子代理会返回结构化中文摘要，而不是完整对话。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "子代理名称，例如：代码检索、测试分析、方案评审",
            },
            "task": {
                "type": "string",
                "description": "交给子代理执行的具体任务，必须包含边界和期望输出",
            },
            "role": {
                "type": "string",
                "enum": ["researcher", "implementer", "reviewer", "tester", "general"],
                "description": "子代理角色",
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "允许使用的工具名称。不传则使用默认只读工具集",
            },
            "max_rounds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "最大 ReAct 轮次",
            },
            "output_schema": {
                "type": "string",
                "enum": ["summary_findings", "review_findings", "implementation_report"],
                "description": "期望输出结构",
            },
        },
        "required": ["name", "task"],
        "additionalProperties": False,
    }
```

### 7.2 工具注册策略

`create_default_registry()` 需要先完成工具工厂化改造。静态工具继续通过 `register(tool_cls)` 注册为 descriptor；运行时依赖工具通过 `register_factory()` 注册。

`RunSubagentTool` 需要拿到 `SubagentManager`、Provider、权限组件等运行时依赖，因此推荐两步：

1. `create_default_registry()` 不默认注册 `RunSubagentTool`。
2. `ChatApp._get_agent_loop()` 创建 `SubagentManager` 后，通过 `tool_registry.register_factory(...)` 注入：

```python
tool_registry.register_factory(
    name=RunSubagentTool.name,
    factory=lambda: RunSubagentTool(manager=subagent_manager),
    schema=RunSubagentTool.get_static_schema(),
    source="runtime.subagent",
)
```

当 `agent.subagents.enabled=False` 时不注册该工具，避免改变默认工具面。

### 7.3 防止递归爆炸

Subagent 的工具列表中默认禁止 `run_subagent`，避免 subagent 再创建 subagent。

后续如需支持多层委派，必须增加：

- `max_depth`
- `parent_run_id`
- 每层预算扣减
- 运行树展示

MVP 明确限制 `max_depth=1`。

## 8. SubagentRunner 执行流程

### 8.1 初始化

`SubagentRunner` 构造参数：

```python
class SubagentRunner:
    def __init__(
        self,
        provider: BaseProvider,
        tool_registry: ToolRegistry,
        renderer: StreamingRenderer,
        workspace_root: Path,
        app_config: AppConfig,
        subagent_config: SubagentConfig,
        permission_store: PermissionStore | None = None,
        permission_confirmer: PermissionConfirmer | None = None,
    ) -> None:
        ...
```

注意：

- `tool_registry` 是通过 `parent_registry.scope(allowed_tools)` 创建的 scoped registry，不共享活跃工具实例。
- 每次 `execute_tool()` 都通过 descriptor factory 创建新工具对象，多个 subagent 可以并行调用同名工具。
- `SubagentManager.run_many()` 使用 `asyncio.TaskGroup` 并行运行多个 `SubagentRunner`，通过 semaphore 按 `concurrency` 控制同时运行数量。
- `renderer` 需要包装为 `SubagentRenderer`，给输出加前缀并禁止流式正文直接刷屏。

### 8.2 执行流程

```text
run(task)
  1. 校验 subagent 配置和任务参数
  2. 计算允许工具集，过滤不存在、危险或未启用工具
  3. 从 parent ToolRegistry 创建 scoped registry
  4. 构建 subagent system prompt
  5. 初始化独立 messages = [user task]
  6. 进入受限 ReAct 循环
     6.1 build_messages(messages, subagent_system_prompt, subagent_context_config)
     6.2 provider.chat(messages, filtered_tools_schema)
     6.3 收集 assistant 文本和 tool_calls
     6.4 执行工具前做权限检查
     6.5 追加 ToolMessage 到 subagent messages
  7. 无工具调用时解析最终回答为 SubagentResult
  8. 压缩并保存 SubagentRunRecord
  9. 返回适合主 Agent 阅读的中文摘要
```

### 8.3 SubagentManager 并行流程

```text
run_many(tasks)
  1. 校验任务数量 <= max_agents
  2. 为每个任务分配 run_id 和 started_order
  3. 使用 asyncio.Semaphore(concurrency) 控制并行度
  4. 使用 asyncio.TaskGroup 启动 runner.run(task)
  5. 捕获单个 runner 异常并转为 failed SubagentResult，不取消其他任务
  6. 所有任务结束后按 started_order 排序返回
```

并行失败策略：

- 单个 subagent 失败，不影响其他 subagent。
- Provider 全局不可用时，每个任务都会得到 `failed` 结果，并带有相同错误摘要。
- 用户 Ctrl+C 时取消所有未完成 subagent，已完成结果仍保存运行记录。

### 8.4 结果格式约束

Subagent 系统提示词要求最终回答使用 JSON，然后 runner 解析为 `SubagentResult`。解析失败时使用文本 fallback：

```json
{
  "summary": "一句话总结",
  "findings": ["发现 1", "发现 2"],
  "changed_files": [],
  "verification": ["建议运行 uv run pytest tests/test_agent/test_loop.py"],
  "errors": []
}
```

Fallback 规则：

- 非空文本作为 `summary`。
- 以 `-`、`1.` 开头的行抽取为 `findings`。
- 包含 `src/`、`tests/`、`docs/` 的路径行抽取为候选文件。
- 解析失败不让工具失败，除非 provider 调用失败或达到最大轮次。

## 9. 工具隔离与权限策略

### 9.1 工具白名单

建议角色默认工具：

| 角色 | 默认工具 |
| --- | --- |
| researcher | `read_file`, `grep_files`, `glob_files` |
| reviewer | `read_file`, `grep_files`, `glob_files` |
| tester | `read_file`, `grep_files`, `glob_files`, `run_shell` |
| implementer | 默认仍只读；只有 `allow_write_tools=true` 且调用显式允许时才能写 |
| general | 使用 `default_allowed_tools` |

如果当前项目实际工具名称不同，实施时以 `ToolRegistry.tool_names` 为准，并在测试中固定。

### 9.2 写操作策略

MVP 建议默认禁止 subagent 写入。原因：

- 主 Agent 更适合作为最终修改者，便于控制 diff 和回答用户。
- 多个 subagent 写同一工作区会产生冲突。
- 权限确认交互会打断子任务节奏，初版先降低复杂度。

如果用户明确开启：

```yaml
agent:
  subagents:
    enabled: true
    allow_write_tools: true
```

还必须满足：

- `allowed_tools` 显式包含写工具。
- 权限检查不是 deny。
- 非 trust mode 下用户确认。
- 结果中必须填充 `changed_files`。

### 9.3 权限确认标识

权限确认面板需要能显示来源：

```text
来源：子代理「测试分析」
任务：运行与 AgentLoop 相关的测试
```

实现方式：

- 短期：`SubagentRunner` 在调用权限确认前，通过 renderer 输出来源上下文。
- 长期：扩展 `PermissionDecision` 增加 `source: str | None`，由确认 UI 统一展示。

### 9.4 Unknown tool 策略

`check_permission()` 对未知工具默认 dangerous。Subagent 工具过滤应更严格：

- 调用参数中出现不存在工具，直接返回参数错误。
- 过滤后工具集为空，返回“没有可用工具”的失败结果。
- Subagent 内部不暴露 `run_subagent`。

## 10. 上下文与记忆策略

### 10.1 独立上下文预算

Subagent 使用独立 `ContextConfig`：

```python
ContextConfig(
    max_input_tokens=config.agent.subagents.max_context_tokens,
    recent_messages=10,
    max_tool_output_chars=6000,
    keep_first_user_message=True,
)
```

原则：

- Subagent 的预算小于主 Agent，避免成本失控。
- 保留首条任务消息，因为它是子任务契约。
- 工具输出更积极压缩。

### 10.2 记忆读取与写入

MVP 行为：

- Subagent system prompt 可以读取当前 memory 内容，让它理解项目偏好。
- 默认不允许 subagent 调用 `remember`，避免子任务把临时发现写成长期记忆。
- 如未来允许，必须在 `SubagentResult` 中显式报告新增记忆，并经过主 Agent 或用户确认。

### 10.3 主上下文污染控制

主 Agent 只接收：

- `SubagentResult` 的格式化摘要。
- 必要文件路径和验证建议。
- 错误状态。

不接收：

- Subagent 完整 messages。
- 大段工具输出。
- 中间推理文本。

## 11. 与 plan-before-execute 的关系

推荐分层：

```text
planning: 生成主任务计划，决定是否需要拆分
subagents: 执行计划中的可隔离子任务
main agent: 汇总结果，做最终修改和用户回复
```

系统提示词可加入委派准则：

- 当任务需要独立检索多个区域时，优先委派 researcher。
- 当需要审查既有改动时，优先委派 reviewer。
- 当需要验证测试范围时，优先委派 tester。
- 不要把简单单文件修改委派给 subagent。
- 不要让 subagent 执行需要用户决策的任务。

计划文档不要求 subagent 自动根据计划启动。MVP 让主模型通过 `run_subagent` 工具自主决定，减少硬编码调度。

## 12. 文件改动清单

### 12.1 新增文件

| 文件 | 职责 |
| --- | --- |
| `src/minicode/agent/subagents/__init__.py` | Subagent 模块公开 API |
| `src/minicode/agent/subagents/models.py` | 配置、任务、结果、运行记录模型 |
| `src/minicode/agent/subagents/prompts.py` | 中文系统提示词和结果格式提示词 |
| `src/minicode/agent/subagents/tool_filter.py` | 工具白名单与 schema 过滤 |
| `src/minicode/agent/subagents/runner.py` | 单个 subagent 的受限 ReAct 执行器 |
| `src/minicode/agent/subagents/manager.py` | 并行调度、运行计数、记录保存、runner 创建 |
| `src/minicode/tools/subagent.py` | `run_subagent` 工具 |
| `tests/test_agent/test_subagent_models.py` | 模型和配置测试 |
| `tests/test_agent/test_subagent_tool_filter.py` | 工具过滤测试 |
| `tests/test_agent/test_subagent_runner.py` | runner 行为测试 |
| `tests/test_agent/test_subagent_manager.py` | 并行调度、顺序回填、失败隔离测试 |
| `tests/test_tools/test_subagent.py` | `run_subagent` 工具测试 |

### 12.2 修改文件

| 文件 | 改动 |
| --- | --- |
| `src/minicode/config/models.py` | `AgentConfig` 增加 `subagents: SubagentConfig` |
| `src/minicode/config/loader.py` | 增加默认配置和环境变量映射 |
| `src/minicode/cli/app.py` | 创建 `SubagentManager` 并注册 `RunSubagentTool` |
| `src/minicode/agent/loop.py` | 并行执行同一轮多个 `run_subagent` tool calls，并按原始顺序追加结果 |
| `src/minicode/agent/system_prompt.py` | 在启用时加入委派准则 |
| `src/minicode/tools/registry.py` | 从共享工具实例改为工具 descriptor/factory，执行时创建独立实例 |
| `src/minicode/tools/__init__.py` | 导出工具注册器新增 factory/scope API |
| `tests/test_cli/test_app.py` | 验证启用/禁用时工具注册行为 |
| `tests/test_config/test_subagent_config.py` | 配置默认值、校验和环境变量 |
| `tests/test_tools/test_registry.py` | 补充工具工厂化、scope 隔离、并行执行不共享实例的测试 |

### 12.3 第二阶段文件

| 文件 | 职责 |
| --- | --- |
| `src/minicode/commands/subagent_cmd.py` | `/subagent` 命令 |
| `tests/test_commands/test_subagent_cmd.py` | 命令测试 |

## 13. 实施阶段

### Phase 1: 配置与模型

- [ ] 新增 `SubagentConfig`、`SubagentTask`、`SubagentResult`、`SubagentRunRecord`。
- [ ] `AgentConfig` 接入 `subagents`。
- [ ] `config.loader` 增加默认值与环境变量。
- [ ] 添加配置校验测试。

验收：

- 默认 `enabled=False`，不影响现有行为。
- 非法 `max_agents=0`、`max_rounds=0` 会被 Pydantic 拒绝。
- 环境变量能正确覆盖配置。

### Phase 2: ToolRegistry 工厂化改造

- [ ] 新增 `ToolDescriptor`，保存工具名称、schema、factory 和来源。
- [ ] 修改 `ToolRegistry.register()`：注册时只保存 descriptor，不保存活跃工具实例。
- [ ] 新增 `register_factory()`，支持 `RunSubagentTool` 这类需要运行时依赖的工具。
- [ ] 修改 `execute_tool()`：每次执行都通过 factory 创建新工具实例，再注入 `workspace_root`。
- [ ] 新增 `scope(allowed_tools)`，为 subagent 创建只包含允许工具的 registry。
- [ ] 保持 `get_tools_schema()`、`tool_names`、`has_tool()` 的外部行为兼容。

验收：

- 同名工具连续执行时拿到不同工具实例。
- 多个 scoped registry 并行执行同名工具时不共享 `workspace_root` 或临时状态。
- 现有工具测试无需感知对象身份变化。
- `register_factory()` 可以注册带运行时依赖的工具。

### Phase 3: 工具过滤与运行记录

- [ ] 实现角色默认工具集。
- [ ] 实现 `filter_tools_schema()` 和 `validate_allowed_tools()`。
- [ ] 禁止 subagent 使用 `run_subagent`。
- [ ] 实现 `.minicode/subagents/runs/` 记录保存。

验收：

- 不存在的工具返回中文错误。
- 默认 researcher 只能看到只读工具。
- 运行记录文件能被写入且不包含超长 tool 输出。

### Phase 4: SubagentRunner

- [ ] 实现独立 messages 和受限 ReAct 循环。
- [ ] 复用 `build_messages()`，使用独立 `ContextConfig`。
- [ ] 复用 provider 的流式接口，但 subagent 正文默认不逐字渲染。
- [ ] 工具执行前复用权限检查逻辑。
- [ ] 使用 scoped registry 执行工具，确保每次工具调用都是独立实例。
- [ ] 解析最终输出为 `SubagentResult`。

验收：

- Subagent 执行不会修改主 `AgentLoop.messages`。
- 两个 subagent 并行调用同名工具时互不影响。
- Provider 失败时返回 `failed` 并记录错误。
- 达到 `max_rounds` 时返回 `max_rounds`，并给出中文提示。

### Phase 5: 并行调度与 `run_subagent` 工具集成

- [ ] 新增 `RunSubagentTool`。
- [ ] 实现 `SubagentManager.run_many()`，使用 `asyncio.TaskGroup` 并行运行多个 runner。
- [ ] 使用 semaphore 按 `agent.subagents.concurrency` 限制并行度。
- [ ] `ChatApp._get_agent_loop()` 在启用时创建 `SubagentManager` 并通过 `register_factory()` 注册工具。
- [ ] 修改 `AgentLoop._execute_tools()`：同一轮多个 `run_subagent` 调用并行执行，结果按原 tool_call 顺序追加。
- [ ] 增加权限确认队列，避免并行 subagent 同时抢占交互输入。
- [ ] 系统提示词加入 subagent 委派准则。
- [ ] 限制单个主任务最多启动 `max_agents` 个 subagent。

验收：

- 默认配置下工具列表没有 `run_subagent`。
- 启用后主 Agent 可以调用 `run_subagent`。
- 同一轮两个以上 `run_subagent` tool calls 会并行运行。
- 先完成的 subagent 不会提前写入主消息历史，ToolMessage 按原始 tool_call 顺序追加。
- 工具返回内容为中文结构化摘要。
- 超过 `max_agents` 返回中文错误。

### Phase 6: 测试、文档与回归

- [ ] 补充 README 配置示例。
- [ ] 添加一组集成测试模拟主 Agent 同时委派多个 researcher。
- [ ] 运行 `uv run pytest`。
- [ ] 运行 `uv run ruff check .`。
- [ ] 运行 `uv run mypy src/minicode`。

验收：

- 现有 AgentLoop 测试不需要大规模改写。
- 禁用 subagent 时现有快照和工具 schema 保持兼容。

### Phase 7: 可选 CLI 命令

- [ ] 新增 `/subagent list` 查看运行记录。
- [ ] 新增 `/subagent show <run_id>` 查看摘要。
- [ ] 新增 `/subagent run <role> <task>` 手动启动。

验收：

- 所有命令提示和错误文案为中文。
- 无记录时显示“未找到运行记录。”。

## 14. 测试矩阵

| 测试类别 | 重点用例 |
| --- | --- |
| 配置 | 默认禁用、启用、非法数值、`concurrency` 环境变量覆盖 |
| 工具注册 | descriptor/factory 注册、scope 隔离、每次执行创建新实例、启用时有 `run_subagent` |
| 参数校验 | 空任务、过长名称、未知角色、未知工具、空工具集 |
| 隔离性 | subagent messages 不进入主 messages；主 context report 不被覆盖 |
| 并行调度 | 多个 `run_subagent` 同轮并行执行、按原 tool_call 顺序回填、单个失败不取消其他任务 |
| 权限 | safe 工具直接执行；deny 工具拒绝；dangerous 工具触发确认 |
| 工具过滤 | 默认只读；写工具需要配置和显式允许；禁止递归 subagent |
| 结果解析 | 合法 JSON、非 JSON fallback、超长结果压缩 |
| 运行记录 | 成功、失败、max_rounds 均保存记录 |
| 错误处理 | ProviderError、工具异常、JSON 参数错误、记录保存失败 |
| 回归 | 现有 `AgentLoop.run()`、planning、memory、session 测试保持通过 |

## 15. 风险与应对

| 风险 | 应对 |
| --- | --- |
| 工具实例共享导致并行冲突 | Phase 2 先改造 `ToolRegistry` 为 descriptor/factory 模式，每次执行创建独立工具实例 |
| 主上下文被 subagent 输出污染 | `max_result_chars`、结构化摘要、禁止回传 transcript |
| 权限确认来源不清晰 | 确认前输出 subagent 来源；权限确认请求通过队列集中展示 |
| Subagent 成本不可控 | `max_agents`、`concurrency`、`max_rounds`、独立 context 预算、结果长度限制 |
| 模型滥用 subagent | 系统提示词明确委派准则；简单任务不委派 |
| 运行记录泄露敏感内容 | 默认压缩 transcript；后续增加敏感信息过滤和配置开关 |
| 写操作冲突 | 默认禁止写工具；允许写入时按文件路径加写锁，并要求权限确认 |

## 16. 后续演进

### 16.1 并行能力增强

MVP 已支持并行 subagent。后续增强重点是更精细的调度与 UI：

- 动态调整 `concurrency`，根据 provider 限速、任务复杂度和工具类型决定并行度。
- `StreamingRenderer` 支持按 subagent 来源分组展示进度。
- 对写操作引入文件级锁，允许不冲突的实现型 subagent 并行修改不同文件。
- 对 provider 调用增加全局速率限制，避免并行请求触发 429。

### 16.2 自动拆分

新增 `SubtaskPlanner`：

- 输入主计划和用户目标。
- 输出 `list[SubagentTask]`。
- 主 Agent 决定是否接受拆分，或由配置控制自动运行。

### 16.3 工作区隔离

复杂实现任务可使用独立 Git worktree：

- researcher/reviewer 仍共享当前工作区只读。
- implementer 使用临时 worktree 写入。
- 主 Agent 汇总 patch 或让用户选择合并。

### 16.4 运行树与恢复

将主会话、subagent run、工具调用串成运行树：

```text
session
  message
    subagent_run
      tool_call
      result
```

后续可支持 `/subagent resume <run_id>`。

## 17. 推荐落地顺序

优先做最小闭环：

1. 配置与模型。
2. `ToolRegistry` 工厂化与 scoped registry。
3. 工具过滤与运行记录。
4. `SubagentRunner` 受限执行。
5. `SubagentManager.run_many()` 并行调度。
6. `run_subagent` 工具注册与 `AgentLoop._execute_tools()` 并行集成。
7. 运行并行集成测试与回归检查。

暂缓：

1. 自动拆分。
2. Subagent 之间互相通信。
3. 独立 Git worktree。
4. `/subagent` 完整命令。

这样可以先让 MiniCode 获得真正可用的“并行隔离研究员”能力，同时通过工具工厂化保证并行运行不会破坏当前单 Agent 主链路。
