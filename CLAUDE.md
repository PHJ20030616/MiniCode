# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MiniCode is a simplified Claude Code clone — a Python CLI tool for AI-assisted programming. It's an open-source project built from scratch, aimed at being a portfolio/resume piece with emphasis on engineering quality and architecture design.

**Current version: 0.1.0** — v0.1 (ReAct Agent + read-only tools) is complete. v0.2 (permissions + write tools + sessions) is in progress.

## Tech Stack

| Category | Technology |
|----------|-----------|
| Language | Python 3.12+ |
| Package manager | uv |
| CLI framework | Typer (arg parsing) + Rich (rendering) + prompt_toolkit (input) |
| Data models | Pydantic v2 |
| HTTP | httpx (async) |
| LLM SDK | `openai` SDK for OpenAI-compatible providers (DeepSeek as default; Anthropic planned for Phase 10) |
| Logging | structlog (structured, JSON file output + Rich console output) |
| Testing | pytest + pytest-asyncio + pytest-mock + pytest-cov |
| Linting | ruff (E, F, I, UP, B, SIM rules, line-length=100) |
| Type checking | mypy (warn_return_any, disallow_untyped_defs) |

## Commands

All commands use `uv run` to ensure the project's virtual environment is used:

```bash
# Install dependencies
uv sync

# Run the CLI
uv run minicode [--model <model>] [--provider <provider>] [--config <path>] [--workspace <path>] [--debug]

# Run tests
uv run pytest                                    # all tests
uv run pytest tests/test_tools/test_file_read.py # single test file
uv run pytest -k "test_name_pattern"             # filter by name
uv run pytest --cov=src/minicode --cov-report=term  # with coverage

# Lint & type check
uv run ruff check .
uv run mypy src/minicode

# Smoke test
uv run python -c "import minicode"
```

## Architecture

Three-layer architecture:

```
CLI Layer (Rich + prompt_toolkit)
  → Renderer (markdown, syntax highlight)
  → Input (prompt_toolkit single-line input in app.py)
  → Command Router (/commands vs Agent Loop)

Agent Layer
  → ReAct Agent Loop (LLM ⇄ tool calls ⇄ results ⇄ repeat, max 20 rounds)
  → Provider Adapter (internal OpenAI-compatible format → provider-specific translation)
  → Tool Registry (plugin-style decorator registration, risk-level gating)

Storage Layer
  → Sessions (JSON files under .minicode/sessions/) — stub, not yet implemented
  → Memory (Markdown + YAML frontmatter, one file per memory) — stub, not yet implemented
  → Config (multi-layer YAML: CLI > ENV > ./.minicode/ > ~/.minicode/ > defaults)
```

## Directory Structure

```
MiniCode/
├── pyproject.toml              # Project metadata, deps, ruff/mypy/pytest config
├── uv.lock                     # Reproducible dependency lockfile
├── src/minicode/
│   ├── main.py                 # Typer CLI entry point
│   ├── __init__.py             # __version__ = "0.1.0"
│   ├── cli/                    # Terminal UI
│   │   ├── app.py              # ChatApp: prompt_toolkit input loop + command routing
│   │   ├── renderer.py         # StreamingRenderer: Rich Live display, markdown rendering
│   │   └── theme.py            # Color theme constants
│   ├── agent/                  # ReAct Agent Loop
│   │   ├── loop.py             # AgentLoop: stream → collect tool_calls → execute → repeat
│   │   ├── context.py          # build_messages(): system prompt + history assembly
│   │   └── system_prompt.py    # System prompt builder (injects tool list)
│   ├── providers/              # LLM Provider abstraction
│   │   ├── base.py             # Message, ToolCall, StreamChunk, BaseProvider models
│   │   ├── openai_compatible.py # OpenAICompatibleProvider using openai.AsyncOpenAI
│   │   └── registry.py         # ProviderRegistry + MockProvider for testing
│   ├── tools/                  # Tool system (plugin-style registration)
│   │   ├── base.py             # BaseTool, ToolResult
│   │   ├── registry.py         # ToolRegistry: decorator registration, schema export, execution routing
│   │   ├── path_safety.py      # Workspace boundary + sensitive file checks
│   │   ├── file_read.py        # read_file tool
│   │   ├── glob.py             # glob tool
│   │   └── grep.py             # grep tool (rg with Python fallback)
│   ├── config/                 # Multi-layer YAML config
│   │   ├── models.py           # AppConfig, ProviderConfig, AgentConfig Pydantic models
│   │   └── loader.py           # Config loading with priority chain + ${ENV_VAR} resolution
│   ├── permissions/            # Parameter-level permission gating (v0.2, in progress)
│   │   ├── models.py           # Permission enums and data models
│   │   └── checker.py          # Permission checker: safe/caution/dangerous/deny
│   ├── commands/               # Slash commands (stub — v0.3)
│   ├── session/                # Session persistence (stub — v0.2)
│   ├── memory/                 # Memory system (stub — v0.4)
│   └── utils/                  # Shared utilities
│       ├── exceptions.py       # MiniCodeError hierarchy (ConfigError, ProviderError, ToolError)
│       └── log.py              # structlog setup (console + JSON file output)
├── tests/                      # Mirror of src/ structure
│   ├── conftest.py             # clean_minicode_env fixture
│   ├── test_smoke.py           # Import smoke test
│   ├── test_cli/               # test_app.py, test_renderer.py, test_theme.py
│   ├── test_agent/             # test_loop.py
│   ├── test_providers/         # test_openai_compatible.py, test_registry.py, test_message_*.py
│   ├── test_tools/             # test_file_read.py, test_glob.py, test_grep.py, test_*.py
│   ├── test_config/            # test_loader.py
│   ├── test_permissions/       # test_checker.py
│   └── test_utils/             # test_exceptions.py, test_log.py
└── doc/                        # Design docs and task plan
    ├── minicode-task-plan.md
    └── minicode-design.md
```

## Key Design Decisions

- **Internal message format is OpenAI-compatible** — all providers translate to/from this format. Anthropic adapter converts OpenAI-format messages/tools to Anthropic API format and back.
- **Tools are plugin-style** — `@ToolRegistry.register` decorator on `BaseTool` subclasses. Each tool has a `risk_level`: `safe` (auto-allow), `caution` (ask once per session), `dangerous` (ask every time).
- **Default provider is DeepSeek** — `deepseek-v4-flash` model. OpenAI is also pre-configured as an alternative.
- **Search uses ripgrep** with a Python fallback (`pathlib.Path.rglob` + `re`) when `rg` is unavailable.
- **No database** — sessions are JSON files, memory is Markdown files, config is YAML. Git-friendly and transparent.
- **Tool execution is serial** — parallel execution is a future optimization.
- **Anthropic support is Phase 10** — current phases focus on OpenAI-compatible providers only.
- **Streaming output is NOT used for text rendering** — streamed deltas are collected with a "正在思考..." status spinner, then rendered as a single Markdown block. This avoids terminal duplicate-output issues. True streaming will be revisited after core functionality stabilizes.

## Data Flow

```
User input → ChatApp (prompt_toolkit)
  ├─ /prefix → Command.execute() → render result (stub)
  └─ text → ReAct Loop:
              → Build messages (system + history + user)
              → Provider.chat(messages, tools, stream=True)
              → Collect stream chunks (show "正在思考..." spinner)
              → Assemble text + tool_calls from deltas
              → Render text as Markdown
              → Execute tools serially → append tool results
              → Loop until text-only response or max_rounds (20)
```

## Risk Levels

- 🟢 `safe` (read_file, grep, glob) — execute silently
- 🟡 `caution` (write_file, edit_file) — ask first time per session, remember "always allow"
- 🔴 `dangerous` (bash) — ask every time

## Code Conventions

### Language & Comments
- **所有重要的代码必须添加必要的中文注释** (important code must have Chinese comments)
- **用户可见的文字必须使用中文** (user-visible text must be in Chinese)
- **所有 prompt 使用中文**，符合企业级、专业的提示词标准 (prompts in Chinese, enterprise-grade)

### Code Patterns

- Every module uses `from __future__ import annotations` at the top
- Import-heavy modules use `TYPE_CHECKING` guards to avoid circular imports:
  ```python
  if TYPE_CHECKING:
      from minicode.tools.registry import ToolRegistry
  ```
- Docstrings are Chinese, Google-style with `Args:`/`Returns:`/`Raises:` sections
- Loggers are created with `logger = get_logger(__name__)` (structlog wrapper)
- Pydantic models are used for all structured data (config, messages, tool results)
- Async throughout: `BaseTool.execute()` is `async`, providers use `AsyncIterator[StreamChunk]`
- The `AgentLoop` is the central orchestrator — it owns messages list, calls provider, executes tools

### Testing Patterns

- Tests mirror the `src/minicode/` structure exactly under `tests/`
- `MockProvider` (in `providers/registry.py`) is used for Agent Loop tests — it yields preset text chunks
- Test files import from `minicode.xxx` directly (the package is installed editable via `uv sync`)
- `conftest.py` provides `clean_minicode_env` fixture to isolate from host machine env vars

## Git Sync

When syncing to remote, push to both:
- GitHub: `git@github.com:PHJ20030616/MiniCode.git`
- Gitee: `https://gitee.com/phj20030616/mini-code.git`

Always ask the user for the changelog before committing and pushing.

## Implementation Phases

See `doc/minicode-task-plan.md` for the full plan. Summary:

| Version | Scope | Status |
|---------|-------|--------|
| v0.1 | OpenAI-compatible streaming + read-only tools + ReAct loop | ✅ Complete |
| v0.2 | Permissions + write/edit/shell tools + session persistence | 🔄 In progress (Task 3.1 complete) |
| v0.3 | Slash commands + session operations | ⏳ Planned |
| v0.4 | Memory system + multi-provider switching | ⏳ Planned |
| v1.0 | Anthropic provider + CI/CD + docs + ≥80% coverage | ⏳ Planned |

**Core principle**: Each phase must end with a runnable product. Tools serial before parallel. Test alongside development, not after.
