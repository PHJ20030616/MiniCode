# 上下文窗口管理 — MVP 实施计划

> **计划版本：** v1 — MVP
> **对应阶段：** 2.0 阶段 10（上下文工程）
> **设计原则：** 先正确，后优化；先可见，后精细

## 1. 范围与边界

### 包含（MVP）

| 模块 | 交付物 | 复杂度 |
|------|--------|--------|
| 词元估算器 | 基于字符比例的轻量估算函数 | 低 |
| 上下文数据模型 | `ContextConfig`、`ContextBuildReport`、`ContextBuildResult` | 低 |
| 上下文配置接入 | `AgentConfig` 子模型、配置加载、环境变量 | 低 |
| 上下文构建器 | 工具输出压缩 + 旧消息裁剪（简单丢弃） | 中 |
| AgentLoop 集成 | 循环内调用 build_messages + 报告保存 | 低 |
| `/context` 命令 | 查看上下文诊断统计 | 低 |

### 不包含（后续迭代）

| 暂缓项 | 原因 |
|--------|------|
| Exchange 组感知裁剪 | 需理解消息间引用关系，MVP 按单条消息丢弃 |
| 记忆内容参与预算 | 记忆注入与 context builder 解耦，可后续调整 |
| 懒惰/增量上下文重建 | 性能优化，功能正确性优先 |
| 内容类型感知压缩 | 高投入低收益，head/tail 截断对 MVP 够用 |
| 压缩幂等性检测 | 边缘场景，后续迭代补 |

## 2. 文件改动清单

### 新增文件

| 文件 | 职责 |
|------|------|
| `src/minicode/agent/token_estimator.py` | 词元估算函数 |
| `src/minicode/agent/context_models.py` | 上下文配置与报告模型 |
| `src/minicode/commands/context_cmd.py` | `/context` 命令实现 |
| `tests/test_agent/test_context.py` | 估算器+构建器+压缩测试 |
| `tests/test_commands/test_context.py` | `/context` 命令测试 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/minicode/agent/context.py` | 从简单前置升级为带预算的上下文构建 |
| `src/minicode/agent/loop.py` | 集成 ContextBuildResult，暴露 last_context_report |
| `src/minicode/config/models.py` | AgentConfig 增加 ContextConfig 子字段 |
| `src/minicode/config/loader.py` | 增加上下文环境变量映射 |
| `src/minicode/commands/__init__.py` | 注册 ContextCommand |

## 3. 实现步骤

### 步骤 1：词元估算器

**文件：** `src/minicode/agent/token_estimator.py`

提供三个估算函数，基于保守的 4 字符/token 比例。

```python
CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD_TOKENS = 4

def estimate_tokens(text: str | None) -> int
def estimate_message_tokens(message: Message) -> int
def estimate_messages_tokens(messages: list[Message]) -> int
```

**关键行为：**
- `estimate_tokens`：空/None 返回 0，否则 `max(1, ceil(len(text)/4))`
- `estimate_message_tokens`：基础开销 4 + content + role + name + tool_call_id + tool_calls JSON
- `estimate_messages_tokens`：逐条累加

**测试要点：**
- 空文本返回 0
- 4 字符 = 1 token，5 字符 = 2 tokens
- Message 估算 >= content 估算
- ToolMessage 包含 name 和 tool_call_id 开销
- 带 tool_calls 的消息额外累加

### 步骤 2：上下文数据模型

**文件：** `src/minicode/agent/context_models.py`

```python
class ContextConfig(BaseModel):
    max_input_tokens: int = 24000       # 总输入预算（含 system prompt）
    recent_messages: int = 16            # 尾部保留的消息数
    max_tool_output_chars: int = 12000   # 单条工具输出压缩阈值
    keep_first_user_message: bool = True  # 是否保留首条用户消息

class ContextBuildReport(BaseModel):
    original_message_count: int
    final_message_count: int
    original_estimated_tokens: int
    final_estimated_tokens: int
    dropped_message_count: int = 0
    compressed_tool_result_count: int = 0

class ContextBuildResult(BaseModel):
    messages: list[Message]
    report: ContextBuildReport
```

### 步骤 3：配置接入

**修改文件：** `src/minicode/config/models.py`

```python
class AgentConfig(BaseModel):
    max_rounds: int = 20
    stream: bool = True
    context: ContextConfig = Field(default_factory=ContextConfig)
```

**修改文件：** `src/minicode/config/loader.py`

```python
# ENV_CONFIG_MAP 新增
"MINICODE_CONTEXT_MAX_INPUT_TOKENS": ("agent", "context", "max_input_tokens"),
"MINICODE_CONTEXT_RECENT_MESSAGES": ("agent", "context", "recent_messages"),
"MINICODE_CONTEXT_MAX_TOOL_OUTPUT_CHARS": ("agent", "context", "max_tool_output_chars"),

# _get_defaults() 新增
"agent": {
    "context": {
        "max_input_tokens": 24000,
        "recent_messages": 16,
        "max_tool_output_chars": 12000,
        "keep_first_user_message": True,
    },
},
```

### 步骤 4：上下文构建器

**文件：** `src/minicode/agent/context.py`

核心逻辑：

```python
def build_messages(
    messages: list[Message],
    system_prompt: str,
    context_config: ContextConfig | None = None,
) -> ContextBuildResult: ...
```

**构建流程：**

```
输入: messages + system_prompt + context_config

1. 构造 system_message + messages（原始列表）
2. 统计 original_message_count 和 original_estimated_tokens
3. 对 tool 消息调用 _compress_text() 压缩（受 max_tool_output_chars 限制）
4. 如果压缩后仍超预算：
   a. 保留 system message
   b. 如 keep_first_user_message，标记首条 user 消息为受保护
   c. 保留尾部 recent_messages 条消息为受保护
   d. 从中间（受保护区域之间）逐条丢弃消息，直到满足预算
   e. 先丢弃 tool 消息，再丢弃 assistant 消息，最后丢弃 user 消息（受保护的除外）
5. 统计 final_message_count 和 final_estimated_tokens
6. 返回 ContextBuildResult(messages=working, report=report)
```

**压缩辅助函数：**

```python
def _compress_text(text: str, max_chars: int) -> tuple[str, bool]:
    """对超长文本做 head/tail 截断。返回(压缩后文本, 是否压缩)。"""
    if not text or len(text) <= max_chars:
        return text, False
    head_len = max_chars // 2
    tail_len = max_chars - head_len
    omitted = len(text) - head_len - tail_len
    compressed = (
        text[:head_len]
        + f"\n\n[tool output truncated: {omitted} chars omitted]\n\n"
        + text[-tail_len:]
    )
    return compressed, True
```

**仅对 role == "tool" 的消息执行压缩。**

### 步骤 5：AgentLoop 集成

**修改文件：** `src/minicode/agent/loop.py`

```python
class AgentLoop:
    def __init__(self, ...):
        ...
        self.last_context_report: ContextBuildReport | None = None

    async def run(self, user_input: str) -> str | None:
        ...
        for round_num in ...:
            # 替换：
            # api_messages = build_messages(self.messages, self.system_prompt)
            # 为：
            context_result = build_messages(
                self.messages,
                self.system_prompt,
                self.config.agent.context,
            )
            self.last_context_report = context_result.report
            api_messages = context_result.messages
            ...
```

### 步骤 6：`/context` 命令

**文件：** `src/minicode/commands/context_cmd.py`

```python
class ContextCommand(BaseCommand):
    name = "context"
    aliases = ["ctx"]
    description = "查看当前上下文窗口状态。"
    usage = "/context"
```

**显示内容：**
- 原始消息数 / 发送消息数
- 原始估算词元 / 发送估算词元
- 裁剪消息数
- 压缩工具结果数

**无对话时提示** "尚未开始对话，暂无上下文统计。"

**注册：** 在 `src/minicode/commands/__init__.py` 中导入并注册。

## 4. 消息丢弃算法详述

`_drop_old_messages_to_budget()` 的具体实现：

```
输入: messages (含 system message), config

1. 计算当前估算 token 数。如在预算内，直接返回。
2. 构建 protected 集合：
   - index 0（system message）永远受保护
   - 如 keep_first_user_message，保护第一条 role=user 的消息
   - 保护尾部 recent_messages 条消息
3. 构建候选丢弃列表（未受保护的消息），按优先级排序：
   - 第一优先级：role="tool" 的消息
   - 第二优先级：role="assistant" 的消息（不含 tool_calls）
   - 第三优先级：role="user" 的消息
   - 同优先级内按 index 从旧到新
4. 遍历候选列表，逐条丢弃，每次重算 token 数。
5. 当预算满足或无可丢弃消息时停止。
6. 返回裁剪后的消息列表。
```

**注意事项：**
- 裁剪后至少保留 system message + 1 条其他消息，避免发送空对话
- 预算配置不合理时（如 `max_input_tokens` 小于 system message），以合理的最小值（150 tokens）保底

## 5. 测试策略

### 估算器测试（`test_token_estimator.py`）
- 空/None 文本
- 字符到 token 的 ceil 计算
- message 结构开销
- ToolMessage 额外字段
- 多条累加

### 上下文模型测试（`test_context_models.py`）
- 默认值
- 序列化/反序列化
- Report 字段计算

### 配置测试（`test_config.py`）
- `ContextConfig` 默认值
- 环境变量覆盖

### 上下文构建器测试（`test_context_builder.py`）
- 基础：system message 前置
- 压缩：长 tool output 被截断
- 裁剪：超预算时丢弃旧消息
- 保护：首条用户消息保留
- 保护：尾部 recent_messages 保留
- 边界：空消息列表
- 边界：所有消息都受保护（不裁剪到空）
- 边界：压缩后仍在预算内（不裁剪）
- Report 正确性：计数一致

### AgentLoop 集成测试（`test_loop.py`）
- `last_context_report` 在 run 后被填充
- report 字段合理

### `/context` 命令测试（`test_context_command.py`）
- 无 AgentLoop 时提示
- 有报告时显示所有字段

## 6. 验收标准

```powershell
# 全部测试通过
uv run pytest tests/test_agent/test_context.py tests/test_commands/test_context.py -v

# 覆盖率无显著下降
uv run pytest --cov=minicode --cov-report=term-missing

# lint 与类型检查
uv run ruff check .
uv run mypy src/minicode
```

**手动验收：**
1. 启动 minicode，输入长对话触发上下文裁剪
2. 输入 `/context` 看到统计数据
3. 确认输出不包含被丢弃的消息内容

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 消息丢弃打乱对话结构 | 模型理解偏离 | MVP 接受此风险，后续迭代增加 exchange 组感知 |
| 估算器不准导致过早/过晚裁剪 | 预算浪费或爆窗 | 使用保守比例 4:1，保留 20% 缓冲余量 |
| 系统提示词超大影响预算 | 历史消息被过度裁剪 | 后续迭代将 system prompt 纳入预算计算 |
