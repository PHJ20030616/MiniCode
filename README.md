# MiniCode

MiniCode 是一个简化版 Claude Code 风格的 Python 命令行工具，用于在终端里进行 AI 辅助编程。它以 OpenAI-compatible API 为模型入口，提供 ReAct Agent Loop、工作区文件工具、Shell 执行、权限确认、会话持久化、项目记忆和斜杠命令等能力。

> 当前版本：`0.1.0`

## 功能概览

- **交互式终端对话**：基于 `prompt_toolkit` 读取输入，使用 Rich 渲染 Markdown、状态、错误和 token 用量。
- **ReAct Agent Loop**：模型回复可触发工具调用，工具结果会回灌给模型继续推理，直到完成或达到最大轮次。
- **OpenAI-compatible Provider**：内置 `openai` 和 `deepseek` 两个 Provider 别名，底层使用 `openai.AsyncOpenAI`。
- **工作区工具**：内置 `read_file`、`glob`、`grep`、`write_file`、`edit_file`、`shell`、`remember`。
- **权限与路径安全**：文件工具限制在工作区内，敏感文件默认拒绝访问；写入、编辑和 Shell 等操作会按风险等级确认。
- **上下文窗口管理**：自动压缩超长工具结果，并按 token 预算裁剪旧消息；可通过 `/context` 查看统计。
- **会话持久化**：每轮对话后自动保存到 `.minicode/sessions/`，支持列表、切换、删除和交互式选择。
- **项目记忆**：长期记忆保存到 `.minicode/memory/`，会注入系统提示词；内置敏感信息检测。
- **多层配置加载**：支持默认值、全局配置、项目配置、显式配置文件、环境变量和 CLI 参数覆盖。
- **工程质量工具**：项目使用 Pydantic、pytest、Ruff 和 mypy，并由 `uv` 管理依赖。

## 环境要求

- Python `3.12+`
- [`uv`](https://docs.astral.sh/uv/) 作为依赖和项目管理工具
- 一个 OpenAI-compatible Provider 的 API key，例如 DeepSeek 或 OpenAI

## 快速开始

安装依赖：

```bash
git clone <repo-url>
cd MiniCode
uv sync
```

配置 API key。MiniCode 默认使用 `deepseek`，可以把长期使用的 key 放在全局配置文件 `~/.minicode/config.yaml`：

```powershell
# PowerShell
New-Item -ItemType Directory -Force ~/.minicode
@"
providers:
  deepseek:
    api_key: "your-api-key"
"@ | Set-Content -Encoding utf8 ~/.minicode/config.yaml
```

```bash
# Bash / zsh
mkdir -p ~/.minicode
cat > ~/.minicode/config.yaml <<'YAML'
providers:
  deepseek:
    api_key: "your-api-key"
YAML
```

也可以使用环境变量：

```powershell
# PowerShell
$env:MINICODE_DEEPSEEK_API_KEY = "your-api-key"
```

```bash
# Bash / zsh
export MINICODE_DEEPSEEK_API_KEY="your-api-key"
```

启动：

```bash
uv run minicode
```

查看帮助和版本：

```bash
uv run minicode --help
uv run minicode --version
```

进入对话后，直接输入问题即可与 AI 对话；输入 `exit`、`quit`、`/quit`、`/exit`、`/q`，或按 `Ctrl+C` / `Ctrl+D` 退出。

## CLI 参数

```bash
uv run minicode [OPTIONS]
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `-m, --model <name>` | 覆盖默认模型。 |
| `-p, --provider <name>` | 覆盖默认 Provider。 |
| `--config <path>` | 额外加载一份配置文件。 |
| `--workspace <path>` | 指定工作区根目录，默认是当前目录。 |
| `--debug` | 启用调试日志，写入 `.minicode/logs/`。 |
| `--version` | 输出版本号并退出。 |

示例：

```bash
uv run minicode -p openai -m gpt-4o-mini
uv run minicode --config ./my-config.yaml
uv run minicode --workspace /path/to/project --debug
```

## 配置

配置加载优先级从低到高：

1. 代码内置默认值
2. 全局用户配置：`~/.minicode/config.yaml`
3. 项目配置：`<workspace>/.minicode/config.yaml`
4. `--config` 指定的配置文件
5. 环境变量
6. CLI 参数，例如 `--provider` 和 `--model`

完整配置示例：

```yaml
default_provider: deepseek
default_model: deepseek-v4-flash
max_tokens: 16384

providers:
  deepseek:
    api_key: "${DEEPSEEK_API_KEY}"
    base_url: "https://api.deepseek.com"
    models:
      - deepseek-v4-flash
      - deepseek-v4-pro
  openai:
    api_key: "${OPENAI_API_KEY}"
    base_url: "https://api.openai.com/v1"
    models:
      - gpt-4o
      - gpt-4o-mini

agent:
  max_rounds: 20
  stream: true
  planning:
    enabled: true
    max_steps: 8
    max_tokens: 2048
  context:
    max_input_tokens: 24000
    recent_messages: 16
    max_tool_output_chars: 12000
    keep_first_user_message: true

permissions:
  trust_mode: false

memory:
  enabled: true
  max_chars: 8000
```

YAML 字符串支持 `${ENV_VAR}` 占位符。占位符会在配置文件合并后解析，未设置的环境变量会导致启动时报出配置错误。

支持的环境变量：

| 环境变量 | 说明 |
| --- | --- |
| `MINICODE_DEFAULT_PROVIDER` | 覆盖 `default_provider`。 |
| `MINICODE_DEFAULT_MODEL` | 覆盖 `default_model`。 |
| `MINICODE_MAX_TOKENS` | 覆盖 `max_tokens`。 |
| `MINICODE_MAX_ROUNDS` | 覆盖 `agent.max_rounds`。 |
| `MINICODE_STREAM` | 覆盖 `agent.stream`。 |
| `MINICODE_PLANNING_ENABLED` | 覆盖 `agent.planning.enabled`。 |
| `MINICODE_PLANNING_MAX_STEPS` | 覆盖 `agent.planning.max_steps`。 |
| `MINICODE_PLANNING_MAX_TOKENS` | 覆盖 `agent.planning.max_tokens`。 |
| `MINICODE_TRUST_MODE` | 覆盖 `permissions.trust_mode`。 |
| `MINICODE_CONTEXT_MAX_INPUT_TOKENS` | 覆盖 `agent.context.max_input_tokens`。 |
| `MINICODE_CONTEXT_RECENT_MESSAGES` | 覆盖 `agent.context.recent_messages`。 |
| `MINICODE_CONTEXT_MAX_TOOL_OUTPUT_CHARS` | 覆盖 `agent.context.max_tool_output_chars`。 |
| `MINICODE_<PROVIDER>_API_KEY` | 设置 `providers.<provider>.api_key`，例如 `MINICODE_DEEPSEEK_API_KEY`。 |
| `MINICODE_<PROVIDER>_BASE_URL` | 设置 `providers.<provider>.base_url`，例如 `MINICODE_OPENAI_BASE_URL`。 |

### 规划模式

规划模式默认开启。MiniCode 会在处理每个普通任务时先向模型请求一份 `执行计划`，将计划展示给用户并写入当前会话历史，然后再进入原有 ReAct 执行流程。规划阶段不会传入工具 schema，也不会执行工具。

可以在配置文件中调整规划参数，或显式关闭：

```yaml
agent:
  planning:
    enabled: true
    max_steps: 8
    max_tokens: 2048
```

也可以通过环境变量覆盖：

```bash
export MINICODE_PLANNING_ENABLED=true
export MINICODE_PLANNING_MAX_STEPS=8
export MINICODE_PLANNING_MAX_TOKENS=2048
```

## 斜杠命令

在交互式对话中，以 `/` 开头的输入会被路由到命令系统。

| 命令 | 说明 |
| --- | --- |
| `/help` | 显示所有可用命令。 |
| `/clear` | 清空当前上下文，并创建新会话。 |
| `/session` 或 `/s` | 打开交互式会话选择器。 |
| `/session list` | 列出已保存会话。 |
| `/session switch <id>` | 切换到指定会话，支持唯一 ID 前缀。 |
| `/session delete <id>` | 删除指定会话，支持唯一 ID 前缀。 |
| `/memory` 或 `/m` | 列出记忆。 |
| `/memory add <name> <content>` | 手动添加一条记忆。 |
| `/memory show <name>` | 查看记忆详情。 |
| `/memory delete <name>` | 删除记忆。 |
| `/context` 或 `/ctx` | 查看最近一次上下文构建统计。 |
| `/config show` | 查看当前配置，API key 会脱敏。 |
| `/quit`、`/exit`、`/q` | 退出 MiniCode。 |

## 内置工具

这些工具由模型在 ReAct Loop 中按需调用，用户通常不需要手动输入工具 JSON。

| 工具 | 说明 |
| --- | --- |
| `read_file` | 读取 UTF-8 文本文件，支持 `offset` 和 `limit` 行范围，默认最多输出 20,000 字符。 |
| `glob` | 使用 glob 模式匹配工作区路径，结果排序后返回，默认最多 250 条。 |
| `grep` | 搜索文件内容，优先使用 `rg`，不可用时降级为 Python 正则搜索；支持文件 glob 过滤。 |
| `write_file` | 写入文本文件，支持 `overwrite` 和 `append`，会自动创建父目录。 |
| `edit_file` | 通过精确字符串替换编辑文件，支持单次替换或 `replace_all=true`。 |
| `shell` | 在工作区目录执行 Shell 命令，默认超时 120 秒，最大 600 秒。 |
| `remember` | 在用户明确要求时保存长期记忆。 |

所有路径类工具都会解析到工作区根目录内。访问工作区外路径、读取或写入敏感文件、明显高危 Shell 命令会被拒绝。

## 权限模型

权限检查按工具名和参数给出四种结果：

| 等级 | 行为 |
| --- | --- |
| `safe` | 直接执行。 |
| `caution` | 需要确认，或命中 always-allow 规则后执行。 |
| `dangerous` | 需要确认，常见于编辑文件、覆盖文件和 Shell 命令。 |
| `deny` | 永远拒绝，`trust_mode` 也不能绕过。 |

`permissions.trust_mode: true` 会跳过 `caution` / `dangerous` 的交互确认，但仍保留 `deny` 拦截。用户选择“始终允许”后，规则会保存到 `.minicode/permissions.json`，按工具名和工作区相对路径模式匹配。

## 运行时数据

MiniCode 会在工作区下使用 `.minicode/` 保存运行时数据。该目录已被 git 忽略。

```text
.minicode/
+-- config.yaml             # 可选的项目配置
+-- logs/                   # --debug 日志
+-- sessions/
|   +-- index.json           # 会话摘要索引
|   +-- <session_id>.json    # 会话完整消息
+-- memory/
|   +-- MEMORY.md            # 记忆索引
|   +-- <name>.md            # 单条记忆
+-- permissions.json         # always-allow 权限规则
```

## 项目结构

```text
MiniCode/
+-- src/minicode/
|   +-- main.py              # Typer CLI 入口
|   +-- cli/                 # 交互式终端应用、渲染、主题、确认提示
|   +-- agent/               # ReAct Loop、系统提示词、上下文窗口管理
|   +-- providers/           # Provider 契约和 OpenAI-compatible 适配器
|   +-- tools/               # 内置工具、注册器、路径安全检查
|   +-- permissions/         # 参数级权限检查和 always-allow 存储
|   +-- commands/            # 斜杠命令系统
|   +-- session/             # 会话模型、序列化和持久化
|   +-- memory/              # Markdown 记忆模型和管理器
|   +-- config/              # Pydantic 配置模型和多层加载器
|   +-- utils/               # 异常、日志、重试等通用能力
+-- tests/                   # pytest 测试套件
+-- doc/                     # 设计文档和任务计划
+-- pyproject.toml           # 项目元数据和工具配置
+-- uv.lock                  # 可复现依赖锁文件
```

更多设计背景见 [doc/minicode-design.md](doc/minicode-design.md) 和 [doc/minicode-task-plan_2.0.md](doc/minicode-task-plan_2.0.md)。

## 开发

安装依赖：

```bash
uv sync
```

运行测试：

```bash
uv run pytest
```

运行 lint 和类型检查：

```bash
uv run ruff check .
uv run mypy src/minicode
```

执行包导入烟雾测试：

```bash
uv run python -c "import minicode"
```

构建发布产物：

```bash
uv build
```

提交前建议至少运行：

```bash
uv run pytest
uv run ruff check .
uv run mypy src/minicode
```

请不要提交 `.minicode/`、`.venv/`、缓存目录、API key 或其他本地运行时数据。
