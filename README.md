# MiniCode

MiniCode 是一个简化版 Claude Code 风格的 Python 命令行工具，用于 AI 辅助编程。项目重点放在清晰的工程结构、可测试的实现方式，以及从终端对话、模型 Provider、分层配置、结构化日志到后续 Agent 工具扩展的完整骨架。

> 当前版本：`0.1.0`

## 目录

- [功能特性](#功能特性)
- [环境要求](#环境要求)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [使用方法](#使用方法)
- [项目结构](#项目结构)
- [开发指南](#开发指南)
- [路线图](#路线图)
- [贡献指南](#贡献指南)

## 功能特性

- **交互式终端对话**：基于 `prompt_toolkit` 接收输入，使用 Rich 渲染输出。
- **流式模型回复**：通过统一的 Provider 抽象处理模型响应。
- **OpenAI-compatible Provider 支持**：支持 OpenAI 风格 API，当前内置 `openai` 和 `deepseek` 两个别名。
- **多层配置加载**：支持默认配置、全局配置、项目配置、显式配置文件、环境变量和 CLI 参数覆盖。
- **YAML 环境变量占位符**：支持在配置中使用 `${DEEPSEEK_API_KEY}` 这类占位符。
- **结构化调试日志**：启用 `--debug` 后，日志会写入 `.minicode/logs/`。
- **类型化 Python 工程**：使用 Pydantic、mypy、Ruff 和 pytest 保障代码质量。

MiniCode 仍处于早期阶段。当前实现重点包括 CLI 入口、配置系统、Provider 契约、OpenAI-compatible 流式对话和终端渲染。Agent 工具、文件编辑、Shell 执行、会话、记忆和 slash commands 等能力仍在规划或后续版本中逐步实现。

## 环境要求

- Python `3.12+`
- [`uv`](https://docs.astral.sh/uv/) 作为依赖和项目管理工具
- 一个 OpenAI-compatible Provider 的 API key，例如 DeepSeek 或 OpenAI

## 快速开始

克隆仓库并安装依赖：

```bash
git clone <repo-url>
cd MiniCode
uv sync
```

设置 API key。MiniCode 默认使用 DeepSeek，推荐把长期使用的 key 放在全局配置文件 `~/.minicode/config.yaml` 中：

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

如果只想给当前项目单独设置，也可以把同样的内容放在项目根目录的 `./.minicode/config.yaml`。临时使用时，也可以通过环境变量设置：

```bash
# PowerShell
$env:MINICODE_DEEPSEEK_API_KEY = "your-api-key"

# Bash / zsh
export MINICODE_DEEPSEEK_API_KEY="your-api-key"
```

启动 CLI：

```bash
uv run minicode
```

查看命令帮助和版本：

```bash
uv run minicode --help
uv run minicode --version
```

进入对话后，可以输入 `exit`、`quit`、`/exit`、`/quit`，或按 `Ctrl+C` 退出。

## 配置说明

MiniCode 按以下优先级加载配置，越靠后优先级越高：

1. 代码内置默认值
2. 全局用户配置：`~/.minicode/config.yaml`
3. 项目配置：`./.minicode/config.yaml`
4. 通过 `--config` 指定的配置文件
5. 环境变量
6. CLI 参数，例如 `--provider` 和 `--model`

通常优先使用全局配置 `~/.minicode/config.yaml`，所有项目都会读取它；需要项目专属配置时，使用项目根目录下的 `./.minicode/config.yaml`；需要临时测试另一份配置时，使用 `--config ./my-config.yaml`。

全局或项目配置示例：

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

permissions:
  trust_mode: false
```

支持的环境变量：

| 环境变量 | 说明 |
| --- | --- |
| `MINICODE_DEFAULT_PROVIDER` | 覆盖 `default_provider`。 |
| `MINICODE_DEFAULT_MODEL` | 覆盖 `default_model`。 |
| `MINICODE_MAX_TOKENS` | 覆盖 `max_tokens`。 |
| `MINICODE_MAX_ROUNDS` | 覆盖 `agent.max_rounds`。 |
| `MINICODE_STREAM` | 覆盖 `agent.stream`。 |
| `MINICODE_TRUST_MODE` | 覆盖 `permissions.trust_mode`。 |
| `MINICODE_<PROVIDER>_API_KEY` | 设置 `providers.<provider>.api_key`，例如 `MINICODE_DEEPSEEK_API_KEY`。 |
| `MINICODE_<PROVIDER>_BASE_URL` | 设置 `providers.<provider>.base_url`，例如 `MINICODE_OPENAI_BASE_URL`。 |

也可以在启动时临时覆盖 Provider 和模型：

```bash
uv run minicode --provider openai --model gpt-4o-mini
uv run minicode --config ./my-config.yaml
uv run minicode --workspace /path/to/project
```

## 使用方法

在当前工作区启动对话：

```bash
uv run minicode
```

开启调试日志：

```bash
uv run minicode --debug
```

指定 Provider 和模型：

```bash
uv run minicode -p deepseek -m deepseek-v4-flash
```

MiniCode 会把日志等运行时文件写入 `.minicode/`。该目录已被 git 忽略。

## 项目结构

```text
MiniCode/
+-- src/minicode/
|   +-- main.py                 # Typer CLI 入口
|   +-- cli/                    # 终端应用、渲染器和主题
|   +-- config/                 # YAML、环境变量和 CLI 参数配置
|   +-- providers/              # Provider 契约和 OpenAI-compatible 适配器
|   +-- agent/                  # 规划中的 ReAct loop 与上下文处理
|   +-- tools/                  # 规划中的工具定义与注册表
|   +-- commands/               # 规划中的 slash command 系统
|   +-- session/                # 规划中的会话持久化
|   +-- memory/                 # 规划中的记忆系统
|   +-- permissions/            # 规划中的权限检查
|   +-- utils/                  # 异常和日志工具
+-- tests/                      # pytest 测试套件
+-- doc/                        # 设计文档和任务计划
+-- pyproject.toml              # 项目元数据和工具配置
+-- uv.lock                     # 可复现依赖锁文件
```

## 开发指南

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

项目主要使用：

- `Typer`：CLI 参数解析
- `Rich`：终端渲染
- `prompt_toolkit`：异步终端输入
- `openai.AsyncOpenAI`：OpenAI-compatible API 调用
- `Pydantic`：配置和 Provider 消息模型
- `pytest`、`Ruff`、`mypy`：测试、lint 和类型检查

## 路线图

- 只读工作区工具，例如文件读取、glob 和 grep。
- 支持工具调用和最大轮次限制的 ReAct Agent Loop。
- 带权限检查的写文件、编辑文件和 Shell 工具。
- 基于 `.minicode/sessions/` 的会话持久化。
- `/help`、`/clear`、`/session`、`/config`、`/provider` 等 slash commands。
- 基于 Markdown 的记忆系统。
- 更多 Provider 适配器和发布质量完善。

更多设计背景见 [doc/minicode-task-plan.md](doc/minicode-task-plan.md) 和 [doc/minicode-design.md](doc/minicode-design.md)。

## 贡献指南

欢迎贡献。涉及行为变化的改动请补充聚焦的测试，并在提交前运行：

```bash
uv run pytest
uv run ruff check .
uv run mypy src/minicode
```

请保持改动范围清晰、类型标注完整，并遵循现有模块边界。不要提交 `.minicode/`、`.venv/`、缓存目录、API key 或其他本地运行时数据。
