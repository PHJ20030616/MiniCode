# MiniCode — 任务计划书

> 从零构建一个简化版 Claude Code 的 Python CLI 工具，用于 AI 辅助编程。
> 开源项目，面向求职简历，注重工程质量和架构设计。
>
> **详细设计文档**：参见 `doc/minicode-design.md`

---

## 任务执行原则

1. **先跑通主线，再优化细节** — 每个 Phase 结束时必须有一个可运行的产品
2. **每个任务独立可测** — 有明确的验收标准
3. **工具串行先于并行** — MVP 不追求极致性能
4. **OpenAI 兼容优先** — 覆盖最大范围的用户（OpenAI + DeepSeek + 中转站）
5. **测试跟随开发** — 每完成一个模块就写测试，不要最后补

---

## Phase 0：项目初始化与基础设施

> **目标：** 一个可以运行 `minicode` 命令的骨架项目，目录结构就位，依赖安装完毕。

### Task 0.1 — 初始化项目结构

**要求：**
- 使用 `uv init` 初始化项目
- 配置 `pyproject.toml`（项目元信息、Python 3.12+、依赖声明）
- 创建完整的 `src/minicode/` 目录结构（所有包的 `__init__.py`）
- 创建 `tests/` 目录结构
- 创建 `.gitignore`（Python 模板 + `.minicode/`）

**推荐实现：**
```bash
uv init MiniCode
cd MiniCode
uv add rich prompt-toolkit httpx pydantic structlog pyyaml typer
uv add --dev pytest pytest-asyncio pytest-mock mypy ruff
```

**验收标准：** `uv run python -c "import minicode"` 不报错

---

### Task 0.2 — CLI 入口与参数解析

**要求：**
- 使用 Typer 实现 CLI 入口 `minicode/main.py`
- 支持以下命令行参数：
  - `--model` / `-m`：指定模型
  - `--provider` / `-p`：指定 Provider
  - `--session` / `-s`：恢复指定会话
  - `--trust`：跳过权限确认
  - `--verbose` / `-v`：详细日志
  - `--debug`：调试日志
  - `--version`：显示版本
  - `--list-sessions`：列出历史会话
- 入口函数解析参数后，调用 `cli/app.py` 的 `run()` 函数

**关键文件：** `src/minicode/main.py`

**验收标准：** `uv run minicode --version` 输出版本信息

---

### Task 0.3 — 配置系统

**要求：**
- 实现 `config/loader.py`：多层配置加载
  - 从 `~/.minicode/config.yaml` 读取全局配置
  - 从 `./.minicode/config.yaml` 读取项目配置
  - 合并环境变量（`MINICODE_*`）
  - 命令行参数覆盖
- 实现 `config/models.py`：Pydantic 配置模型
  - `MinicodeConfig`：顶层配置
  - `ProviderConfig`：单个 Provider 配置（api_key, base_url, models）
  - `ModelConfig`：模型默认设置
  - `AgentConfig`：Agent 行为配置
- 支持环境变量占位符：`api_key: "${ENV_VAR_NAME}"`

**关键文件：** `src/minicode/config/loader.py`, `src/minicode/config/models.py`

**验收标准：** 单元测试覆盖所有加载优先级场景

---

### Task 0.4 — 日志系统

**要求：**
- 实现 `utils/logger.py`：structlog 配置
  - 控制台输出：Rich 渲染，彩色
  - 文件输出：JSON 格式，按天轮转
  - 支持 `--verbose` 和 `--debug` 标志
- 实现 `utils/exceptions.py`：基础异常类
  - `MiniCodeError`（基类）
  - `ConfigError`
  - `ProviderError`
  - `ToolError`

**关键文件：** `src/minicode/utils/logger.py`, `src/minicode/utils/exceptions.py`

**验收标准：** 运行程序，日志正确写入文件和控制台

---

## Phase 1：首次对话（无工具的单轮对话）

> **目标：** 用户输入问题 → 调用 LLM → 流式显示回复。这是整个系统的"心跳"。

### Task 1.1 — Provider 抽象与注册

**要求：**
- 实现 `providers/base.py`：
  - `BaseProvider` 抽象类（`chat()`, `list_models()`）
  - `StreamChunk` 数据模型（`text_delta | tool_call_delta | done | error`）
- 实现 `providers/registry.py`：
  - `ProviderRegistry`：字典注册 Provider 实例
  - 支持 `get(name)` 和 `register(name, provider)`
- 统一内部消息格式：`Message`, `ToolCall`, `FunctionCall`（Pydantic 模型）

**关键文件：** `src/minicode/providers/base.py`, `src/minicode/providers/registry.py`

**验收标准：** Provider Registry 可以注册和获取 Provider

---

### Task 1.2 — OpenAI 兼容 Provider

**要求：**
- 实现 `providers/openai_compatible.py`：
  - 继承 `BaseProvider`
  - 支持流式 SSE 解析
  - 正确处理 `text_delta` 和 `tool_call_delta` 两种 chunk
  - 支持用户自定义 `base_url` 和 `api_key`（中转站场景）
- 支持以下 API 端：OpenAI、DeepSeek、任意 OpenAI 兼容中转站

> **建议：** MVP 阶段直接用 `openai` 官方库（`openai.AsyncOpenAI`），它已经处理好了 SSE 流解析，可以少踩坑。后续如果需要更灵活的控制，再切换为 httpx 手动解析。

**关键文件：** `src/minicode/providers/openai_compatible.py`

**验收标准：**
1. 单元测试（mock HTTP）验证流式解析正确
2. 手动测试：用 DeepSeek API 发一条消息，看到流式回复

---

### Task 1.3 — CLI 终端渲染器

**要求：**
- 实现 `cli/renderer.py`：
  - 使用 Rich 渲染 Markdown 文本（`Markdown` 组件）
  - 代码块语法高亮（`Syntax` 组件）
  - 流式追加文本（`Live` 组件实时刷新）
- 实现 `cli/theme.py`：
  - 定义颜色/样式常量
  - 统一的视觉风格

**关键文件：** `src/minicode/cli/renderer.py`, `src/minicode/cli/theme.py`

**验收标准：** 渲染一段包含代码块的 Markdown 文本，显示正确

---

### Task 1.4 — 单轮对话主循环（MVP 核心）

**要求：**
- 实现 `cli/app.py`：
  - 使用 `prompt_toolkit` 异步模式接收用户输入
  - 调用 Provider 的流式 chat 接口
  - 实时渲染回复
  - 无工具调用（Phase 1 只做纯文本）
- 实现 `agent/system_prompt.py`：
  - 构建基本的 system prompt（告诉模型它的角色和能力）
- 整个链路跑通：用户输入 → API 调用 → 流式渲染 → 等待下一次输入

**关键文件：** `src/minicode/cli/app.py`, `src/minicode/agent/system_prompt.py`

**验收标准：** 启动 `minicode`，输入 "Hello"，看到流式回复

---

## Phase 2：核心闭环 — 工具调用 + Agent Loop

> **目标：** 模型可以调用工具（读文件、写文件、搜索、执行命令），形成完整的 ReAct 循环。

### Task 2.1 — 工具系统基础设施

**要求：**
- 实现 `tools/base.py`：
  - `BaseTool` 抽象类（name, description, parameters, risk_level, execute）
  - `ToolResult` 数据模型
- 实现 `tools/registry.py`：
  - `ToolRegistry`：装饰器注册 + 字典存储
  - `get_all_tools()`, `get_tool(name)`, `get_tools_schema()`（生成 OpenAI 兼容的 tools 数组）
- 工具 `parameters` 字段使用 JSON Schema 格式，可直接传给 API

**关键文件：** `src/minicode/tools/base.py`, `src/minicode/tools/registry.py`

**验收标准：** 注册多个工具，`get_tools_schema()` 输出正确的 JSON Schema 数组

---

### Task 2.2 — 文件读取工具

**要求：**
- 实现 `tools/file_read.py`：
  - 支持指定行范围（`offset`, `limit`）
  - 支持 PDF 文件（`pages` 参数，可选）
  - 支持图片文件（可选）
  - 目录读取返回错误提示
  - 文件不存在返回友好错误

> **建议：** 第一版先支持文本文件，PDF/图片作为第二版

**关键文件：** `src/minicode/tools/file_read.py`

**验收标准：** 单元测试：读文本文件、读不存在的文件、读目录、读行范围

---

### Task 2.3 — 文件写入工具

**要求：**
- 实现 `tools/file_write.py`：
  - 创建新文件或覆盖已有文件
  - 父目录不存在 → 自动创建

**关键文件：** `src/minicode/tools/file_write.py`

**验收标准：** 单元测试覆盖各种场景

---

### Task 2.4 — 文件编辑工具

**要求：**
- 实现 `tools/file_edit.py`：
  - **精确字符串替换**（类似 Claude Code 的 Edit 工具）
  - 参数：`file_path`, `old_string`, `new_string`, `replace_all`
  - `old_string` 必须在文件中唯一（`replace_all=false` 时），否则报错
  - 实施前验证：确认文件存在、old_string 匹配、唯一性检查

**关键文件：** `src/minicode/tools/file_edit.py`

**验收标准：** 单元测试：单次替换、全部替换、不唯一报错、不匹配报错

---

### Task 2.5 — Bash 执行工具

**要求：**
- 实现 `tools/bash.py`：
  - 使用 `asyncio.create_subprocess_shell` 执行命令
  - 支持 `timeout` 参数（默认 120 秒，最大 600 秒）
  - 返回：stdout, stderr, exit_code
  - 截断超长输出（避免 token 消耗过大）
  - 注意：Windows 兼容（git bash / WSL / cmd 的差异）

**关键文件：** `src/minicode/tools/bash.py`

**验收标准：** 单元测试：执行简单命令、超时、错误命令

---

### Task 2.6 — 代码搜索工具

**要求：**
- 实现 `tools/grep.py`：
  - 底层调用 ripgrep（`rg`），如果不可用则 fallback 到 Python 实现
  - 参数：`pattern`（正则）、`path`、`glob`（文件类型过滤）、`-A/-B/-C`（上下文行数）
  - `output_mode`：`content` / `files_with_matches` / `count`
  - `head_limit`：限制输出行数（默认 250）
  - `multiline`：多行模式（`rg -U --multiline-dotall`）
- 实现 `tools/glob.py`：
  - 文件模式匹配（`**/*.py`, `src/**/*.ts`）
  - 返回匹配路径列表，按修改时间排序

> **建议：** ripgrep 检测用 `shutil.which("rg")`，如果存在则 subprocess 调用；否则用 `pathlib.Path.rglob` + `re` 作为 fallback

**关键文件：** `src/minicode/tools/grep.py`, `src/minicode/tools/glob.py`

**验收标准：** 单元测试：grep 搜索、glob 匹配、多行模式、fallback

---

### Task 2.7 — 权限控制系统

**要求：**
- 实现 `permissions/checker.py`：
  - `check(tool: BaseTool, args: dict) -> PermissionDecision`
  - 三级判定：🟢safe → 允许 / 🟡caution → 会话内首次询问 / 🔴dangerous → 每次询问
  - 支持 `--trust` 全局跳过
- 实现 `permissions/store.py`：
  - 记录用户的 "Always allow" 决定
  - 存储位置：`.minicode/permissions.json`
- prompt_toolkit 确认框集成：
  - 工具执行前弹出确认：[Y] 允许 / [n] 拒绝 / [a] 总是允许

**关键文件：** `src/minicode/permissions/checker.py`, `src/minicode/permissions/store.py`

**验收标准：** 单元测试覆盖各种权限场景

---

### Task 2.8 — ReAct Agent Loop

**要求：**
- 实现 `agent/loop.py`：
  - 完整的 ReAct 循环：构建 messages → 调用 Provider → 流式渲染/收集 tool_calls → 权限检查 → 执行工具 → 追加结果 → 循环
  - 最大轮次限制（20 轮）
  - 用户中断支持（Ctrl+C / Esc）
  - 工具执行中显示状态："⏳ Executing read_file..."
- 实现 `agent/context.py`：
  - 简单的 token 估算（规则：约 4 字符 ≈ 1 token）
  - 上下文窗口接近上限时发出警告

**关键文件：** `src/minicode/agent/loop.py`, `src/minicode/agent/context.py`

**验收标准：**
1. 手动测试：要求模型 "读取 README.md 并总结内容"
2. 手动测试：要求模型 "创建一个 hello.py 文件，内容是 print('hello')"
3. 验证权限弹框出现，选择后工具执行

---

## Phase 3：用户体验完善

> **目标：** 会话持久化、记忆系统、斜杠命令——让 MiniCode 成为一个"可以日常使用"的工具。

### Task 3.1 — 会话管理

**要求：**
- 实现 `session/models.py`：
  - `Session` Pydantic 模型（id, name, messages, created_at, updated_at, model, provider）
  - 每条 message 完整序列化（含 tool_calls, tool_call_id 等）
- 实现 `session/manager.py`：
  - `create_session()` → 新建会话
  - `save_session(session)` → 保存到 `.minicode/sessions/<id>.json`
  - `load_session(session_id)` → 从 JSON 恢复
  - `list_sessions()` → 读取 `index.json`
  - `delete_session(session_id)` → 删除文件
  - `get_current_session()` → 返回当前活跃会话
- **自动保存**：每次 Agent Loop 完成后自动保存

**关键文件：** `src/minicode/session/models.py`, `src/minicode/session/manager.py`

**验收标准：** 单元测试覆盖 CRUD 全流程

---

### Task 3.2 — 斜杠命令系统

**要求：**
- 实现 `commands/base.py`：
  - `BaseCommand` 抽象类
  - `CommandResult` 数据模型
  - `CommandContext`（传入当前 session、config、provider registry）
- 实现 `commands/registry.py`：
  - `CommandRegistry`：装饰器注册
  - `route(input: str) -> Command | None`：解析 `/xxx args` 格式
- 实现以下命令（按优先级）：
  1. `/quit` / `/exit` / `/q` — 退出
  2. `/help` / `/h` — 帮助
  3. `/new` — 新建会话
  4. `/session` / `/s` — 会话管理（列表/切换/删除）
  5. `/model` / `/m` — 切换模型
  6. `/provider` / `/p` — 切换 Provider
  7. `/clear` / `/c` — 清除上下文
  8. `/config` / `/cfg` — 查看/修改配置
  9. `/memory` / `/mem` — 记忆管理
- 集成到 `cli/app.py`：用户输入以 `/` 开头 → 路由到命令；否则 → Agent Loop

**关键文件：** `src/minicode/commands/`

**验收标准：**
1. 输入 `/help` 看到命令列表
2. 输入 `/new` 创建新会话
3. 输入 `/model gpt-4o` 切换模型，下次对话使用新模型

---

### Task 3.3 — 记忆系统

**要求：**
- 实现 `memory/models.py`：
  - `Memory` Pydantic 模型（name, description, content, type, created_at）
  - frontmatter 解析（YAML 格式的 `---` 包裹元数据）
- 实现 `memory/manager.py`：
  - `list_memories()` → 读取 `MEMORY.md` 索引
  - `add_memory(memory)` → 创建新 `.md` 文件 + 更新索引
  - `delete_memory(name)` → 删除文件 + 更新索引
  - `get_all_content()` → 合并所有记忆内容（注入 system prompt）
- 集成到 `agent/system_prompt.py`：
  - 每次构建 system prompt 时，追加记忆内容
- `/memory` 命令集成（add/list/delete）

**关键文件：** `src/minicode/memory/models.py`, `src/minicode/memory/manager.py`

**验收标准：**
1. 单元测试覆盖 CRUD
2. 手动测试：`/memory add "我喜欢用 pytest"` → 新会话中模型提到测试时自动推荐 pytest

---

### Task 3.4 — CLI 终端体验打磨

**要求：**
- 多行输入支持（`prompt_toolkit` 配置）
- 输入历史（上下箭头浏览）
- 语法感知的补全（`/` 开头补全命令名，普通补全文件路径）
- 状态栏显示：当前模型、会话名、token 用量
- 颜色/图标体系完善（`cli/theme.py`）

**关键文件：** `src/minicode/cli/input.py`, `src/minicode/cli/theme.py`

**验收标准：** 手动体验：输入流畅，视觉美观，状态信息清晰

---

## Phase 4：多 Provider 支持

> **目标：** 支持 Anthropic API + 用户自定义中转站，覆盖主流 LLM 服务。

### Task 4.1 — Anthropic Provider 适配器

**要求：**
- 实现 `providers/anthropic.py`：
  - 将内部统一格式（OpenAI 兼容 messages + tools）**翻译为** Anthropic API 格式
  - System prompt → 独立的 `system` 参数
  - Messages → Anthropic 的 `messages` 数组
  - Tools → Anthropic 的 `tools` 数组
  - 流式解析 Anthropic 的 SSE 事件（`content_block_start/delta/stop`）
  - 将 Anthropic 响应 **翻译回** 内部统一格式
- 使用 `anthropic` 官方 Python SDK（`anthropic.AsyncAnthropic`）

**关键文件：** `src/minicode/providers/anthropic.py`

**验收标准：**
1. 单元测试验证格式翻译正确（OpenAI ↔ Anthropic 互转）
2. 手动测试：用 Claude API 完成一次带工具调用的对话

---

### Task 4.2 — 自定义中转站 Provider

**要求：**
- 增强 `providers/openai_compatible.py`：
  - 支持用户运行时通过 `/provider add custom <name> <base_url> <api_key>` 动态添加
  - 动态 Provider 自动探测可用模型（调用 `/v1/models` 端点）
- 配置持久化：新添加的 Provider 自动保存到 `~/.minicode/config.yaml`

**关键文件：** `src/minicode/providers/openai_compatible.py`, `src/minicode/commands/provider_cmd.py`

**验收标准：** 手动测试：添加一个中转站 → 切换 → 成功对话

---

### Task 4.3 — 多 Provider 切换体验

**要求：**
- `/provider list` — 列出所有已配置的 Provider，高亮当前
- `/provider switch <name>` — 切换
- `/model list` — 列出当前 Provider 的可用模型
- `/model switch <name>` — 切换模型

**验收标准：** 手动体验：流畅切换 Provider 和 Model

---

## Phase 5：质量打磨与开源准备

> **目标：** 测试覆盖率、CI/CD、文档、错误处理——达到开源项目标准。

### Task 5.1 — 完整测试套件

**要求：**
- 核心模块单元测试覆盖率 ≥ 80%
- 重点测试：
  - Agent Loop（mock Provider，验证 ReAct 循环逻辑）
  - 工具执行（真实文件系统操作，用 tmp_path）
  - Provider 格式翻译（OpenAI ↔ Anthropic 互转）
  - 配置加载优先级
  - 权限判断逻辑
  - 会话序列化/反序列化
- pytest fixtures：`mock_provider`, `tmp_session_dir`, `sample_config`

**关键文件：** `tests/`, `tests/conftest.py`

**验收标准：** `pytest --cov=src/minicode --cov-report=term` 覆盖率 ≥ 80%

---

### Task 5.2 — CI/CD

**要求：**
- GitHub Actions workflow（`.github/workflows/ci.yml`）：
  - Python 3.12 环境
  - uv 安装依赖
  - ruff 代码风格检查
  - mypy 类型检查（严格模式）
  - pytest 测试套件
  - 覆盖率报告

**验收标准：** push 到 GitHub 后 CI 自动运行，全部通过

---

### Task 5.3 — 代码质量

**要求：**
- 配置 `ruff`：pyproject.toml 中定义规则（推荐 `select = ["E", "F", "I", "N", "W", "UP"]`）
- 配置 `mypy`：`strict = true`
- 所有 `async def` 有返回值类型注解
- 所有公开 API 有 docstring
- `__init__.py` 导出规范（控制公共 API 面）

**验收标准：** `ruff check` 和 `mypy src/minicode` 零错误

---

### Task 5.4 — 错误处理

**要求：**
- 网络错误（API 超时、连接失败）→ 自动重试（最多 3 次，指数退避），给用户友好提示
- API 错误（401、429、500）→ 显示具体错误信息，不崩溃
- 工具执行错误 → 返回 error 信息给 LLM（让模型知道操作失败了）
- 全局异常捕获 → 记录日志，优雅退出

**验收标准：** 手动测试：断网时不会崩溃，显示 "Network error, retrying..."

---

### Task 5.5 — 文档编写

**要求：**
- `README.md`：项目介绍、安装指南、快速开始、功能列表、配置说明、开发指南、许可证
- `CONTRIBUTING.md`：贡献指南
- `CHANGELOG.md`：版本变更记录

---

### Task 5.6 — 发布准备

**要求：**
- 配置 PyPI 发布（`pyproject.toml` 中的 `[project]` 完整元信息）
- 打 Git tag（`v0.1.0`）
- GitHub Release 页面

---

## 附录 A：技术亮点清单（简历素材）

| 亮点 | 说明 |
|------|------|
| 全异步架构 | asyncio + httpx，全链路非阻塞 |
| 插件式工具系统 | 装饰器注册，零耦合，可扩展 |
| 多 Provider 适配 | 内部统一格式 + 外部适配器，支持 OpenAI/Anthropic/自定义中转站 |
| ReAct Agent Loop | 模型自主调用工具，多轮推理循环 |
| 流式 SSE 解析 | 实时逐 token 渲染 |
| 结构化日志 | structlog JSON 格式，可观测性 |
| 多层配置合并 | CLI > ENV > 项目 > 全局 > 默认 |
| 类型安全 | Pydantic v2 + mypy strict |
| 文件级记忆系统 | Markdown + frontmatter，Git 友好 |
| 三级权限控制 | safe / caution / dangerous 分级授权 |

## 附录 B：风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| prompt_toolkit 异步模式踩坑 | Phase 1 延迟 | 先实现最简版本，再逐步整合 |
| ripgrep 不可用 | 搜索功能降级 | fallback 到 Python re + rglob |
| Anthropic API 格式翻译复杂 | Phase 4 延期 | 确保内部格式足够通用；Phase 1-3 只关注 OpenAI 兼容 |
| token 消耗过大 | 成本失控 | 实现 token 计数和上限告警；限制上下文窗口 |
| 流式解析错误 | 用户看到乱码 | 充分的单元测试 + 错误恢复 |
