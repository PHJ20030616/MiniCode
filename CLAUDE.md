# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MiniCode is a simplified Claude Code clone — a Python CLI tool for AI-assisted programming. It's an open-source project built from scratch, aimed at being a portfolio/resume piece with emphasis on engineering quality and architecture design.

## Tech Stack

| Category | Technology |
|----------|-----------|
| Language | Python 3.12+ |
| Package manager | uv |
| CLI framework | Typer (arg parsing) + Rich (rendering) + prompt_toolkit (input) |
| Data models | Pydantic v2 |
| HTTP | httpx (async) |
| LLM SDK | `openai` SDK for OpenAI-compatible; `anthropic` SDK for Anthropic (later phase) |
| Logging | structlog (structured, JSON file output + Rich console output) |
| Testing | pytest + pytest-asyncio |
| Linting | ruff |
| Type checking | mypy (strict mode) |

## Commands

```bash
# Install dependencies
uv sync

# Run the CLI
uv run minicode [--model <model>] [--provider <provider>] [--session <id>] [--trust] [--verbose] [--debug]

# Run tests
pytest                                    # all tests
pytest tests/test_tools/test_file_read.py # single test file
pytest -k "test_name_pattern"             # filter by name
pytest --cov=src/minicode --cov-report=term  # with coverage

# Lint & type check
ruff check .
mypy src/minicode
```

## Architecture

Three-layer architecture:

```
CLI Layer (Rich + prompt_toolkit)
  → Renderer (markdown, syntax highlight, streaming)
  → Input (autocomplete, history, multiline)
  → Command Router (/commands vs Agent Loop)

Agent Layer
  → ReAct Agent Loop (LLM ⇄ tool calls ⇄ results ⇄ repeat, max 20 rounds)
  → Provider Adapter (internal OpenAI-compatible format → provider-specific translation)
  → Tool Registry (plugin-style decorator registration, risk-level gating)

Storage Layer
  → Sessions (JSON files under .minicode/sessions/)
  → Memory (Markdown + YAML frontmatter, one file per memory)
  → Config (multi-layer YAML: CLI > ENV > ./.minicode/ > ~/.minicode/ > defaults)
```

## Key Design Decisions

- **Internal message format is OpenAI-compatible** — all providers translate to/from this format. Anthropic adapter converts OpenAI-format messages/tools to Anthropic API format and back.
- **Tools are plugin-style** — `@ToolRegistry.register` decorator on `BaseTool` subclasses. Each tool has a `risk_level`: `safe` (auto-allow), `caution` (ask once per session), `dangerous` (ask every time).
- **MVP uses `openai` SDK directly** rather than raw httpx SSE parsing — less error-prone for the initial build.
- **Search uses ripgrep** with a Python fallback (`pathlib.Path.rglob` + `re`) when `rg` is unavailable.
- **No database** — sessions are JSON files, memory is Markdown files, config is YAML. Git-friendly and transparent.
- **Tool execution is serial in MVP** — parallel execution is a future optimization.
- **Anthropic support is Phase 4** — Phases 1–3 focus on OpenAI-compatible providers only.

## Directory Structure (Planned)

```
MiniCode/
├── pyproject.toml
├── src/minicode/
│   ├── main.py              # Typer CLI entry point
│   ├── cli/                 # Terminal UI (app loop, renderer, input, theme)
│   ├── agent/               # ReAct loop, context window, system prompt
│   ├── providers/           # BaseProvider + OpenAI-compatible + Anthropic adapters
│   ├── tools/               # BaseTool, registry, file ops, bash, grep, glob
│   ├── commands/            # Slash commands (/new, /session, /model, etc.)
│   ├── config/              # Multi-layer YAML config loading + Pydantic models
│   ├── session/             # Session CRUD via JSON files
│   ├── memory/              # Memory CRUD via Markdown + frontmatter files
│   ├── permissions/         # Risk-level gating + trust mode
│   └── utils/               # Logging setup, custom exceptions
└── tests/                   # Mirror of src/ structure
```

## Implementation Phases

The project follows a 6-phase plan (see `doc/minicode-task-plan.md`):
- **Phase 0**: Project scaffolding, CLI entry, config system, logging
- **Phase 1**: Single-turn chat (no tools) — Provider adapter + streaming renderer
- **Phase 2**: Tool system + ReAct Agent Loop (file ops, bash, search, permissions)
- **Phase 3**: Sessions, slash commands, memory system, UX polish
- **Phase 4**: Anthropic provider + custom provider support
- **Phase 5**: Test coverage (≥80%), CI/CD, docs, error handling, release prep

**Core principle**: Each phase must end with a runnable product. Tools serial before parallel. OpenAI-compatible first. Test alongside development, not after.

## Data Flow

```
User input → CommandRouter
  ├─ /prefix → Command.execute() → render result
  └─ text → ReAct Loop:
              → Build messages (system + memory + history + user)
              → Provider.chat(messages, tools, stream=True)
              → Stream text deltas to Rich renderer
              → Collect tool_calls → permission check → execute → append results
              → Loop until text response or max_rounds (20)
```

## Risk Levels

- 🟢 `safe` (read_file, grep, glob) — execute silently
- 🟡 `caution` (write_file, edit_file) — ask first time per session, remember "always allow"
- 🔴 `dangerous` (bash) — ask every time

`--trust` flag skips all permission prompts.
