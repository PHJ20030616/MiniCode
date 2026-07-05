# MiniCode — 任务计划书

> 从零构建一个简化版 Claude Code 的 Python CLI 工具，用于 AI 辅助编程。
> 开源项目，面向求职简历，注重工程质量、可运行主线和清晰架构。
>
> **详细设计文档**：参见 `doc/minicode-design.md`

---

## 执行策略

这个计划按可交付版本拆分，而不是按“完整功能清单”一次性铺开。每个版本结束时都必须有一个可运行、可测试、可演示的产品。

> **Provider 命名口径：** 文档中的 `OpenAI-compatible` 指协议和 SDK 兼容层，并不限定最终接入 OpenAI 官方接口。最终真实接口验收统一使用 DeepSeek 的 OpenAI-compatible API；单元测试仍以 mock client 验证协议映射。

| 版本 | 目标 | 不做 |
|------|------|------|
| v0.1 | OpenAI-compatible 流式对话 + 只读工具 + ReAct 主闭环 | 写文件、执行 shell、记忆、多 Provider |
| v0.2 | 写入/编辑/shell 工具 + 参数级权限 + 会话持久化 | Anthropic、复杂 TUI、发布 |
| v0.3 | 斜杠命令和日常会话操作 | 记忆系统、Anthropic、发布 |
| v0.4 | 记忆系统、Provider/Model 切换体验 | PyPI 正式发布 |
| v1.0 | CI、文档、错误处理、开源发布质量 | 新增大功能 |

## 任务执行原则

1. **主线优先**：先让“用户提问 -> 模型读项目文件 -> 回答”稳定跑通。
2. **MVP 边界硬化**：v0.1 只做只读工具，避免权限和破坏性操作拖慢主线。
3. **测试跟随开发**：每个 Phase 都有自己的测试 gate，不把质量集中推迟到最后。
4. **Provider 简化**：v0.1 只支持 OpenAI-compatible API，默认按 DeepSeek 兼容接口完成真实验收；工具调用可以先非流式，文本回复保持流式。
5. **安全默认值**：所有文件工具默认限制在 workspace root 内，敏感文件默认拒绝读取。
6. **手动验收不可替代单测**：手动测试只验证真实体验，核心逻辑必须有自动化测试。

---

## v0.1：只读 Agent MVP

> **目标：** 启动 `minicode` 后，用户可以和模型流式对话；模型可以读取/搜索当前项目文件并回答问题。

### Phase 0：项目初始化与基础设施

#### Task 0.1 — 初始化项目结构

**要求：**
- 使用 `uv` 创建 Python 3.12+ 项目。
- 采用 `src/minicode/` 包结构。
- 创建测试目录和基础配置文件。
- `.gitignore` 忽略 `.minicode/`、虚拟环境、缓存、日志。

**推荐依赖：**
```bash
uv init --package MiniCode
uv add rich prompt-toolkit openai httpx pydantic structlog pyyaml typer
uv add --dev pytest pytest-asyncio pytest-mock pytest-cov mypy ruff
```

**关键文件：**
- `pyproject.toml`
- `src/minicode/__init__.py`
- `src/minicode/main.py`
- `tests/conftest.py`

**验收标准：**
- `uv run python -c "import minicode"` 不报错。
- `uv run pytest` 可以启动并通过空测试或 smoke test。

#### Task 0.2 — CLI 入口与最小参数解析

**要求：**
- 使用 Typer 实现 `minicode/main.py`。
- v0.1 支持参数：
  - `--model` / `-m`
  - `--provider` / `-p`
  - `--config`
  - `--workspace`
  - `--debug`
  - `--version`
- 暂不实现 `--session`、`--trust`、`--list-sessions`，这些进入 v0.2/v0.3。

**验收标准：**
- `uv run minicode --version` 输出版本信息。
- `uv run minicode --help` 能看到参数说明。

#### Task 0.3 — 配置系统

**要求：**
- 实现多层配置加载：
  1. 代码默认值
  2. `~/.minicode/config.yaml`
  3. `./.minicode/config.yaml`
  4. 环境变量
  5. CLI 参数
- 支持 `${ENV_VAR}` 占位符解析。
- v0.1 只要求 OpenAI-compatible Provider 配置。

**关键文件：**
- `src/minicode/config/models.py`
- `src/minicode/config/loader.py`
- `tests/test_config/test_loader.py`

**验收标准：**
- 单元测试覆盖默认配置、全局配置、项目配置、环境变量、CLI 覆盖优先级。
- 缺少 API key 时给出清晰错误，不进入 Agent Loop。

#### Task 0.4 — 日志与异常

**要求：**
- 实现基础异常类：`MiniCodeError`、`ConfigError`、`ProviderError`、`ToolError`。
- 控制台默认只显示用户需要的信息。
- `--debug` 时将结构化日志写入 `.minicode/logs/`。

**验收标准：**
- 配置错误不会打印长 traceback。
- debug 模式下日志文件包含 provider、model、workspace、错误类型。

---

### Phase 1：流式文本对话

#### Task 1.1 — Provider 抽象

**要求：**
- 定义内部消息模型：`Message`、`ToolCall`、`FunctionCall`、`ToolMessage`。
- 定义 `BaseProvider`。
- `chat()` 支持 `tools=None` 的纯文本流式对话。
- v0.1 允许工具调用阶段使用非流式响应，降低增量 tool_call 解析复杂度。
- 做一次 Anthropic 格式适配可行性验证：不实现真实 Anthropic Provider，只写 mock 转换测试，确认内部模型没有强依赖 OpenAI 独有字段。

**关键文件：**
- `src/minicode/providers/base.py`
- `src/minicode/providers/registry.py`
- `tests/test_providers/test_registry.py`
- `tests/test_providers/test_message_contract.py`

**验收标准：**
- mock provider 可以注册、获取、返回文本 chunk。
- 类型模型能序列化/反序列化普通 user/assistant/tool 消息。
- mock Anthropic 转换测试能覆盖 system prompt、普通消息、assistant tool call、tool result 四类结构。

#### Task 1.2 — OpenAI-compatible Provider

**要求：**
- 使用 `openai.AsyncOpenAI` 实现 OpenAI-compatible API。
- 支持 `api_key`、`base_url`、`model`。
- 文本输出使用流式。
- 网络超时、401、429、5xx 转换为友好错误。

**关键文件：**
- `src/minicode/providers/openai_compatible.py`
- `tests/test_providers/test_openai_compatible.py`

**验收标准：**
- 单元测试用 mock client 验证文本 delta 被正确转换为内部 chunk。
- 最终手动验收使用 DeepSeek 的 OpenAI-compatible 接口完成一次纯文本对话；如需排查兼容性，可补充使用 OpenAI 官方接口或兼容中转站。

#### Task 1.3 — CLI 输入与渲染

**要求：**
- 使用 `prompt_toolkit` 接收单行输入。
- 使用 Rich 渲染 Markdown 和代码块。
- v0.1 不要求复杂多行编辑、状态栏、补全。

**关键文件：**
- `src/minicode/cli/app.py`
- `src/minicode/cli/renderer.py`
- `src/minicode/cli/theme.py`

**验收标准：**
- 启动 `minicode`，输入 `Hello`，看到模型流式回复。
- 输入 `exit` 或 Ctrl+C 能优雅退出。

---

### Phase 2：只读工具与 ReAct 闭环

#### Task 2.1 — 工具基础设施

**要求：**
- 实现 `BaseTool`、`ToolResult`、`ToolRegistry`。
- 工具 schema 输出兼容 OpenAI function tools。
- 所有工具接收 `workspace_root`，默认禁止访问 workspace 外路径。

**关键文件：**
- `src/minicode/tools/base.py`
- `src/minicode/tools/registry.py`
- `src/minicode/tools/path_safety.py`
- `tests/test_tools/test_registry.py`
- `tests/test_tools/test_path_safety.py`

**验收标准：**
- `get_tools_schema()` 输出可直接传给 OpenAI-compatible API。
- 路径 `../outside.txt`、绝对路径指向 workspace 外时被拒绝。
- `.env`、`.ssh`、常见密钥文件默认拒绝读取。

#### Task 2.2 — 文本文件读取工具

**要求：**
- 实现 `read_file`。
- 支持 `file_path`、`offset`、`limit`。
- 只支持文本文件（包括各种代码文件）；PDF/图片进入后续版本。
- 输出做长度截断，默认最多 20,000 字符。

**关键文件：**
- `src/minicode/tools/file_read.py`
- `tests/test_tools/test_file_read.py`

**验收标准：**
- 覆盖成功读取、不存在、目录、越界行范围、超长截断、敏感文件拒绝。

#### Task 2.3 — 搜索与文件匹配工具

**要求：**
- 实现 `glob`：支持 `**/*.py` 这类模式，按路径排序。
- 实现 `grep`：优先调用 `rg`，不可用时 fallback 到 Python `re` + `Path.rglob`。
- v0.1 不实现多行 grep，只支持常规单行匹配。
- 输出默认最多 250 行。

**关键文件：**
- `src/minicode/tools/glob.py`
- `src/minicode/tools/grep.py`
- `tests/test_tools/test_glob.py`
- `tests/test_tools/test_grep.py`

**验收标准：**
- 有 `rg` 和无 `rg` 两种路径均有测试。
- 搜索不会越出 workspace。
- 输出过长时明确说明已截断。

#### Task 2.4 — ReAct Agent Loop

**要求：**
- 构建 `system + history + user` messages。
- 调用 provider，允许模型返回 tool calls。
- 串行执行工具。
- 将工具结果追加为 tool message 后继续调用模型。
- 最大轮次默认 8，超过后停止并提示用户。
- 最大轮次从配置读取，默认 8；后续用户可通过配置文件调整。
- v0.1 工具调用阶段可以不做流式 tool_call delta；优先正确性。

**关键文件：**
- `src/minicode/agent/loop.py`
- `src/minicode/agent/system_prompt.py`
- `src/minicode/agent/context.py`
- `tests/test_agent/test_loop.py`

**验收标准：**
- mock provider 测试覆盖：无工具直接回答、一次 read_file 后回答、多工具串行、工具错误返回给模型、超过最大轮次。
- 集成测试覆盖：Agent Loop + mock Provider + ToolRegistry + 临时 workspace 完成一次“读取文件后总结”的完整链路。
- 手动测试：要求模型“读取 README.md 并总结内容”，能完成。

#### v0.1 发布 Gate

必须全部满足：
- `uv run ruff check .` 通过。
- `uv run mypy src/minicode` 通过，允许在 pyproject 中先采用适度严格配置，不强行 `strict = true`。
- `uv run pytest --cov=src/minicode --cov-report=term` 覆盖率不低于 60%。
- 至少 1 个关键路径集成测试通过：Agent Loop + mock Provider + 只读工具。
- 使用 DeepSeek OpenAI-compatible 接口完成一次真实手动验收：普通流式对话，以及“读取 README.md 并总结内容”的只读 Agent 链路。
- README 包含安装、配置、v0.1 功能和限制。
- 提交 `uv.lock`，保证依赖可复现。

---

## v0.2：写入能力、权限与会话

> **目标：** MiniCode 可以安全地修改项目文件，并能保存/恢复会话。

### Phase 3：参数级权限系统

#### Task 3.1 — 权限模型

**要求：**
- 权限判断不只看工具名，还要看参数。
- 判断维度：
  - 路径是否在 workspace 内
  - 是否覆盖已有文件
  - 是否访问敏感文件
  - 是否删除或批量修改
  - shell 命令是否包含明显危险操作
- `--trust` 在 v0.2 引入，但仍不允许默认读取敏感文件。

**关键文件：**
- `src/minicode/permissions/checker.py`
- `src/minicode/permissions/models.py`
- `tests/test_permissions/test_checker.py`

**验收标准：**
- 单测覆盖 safe/caution/dangerous/deny 四类结果。
- 权限提示中包含工具名、目标路径、操作摘要。

#### Task 3.2 — 权限确认交互

**要求：**
- 工具执行前需要确认时，支持 `[y] allow`、`[n] deny`、`[a] always allow this pattern`。
- always allow 存储在 `.minicode/permissions.json`。
- 存储项必须包含工具名、路径 pattern、创建时间。

**关键文件：**
- `src/minicode/permissions/store.py`
- `src/minicode/cli/confirm.py`
- `tests/test_permissions/test_store.py`

**验收标准：**
- 拒绝后工具不会执行，并将拒绝结果返回给模型。
- always allow 只匹配同一工具和同一安全路径范围。

### Phase 4：写入、编辑与 shell 工具

#### Task 4.1 — 写文件工具

**要求：**
- 实现 `write_file`。
- 支持创建文件、覆盖文件、自动创建父目录。
- 覆盖已有文件必须触发权限确认。

**验收标准：**
- 测试覆盖新建、覆盖、父目录创建、workspace 外拒绝、敏感路径拒绝。

#### Task 4.2 — 精确编辑工具

**要求：**
- 实现 `edit_file`。
- 参数：`file_path`、`old_string`、`new_string`、`replace_all`。
- `replace_all=false` 时 `old_string` 必须唯一。
- 编辑前后返回简短 diff 摘要。

**验收标准：**
- 测试覆盖单次替换、全部替换、不匹配、不唯一、编码处理。

#### Task 4.3 — Shell 工具

**要求：**
- 工具名使用 `shell`，不要承诺某一种 bash 环境。
- Windows 下默认使用 PowerShell，Unix 下默认使用系统 shell。
- 支持 timeout，默认 120 秒，最大 600 秒。
- 超时后终止进程并返回 stdout/stderr 截断结果。
- 危险命令每次确认。
- 建立跨平台命令兼容矩阵，至少覆盖 Windows PowerShell 和 Unix shell 的成功命令、失败命令、环境变量读取、路径输出、UTF-8 中文输出。

**验收标准：**
- 测试覆盖成功命令、非零退出、超时、输出截断。
- Windows PowerShell 路径必须有自动化测试；Unix shell 可在 CI 或手动记录中覆盖。
- 文档明确不同平台 shell 语法不兼容，模型生成命令时应基于当前平台。

### Phase 5：会话持久化

#### Task 5.1 — 会话模型与存储

**要求：**
- 实现 `Session`、`SessionManager`。
- 保存完整 messages，包括 tool calls 和 tool results。
- 存储到 `.minicode/sessions/`。

**验收标准：**
- 单测覆盖 create/save/load/list/delete。
- 自动保存发生在每轮 Agent Loop 完成后。
- 集成测试覆盖：Agent Loop 完成后保存会话，再加载会话继续追加一轮消息。

#### v0.2 发布 Gate

必须全部满足：
- `uv run ruff check .` 通过。
- `uv run mypy src/minicode` 通过。
- `uv run pytest --cov=src/minicode --cov-report=term` 覆盖率不低于 70%。
- 至少 2 个关键路径集成测试通过：只读 Agent Loop、会话保存/恢复、权限拒绝工具执行。
- 手动测试：创建文件、编辑文件、运行简单 shell 命令、恢复会话。

---

## v0.3：斜杠命令与会话操作

> **目标：** 加入常用 slash commands，让已有会话和上下文操作接近日常可用。

### Phase 6：斜杠命令

**优先级：**
1. `/quit`、`/exit`、`/q`
2. `/help`
3. `/clear`
4. `/session list|switch|delete`
5. `/config show`

**验收标准：**
- 命令路由有单元测试。
- 每个命令失败时给出可读错误。
- `/help` 只显示已经实现的命令。
- 集成测试覆盖：通过命令切换会话、清空上下文、查看配置。

#### v0.3 发布 Gate

必须全部满足：
- `uv run ruff check .` 通过。
- `uv run mypy src/minicode` 通过。
- `uv run pytest --cov=src/minicode --cov-report=term` 覆盖率不低于 75%。
- README 更新已实现命令和配置示例。

---

## v0.4：记忆与多 Provider 体验

> **目标：** 在稳定命令系统基础上加入记忆和多 OpenAI-compatible Provider 切换。

### Phase 7：记忆系统

**要求：**
- 使用 Markdown + YAML frontmatter 存储记忆。
- 每条记忆包含 `created_at`、`updated_at`、`source`、`scope`、`confidence` 元数据。
- `get_all_content()` 必须限制总注入长度，默认 8,000 字符。
- 注入 system prompt 时标明“用户记忆，可能不完整或过期”。
- 当记忆名称或 scope 冲突时，按 `updated_at` 优先，并在 debug 日志记录冲突。
- 过期策略：默认不删除旧记忆，但注入时优先选择较新、较高 confidence、scope 匹配当前 workspace 的记忆。

**验收标准：**
- 测试覆盖 add/list/delete/frontmatter 解析/长度限制/冲突处理/过期排序。
- 手动测试：添加偏好后，新会话能读到该偏好。

### Phase 8：多 Provider 切换体验

**要求：**
- v0.4 先只做多个 OpenAI-compatible Provider。
- 支持配置多个 base_url/api_key/model。
- 不在 v0.4 动态写入用户全局配置，避免误改用户文件；先支持编辑 YAML 后读取。

**验收标准：**
- `/provider list`、`/provider switch`、`/model list`、`/model switch` 可用。
- 切换后下一轮对话使用新 provider/model。

#### v0.4 发布 Gate

必须全部满足：
- `uv run ruff check .` 通过。
- `uv run mypy src/minicode` 通过。
- `uv run pytest --cov=src/minicode --cov-report=term` 覆盖率不低于 78%。
- 集成测试覆盖：记忆注入 system prompt、多 Provider 切换后对话使用新 provider。
- README 更新记忆和多 Provider 配置示例。

---

## v1.0：开源发布质量

> **目标：** 从“能用的简历项目”提升为“别人可以安装、贡献、信任”的开源项目。

### Phase 9：错误处理与稳定性

**要求：**
- 网络错误最多重试 3 次，指数退避。
- 401、429、5xx 分别显示具体建议。
- 工具错误返回给模型，同时渲染给用户。
- 全局异常记录 debug 日志并优雅退出。

**验收标准：**
- 断网、无效 API key、限流、工具异常均有测试或手动记录。

### Phase 10：Anthropic Provider

**要求：**
- 将内部 OpenAI-compatible 消息格式转换为 Anthropic messages/tools。
- 支持文本和工具调用。
- Anthropic 流式事件转换为内部 chunk。

**验收标准：**
- 格式转换有单元测试。
- 手动测试 Claude API 完成一次带工具调用的对话。

### Phase 11：CI、质量和发布

**要求：**
- GitHub Actions 运行 ruff、mypy、pytest。
- 覆盖率不低于 80%。
- README、CONTRIBUTING、CHANGELOG、LICENSE 齐全。
- 配置 PyPI 元信息和 GitHub Release。

**验收标准：**
- CI 全绿。
- `uv build` 成功。
- 从干净环境安装后可运行 `minicode --help`。
- CI 至少覆盖 Python 3.12；可选增加 Python 3.13 兼容性矩阵。

---

## 功能暂缓清单

这些功能有价值，但不进入 v0.1/v0.2，避免拖慢主线：

| 功能 | 暂缓原因 | 建议版本 |
|------|----------|----------|
| PDF/图片读取 | 涉及额外依赖、二进制解析、多模态模型差异 | v1.x |
| 并行工具执行 | ReAct 正确性优先，串行更容易调试 | v1.x |
| 动态写入全局 Provider 配置 | 容易误改用户配置，交互确认复杂 | v1.x |
| 复杂状态栏和补全 | 体验加分但不是主线能力 | v0.4+ |
| 精确 token tokenizer | 先用字符截断和上限告警即可 | v1.x |

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| prompt_toolkit 异步输入复杂 | 阻塞 Phase 1 | v0.1 只做单行输入，复杂输入后移 |
| 流式 tool_call delta 难处理 | Agent Loop 不稳定 | v0.1 文本流式，工具调用可先非流式 |
| Anthropic 后期适配导致重构 | Provider 抽象泄露 OpenAI 假设 | v0.1 用 mock Anthropic contract test 验证内部消息模型 |
| 工具越权访问文件 | 安全风险高 | 默认 workspace root 限制 + 敏感文件 deny |
| 写入/shell 工具破坏项目 | 用户信任下降 | v0.2 才引入，并先完成参数级权限 |
| Windows shell 差异 | 跨平台失败 | 工具命名为 shell，建立 PowerShell/Unix 兼容矩阵 |
| 记忆污染上下文 | 回复偏离任务 | v0.4 注入长度限制、scope/confidence/updated_at 排序，并标明记忆可能过期 |

## 简历亮点映射

| 亮点 | 在哪个版本形成 |
|------|----------------|
| OpenAI-compatible Provider 抽象 | v0.1 |
| 流式终端渲染 | v0.1 |
| 插件式只读工具系统 | v0.1 |
| ReAct Agent Loop | v0.1 |
| Workspace 安全边界 | v0.1 |
| 参数级权限控制 | v0.2 |
| 会话持久化 | v0.2 |
| Slash command 系统 | v0.3 |
| Markdown 记忆系统 | v0.4 |
| 多 Provider 适配 | v0.4/v1.0 |
| CI + 类型检查 + 覆盖率 | v1.0 |
