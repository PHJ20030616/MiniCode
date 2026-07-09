"""Shell 命令执行工具。

使用 asyncio.create_subprocess_exec 执行 shell 命令，
支持超时终止进程树、输出截断和跨平台适配。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path

from minicode.tools.base import BaseTool, ToolResult

# 用于区分"未传入参数"和"显式传入 None"
_MISSING = object()

# 单路输出最大字符数
_MAX_OUTPUT_LENGTH = 16000


# ---------------------------------------------------------------------------
# 纯函数：平台无关的 shell 调用构造
# ---------------------------------------------------------------------------


def _build_shell_invocation(command: str, platform: str) -> list[str]:
    """根据平台构造 shell 调用参数列表。

    Windows 使用 powershell.exe 并注入 UTF-8 编码前缀；
    其它平台使用 os.environ.get("SHELL") 或 /bin/sh。

    Args:
        command: 要执行的 shell 命令字符串
        platform: 平台标识（如 "win32"、"linux"、"darwin"）

    Returns:
        可直接传递给 asyncio.create_subprocess_exec 的参数列表
    """
    if platform.startswith("win"):
        # PowerShell 编码前缀保证中文 UTF-8 输出
        prefix = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        return [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            prefix + command,
        ]
    # Unix 类平台
    shell = os.environ.get("SHELL") or "/bin/sh"
    return [shell, "-c", command]


# ---------------------------------------------------------------------------
# 纯函数：timeout 参数校验与夹逼
# ---------------------------------------------------------------------------


def _normalize_timeout(value: object) -> tuple[int, str | None]:
    """校验并规范化 timeout 参数。

    默认值为 120，有效范围 [1, 600]。超出范围的自动夹逼。
    None / bool / 非整数 都会返回错误。

    Args:
        value: 原始 timeout 值，使用 _MISSING 表示未传入

    Returns:
        (timeout, error): 规范化后的 timeout 值和错误信息。
        无错误时 error 为 None。
    """
    if value is _MISSING:
        return 120, None
    if value is None:
        return 0, "参数 timeout 不能为 None"
    if isinstance(value, bool):
        return 0, "参数 timeout 不能为布尔值"
    if not isinstance(value, int):
        return 0, "参数 timeout 必须是整数"
    if value < 1:
        return 1, None  # 夹逼到最小值 1
    if value > 600:
        return 600, None  # 夹逼到最大值 600
    return value, None


# ---------------------------------------------------------------------------
# 纯函数：输出文本截断
# ---------------------------------------------------------------------------


def _truncate_output(text: str, max_length: int = _MAX_OUTPUT_LENGTH) -> str:
    """截断输出文本到指定长度，超过时追加长度提示。

    Args:
        text: 原始输出文本
        max_length: 最大字符数，默认 16000

    Returns:
        截断后的文本。未超过 max_length 时返回原文本。
    """
    if len(text) > max_length:
        return text[:max_length] + f"\n\n（输出已截断，原始长度 {len(text)} 字符）"
    return text


# ---------------------------------------------------------------------------
# 异步辅助：终止进程树
# ---------------------------------------------------------------------------


async def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """终止进程树。

    POSIX 使用 start_new_session + os.killpg 先 SIGTERM 再 SIGKILL；
    Windows 使用 taskkill /F /T /PID 兜底。

    Args:
        process: 要终止的子进程
    """
    if sys.platform.startswith("win"):
        try:
            kill_proc = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(process.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
        except Exception:
            pass  # 进程可能已终止
    else:
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                os.killpg(pgid, signal.SIGKILL)
                await process.wait()
        except (ProcessLookupError, ChildProcessError):
            pass  # 进程已不存在
        except Exception:
            pass  # 兜底


# ---------------------------------------------------------------------------
# 异步辅助：流读取
# ---------------------------------------------------------------------------


async def _read_stream(stream: asyncio.StreamReader | None) -> bytes:
    """读取异步流直到 EOF。

    Args:
        stream: 异步流读取器，为 None 时返回空字节串

    Returns:
        流中的所有字节
    """
    if stream is None:
        return b""
    chunks: list[bytes] = []
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# 构建输出文本
# ---------------------------------------------------------------------------


def _format_output(exit_code: int, stdout_str: str, stderr_str: str) -> str:
    """格式化工具输出。

    Args:
        exit_code: 进程退出码
        stdout_str: 标准输出文本
        stderr_str: 标准错误文本

    Returns:
        格式化的输出字符串
    """
    return (
        f"退出码：{exit_code}\n"
        f"\n"
        f"--- stdout ---\n"
        f"{stdout_str}\n"
        f"\n"
        f"--- stderr ---\n"
        f"{stderr_str}"
    )


# ---------------------------------------------------------------------------
# ShellTool
# ---------------------------------------------------------------------------


class ShellTool(BaseTool):
    """执行 shell 命令并返回输出。

    支持设置超时时间，默认 120 秒，最大 600 秒。
    注意：Windows 系统使用 PowerShell 语法，Unix 系统使用 sh/bash 语法。
    """

    name: str = "shell"
    description: str = (
        "执行 shell 命令并返回输出。支持设置超时时间（默认 120 秒，最大 600 秒）。"
        "注意：Windows 系统使用 PowerShell 语法，Unix 系统使用 sh/bash 语法，"
        "两种平台的命令语法不兼容。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令字符串",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒），默认 120，最大 600",
                "default": 120,
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        """执行 shell 命令。

        Args:
            **kwargs: 工具参数
                - command (str): 要执行的命令（必填）
                - timeout (int): 超时秒数（可选，默认 120）

        Returns:
            包含退出码、stdout、stderr 的执行结果
        """
        # ----- command 参数校验 -----
        command = kwargs.get("command")
        if not isinstance(command, str) or not command.strip():
            return ToolResult(success=False, output="参数 command 必须是有效的非空字符串")

        # ----- timeout 参数校验 -----
        timeout_raw = kwargs.get("timeout", _MISSING)
        timeout_val, err = _normalize_timeout(timeout_raw)
        if err is not None:
            return ToolResult(success=False, output=err)

        # ----- workspace_root 检查 -----
        if self.workspace_root is None:
            return ToolResult(success=False, output="工作区根路径未设置")
        cwd: Path = self.workspace_root

        # ----- 构造 shell 调用 -----
        invocation = _build_shell_invocation(command.strip(), sys.platform)

        # ----- 启动子进程 -----
        if sys.platform.startswith("win"):
            process = await asyncio.create_subprocess_exec(
                *invocation,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=os.environ,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *invocation,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=os.environ,
                start_new_session=True,
            )

        # ----- 并发读取 stdout/stderr，等待进程退出 -----
        stdout_task = asyncio.create_task(_read_stream(process.stdout))
        stderr_task = asyncio.create_task(_read_stream(process.stderr))

        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_val)
        except TimeoutError:
            timed_out = True
            await _kill_process_tree(process)
            # 确保进程已终止后管道关闭，读取任务完成
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), timeout=5)

        # 等待读取任务完成（管道已关闭或进程已终止）
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        def _task_bytes(task: asyncio.Task[bytes]) -> bytes:
            """安全获取已完成的任务中的字节数据。"""
            return task.result() if task.done() and not task.cancelled() else b""

        stdout_bytes = _task_bytes(stdout_task)
        stderr_bytes = _task_bytes(stderr_task)

        # ----- 解码与截断 -----
        stdout_str = _truncate_output(stdout_bytes.decode("utf-8", errors="replace"))
        stderr_str = _truncate_output(stderr_bytes.decode("utf-8", errors="replace"))

        if timed_out:
            return ToolResult(
                success=False,
                output=(
                    f"退出码：(超时)\n"
                    f"执行超时（已终止进程）\n"
                    f"\n"
                    f"--- stdout ---\n"
                    f"{stdout_str}\n"
                    f"\n"
                    f"--- stderr ---\n"
                    f"{stderr_str}"
                ),
            )

        # ----- 正常结束 -----
        exit_code = process.returncode or 0
        output = _format_output(exit_code, stdout_str, stderr_str)
        return ToolResult(success=(exit_code == 0), output=output)
