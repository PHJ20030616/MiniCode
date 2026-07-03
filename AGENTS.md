# Repository Guidelines

## Project Structure & Module Organization

MiniCode is a Python 3.12+ CLI project managed with `uv`. Source code lives under `src/minicode/`. Current module groups mirror the planned architecture:

- `src/minicode/main.py`: Typer CLI entry point.
- `src/minicode/cli/`: terminal UI and rendering code.
- `src/minicode/agent/`: ReAct loop and context handling.
- `src/minicode/providers/`: LLM provider adapters.
- `src/minicode/tools/`: tool definitions and registry.
- `src/minicode/config/`, `session/`, `memory/`, `permissions/`, `commands/`, `utils/`: supporting layers.
- `tests/`: pytest tests, mirroring source modules where practical.
- `doc/`: design notes and task plans.

Runtime data such as `.minicode/`, virtual environments, caches, and local assistant settings are ignored by git.

## Build, Test, and Development Commands

- `uv sync`: install dependencies from `pyproject.toml` and `uv.lock`.
- `uv run minicode --help`: run the CLI locally.
- `uv run python -c "import minicode"`: smoke-test package import.
- `uv run pytest`: run the test suite.
- `uv run ruff check .`: lint all tracked Python code.
- `uv run mypy src/minicode`: type-check the package.
- `uv build`: build source and wheel distributions when preparing releases.

## Coding Style & Naming Conventions

Use 4-space indentation and Python 3.12 syntax. Prefer explicit type annotations for public functions and new internal APIs. Keep modules small and aligned with their architecture layer. Use `snake_case` for functions, modules, and variables; `PascalCase` for classes; uppercase for constants. Ruff is the source of truth for linting, with imports sorted by Ruff rules. Mypy is configured with typed function definitions required.

## Testing Guidelines

Tests use `pytest`, `pytest-asyncio`, `pytest-mock`, and `pytest-cov`. Name test files as `test_*.py` and test functions as `test_*`. Place module-specific tests in matching folders, for example `tests/test_tools/test_file_read.py`. Add smoke or contract tests for new architecture boundaries. Before handing off changes, run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src/minicode`.

## Commit & Pull Request Guidelines

Existing history uses short, imperative-style commit messages. Chinese or English is acceptable; keep the first line focused on the user-visible change or task completed. For pull requests, include a concise summary, validation commands run, linked issue or task number, and notes about any skipped checks. Include screenshots only for terminal UI changes where output formatting matters.

## Security & Configuration Tips

Never commit `.env`, `.minicode/`, `.venv/`, cache folders, or local assistant settings. Keep `uv.lock` committed for reproducible installs. File tools must stay workspace-bound and avoid reading sensitive paths by default.
