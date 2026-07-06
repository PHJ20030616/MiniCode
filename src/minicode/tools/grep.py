"""文件内容搜索工具。

优先调用 ripgrep (rg) 加速搜索，不可用时自动降级到 Python re + Path.rglob。
默认最多返回 250 行匹配结果，超出部分会提示已截断。
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

from minicode.tools.base import BaseTool, ToolResult

# 默认最大输出行数
DEFAULT_MAX_LINES = 250

# 默认跳过搜索的目录名
SKIP_DIRS = frozenset({
    '.git', '.venv', 'venv', '__pycache__', 'node_modules',
    '.tox', '.mypy_cache', '.pytest_cache', '.ruff_cache',
})


def _should_skip_file(file_path: Path) -> bool:
    """检查文件是否位于需要跳过的目录（如 .git、.venv）中。"""
    return any(part in SKIP_DIRS for part in file_path.parts)


class GrepFiles(BaseTool):
    """在文件内容中搜索正则表达式模式。

    优先使用 ripgrep (rg) 加速搜索，不可用时自动降级为 Python 实现。
    默认最多返回 250 行匹配结果，超出部分会提示已截断。
    """

    name: str = "grep"
    description: str = (
        "在文件内容中搜索正则表达式模式，支持按文件 glob 类型过滤。"
        "优先使用 ripgrep 加速搜索，不可用时自动使用 Python 实现。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "要搜索的正则表达式模式",
            },
            "glob": {
                "type": "string",
                "description": "文件类型过滤 glob 模式，例如 *.py、*.md、*.ts（可选）",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        # ----- pattern 参数校验 -----
        pattern = kwargs.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            return ToolResult(success=False, output="参数 pattern 必须是有效的非空正则表达式")

        if self.workspace_root is None:
            return ToolResult(success=False, output="工作区根路径未设置")

        # ----- glob 参数校验 -----
        file_glob = kwargs.get("glob")
        if file_glob is not None and not isinstance(file_glob, str):
            return ToolResult(success=False, output="参数 glob 必须是字符串")

        # 选择搜索方式
        if shutil.which("rg"):
            return await self._search_with_rg(pattern, file_glob)
        try:
            return await asyncio.wait_for(
                self._search_with_python(pattern, file_glob),
                timeout=60,
            )
        except TimeoutError:
            return ToolResult(success=False, output="Python 搜索超时（60 秒）")

    async def _search_with_rg(
        self, pattern: str, file_glob: str | None
    ) -> ToolResult:
        """使用 ripgrep 执行搜索。"""
        cmd = [
            "rg",
            "--no-heading",
            "--line-number",
            "--color",
            "never",
            "-E",
            "utf-8",
        ]
        if file_glob:
            cmd.extend(["--glob", file_glob])
        cmd.extend([pattern, str(self.workspace_root)])

        try:
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, timeout=30
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, output="搜索超时（30 秒）")

        if result.returncode == 2:
            err_msg = result.stderr.decode("utf-8", errors="replace")[:500]
            return ToolResult(
                success=False,
                output=f"ripgrep 搜索失败：{err_msg}",
            )

        return self._format_matches(
            result.stdout.decode("utf-8", errors="replace"), pattern
        )

    async def _search_with_python(
        self, pattern: str, file_glob: str | None
    ) -> ToolResult:
        """使用 Python re 实现搜索。"""
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return ToolResult(success=False, output=f"正则表达式无效：{e}")

        # workspace_root 已在 execute() 中保证非 None
        root = self.workspace_root
        assert root is not None

        # 收集要搜索的文本文件，跳过黑名单目录
        if file_glob:
            files = [
                f for f in root.rglob(file_glob)
                if f.is_file() and not _should_skip_file(f)
            ]
        else:
            files = [
                f for f in root.rglob("*")
                if f.is_file() and not _should_skip_file(f)
            ]

        matches: list[str] = []
        total_matches = 0
        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            try:
                rel_path = file_path.relative_to(root)
            except ValueError:
                continue

            for line_no, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    total_matches += 1
                    if len(matches) < DEFAULT_MAX_LINES:
                        matches.append(f"{rel_path}:{line_no}:{line}")

        return self._format_matches(
            "\n".join(matches), pattern, total=total_matches
        )

    def _format_matches(
        self, raw_output: str, pattern: str, total: int | None = None
    ) -> ToolResult:
        """统一格式化搜索匹配结果。

        Args:
            raw_output: 原始匹配输出（每行一个匹配）
            pattern: 搜索模式（用于错误消息）
            total: 实际总匹配数。为 None 时使用 raw_output 中的行数
        """
        lines = [ln for ln in raw_output.splitlines() if ln.strip()]
        if not lines:
            return ToolResult(
                success=True, output=f"没有找到匹配的行（模式：{pattern}）"
            )

        actual_total = total if total is not None else len(lines)
        truncated = False
        if actual_total > DEFAULT_MAX_LINES:
            lines = lines[:DEFAULT_MAX_LINES]
            truncated = True

        output = "\n".join(lines)
        summary = f"搜索到 {actual_total} 行匹配"
        if truncated:
            summary += f"（显示前 {DEFAULT_MAX_LINES} 行）"

        result = f"{summary}：\n\n{output}"
        if truncated:
            result += (
                f"\n...（结果已截断，共 {actual_total} 行匹配，"
                f"仅显示前 {DEFAULT_MAX_LINES} 行）"
            )

        return ToolResult(success=True, output=result)
