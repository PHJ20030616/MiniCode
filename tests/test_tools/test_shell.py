"""Shell 工具单元测试。

覆盖纯函数（_build_shell_invocation、_normalize_timeout、_truncate_output）、
参数校验、执行成功/失败、超时处理、输出截断、中文编码、工作目录/环境变量继承、注册器集成。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from minicode.tools.shell import (
    _MISSING,
    ShellTool,
    _build_shell_invocation,
    _normalize_timeout,
    _truncate_output,
)

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def tool() -> ShellTool:
    """创建 ShellTool 实例（不含 workspace_root）。"""
    return ShellTool()


@pytest.fixture
def tool_with_root(tmp_path: Path) -> ShellTool:
    """创建已设置 workspace_root 的 ShellTool 实例。"""
    return ShellTool(workspace_root=tmp_path)


# =========================================================================
# 纯函数：_build_shell_invocation
# =========================================================================


class TestBuildShellInvocation:
    """_build_shell_invocation 平台派发测试。"""

    def test_windows_uses_powershell_with_utf8_prefix(self) -> None:
        """Windows 平台使用 powershell.exe 加 UTF-8 编码前缀。"""
        cmd = "echo hello"
        result = _build_shell_invocation(cmd, "win32")
        assert result[0] == "powershell.exe"
        assert "-NoProfile" in result
        assert "-NonInteractive" in result
        assert "-Command" in result
        prefix = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        assert result[-1].startswith(prefix)
        assert result[-1].endswith(cmd)

    def test_windows_platform_win_any(self) -> None:
        """任何以 'win' 开头的平台都命中 Windows 分支。"""
        result = _build_shell_invocation("dir", "win64")
        assert result[0] == "powershell.exe"

    def test_unix_default_shell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unix 平台默认使用 /bin/sh -c。"""
        monkeypatch.delenv("SHELL", raising=False)
        result = _build_shell_invocation("echo hello", "linux")
        assert result[0] == "/bin/sh"
        assert result[1] == "-c"
        assert result[2] == "echo hello"

    def test_unix_respects_shell_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unix 平台使用 SHELL 环境变量。"""
        monkeypatch.setenv("SHELL", "/bin/bash")
        result = _build_shell_invocation("echo hello", "linux")
        assert result[0] == "/bin/bash"
        assert result[1] == "-c"
        assert result[2] == "echo hello"

    def test_unix_shell_env_empty_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unix 平台 SHELL 为空时回退到 /bin/sh。"""
        monkeypatch.setenv("SHELL", "")
        result = _build_shell_invocation("echo hello", "linux")
        assert result[0] == "/bin/sh"

    def test_macos_darwin_uses_unix_branch(self) -> None:
        """macOS (darwin) 命中 Unix 分支。"""
        result = _build_shell_invocation("echo hello", "darwin")
        assert result[1] == "-c"

    def test_command_preserved_unchanged(self) -> None:
        """原始命令字符串不变地传递到调用列表末尾。"""
        cmd = "echo hello && echo world"
        result = _build_shell_invocation(cmd, "linux")
        assert result[-1] == cmd

        result_win = _build_shell_invocation(cmd, "win32")
        assert result_win[-1].endswith(cmd)


# =========================================================================
# 纯函数：_normalize_timeout
# =========================================================================


class TestNormalizeTimeout:
    """_normalize_timeout 参数校验与夹逼测试。"""

    def test_missing_returns_default(self) -> None:
        """缺省值返回 120。"""
        val, err = _normalize_timeout(_MISSING)
        assert val == 120
        assert err is None

    def test_none_returns_error(self) -> None:
        """None 返回错误。"""
        val, err = _normalize_timeout(None)
        assert val == 0
        assert err is not None
        assert "None" in err

    def test_bool_true_rejected(self) -> None:
        """bool True 被拒绝。"""
        val, err = _normalize_timeout(True)
        assert err is not None
        assert "布尔" in err

    def test_bool_false_rejected(self) -> None:
        """bool False 被拒绝。"""
        val, err = _normalize_timeout(False)
        assert err is not None

    def test_float_rejected(self) -> None:
        """浮点数被拒绝。"""
        val, err = _normalize_timeout(30.5)
        assert err is not None
        assert "整数" in err

    def test_string_rejected(self) -> None:
        """字符串被拒绝。"""
        val, err = _normalize_timeout("abc")
        assert err is not None
        assert "整数" in err

    def test_zero_clamps_to_one(self) -> None:
        """0 夹逼到 1。"""
        val, err = _normalize_timeout(0)
        assert val == 1
        assert err is None

    def test_negative_clamps_to_one(self) -> None:
        """负值夹逼到 1。"""
        val, err = _normalize_timeout(-5)
        assert val == 1
        assert err is None

    def test_above_max_clamps_to_600(self) -> None:
        """超过 600 的值夹逼到 600。"""
        val, err = _normalize_timeout(1000)
        assert val == 600
        assert err is None

    def test_valid_mid_range(self) -> None:
        """范围内的有效值保持不变。"""
        val, err = _normalize_timeout(30)
        assert val == 30
        assert err is None

    def test_boundary_one(self) -> None:
        """边界值 1 保持不变。"""
        val, err = _normalize_timeout(1)
        assert val == 1
        assert err is None

    def test_boundary_600(self) -> None:
        """边界值 600 保持不变。"""
        val, err = _normalize_timeout(600)
        assert val == 600
        assert err is None


# =========================================================================
# 纯函数：_truncate_output
# =========================================================================


class TestTruncateOutput:
    """_truncate_output 截断测试。"""

    def test_short_text_not_truncated(self) -> None:
        """短文本不截断。"""
        result = _truncate_output("hello", 10)
        assert result == "hello"

    def test_exact_max_length_not_truncated(self) -> None:
        """刚好等于 max_length 不截断。"""
        text = "a" * 100
        result = _truncate_output(text, 100)
        assert result == text
        assert len(result) == 100

    def test_exceeds_max_length_truncated(self) -> None:
        """超过 max_length 截断并追加提示。"""
        text = "a" * 200
        result = _truncate_output(text, 100)
        assert len(result) < 200
        assert "截断" in result
        assert "原始长度 200" in result

    def test_truncation_message_does_not_exceed_limit(self) -> None:
        """截断后原始文本部分不超过 max_length。"""
        text = "x" * 50000
        result = _truncate_output(text, 16000)
        assert result.startswith("x" * 16000)
        assert "输出已截断" in result
        assert "原始长度 50000" in result

    def test_empty_string(self) -> None:
        """空字符串不截断。"""
        result = _truncate_output("", 100)
        assert result == ""


# =========================================================================
# 参数校验
# =========================================================================


class TestShellParameterValidation:
    """ShellTool 参数校验测试。"""

    async def test_missing_command(self, tool: ShellTool) -> None:
        """command 缺失。"""
        result = await tool.execute()
        assert result.success is False

    async def test_command_is_none(self, tool: ShellTool) -> None:
        """command 为 None。"""
        result = await tool.execute(command=None)  # type: ignore[arg-type]
        assert result.success is False

    async def test_command_is_empty(self, tool: ShellTool) -> None:
        """command 为空字符串。"""
        result = await tool.execute(command="")
        assert result.success is False

    async def test_command_is_whitespace(self, tool: ShellTool) -> None:
        """command 为纯空白。"""
        result = await tool.execute(command="   ")
        assert result.success is False

    async def test_command_is_non_string(self, tool: ShellTool) -> None:
        """command 为非字符串。"""
        result = await tool.execute(command=123)  # type: ignore[arg-type]
        assert result.success is False

    async def test_timeout_is_none(self, tool: ShellTool) -> None:
        """timeout 显式传入 None。"""
        result = await tool.execute(command="echo hi", timeout=None)  # type: ignore[arg-type]
        assert result.success is False

    async def test_timeout_is_bool(self, tool: ShellTool) -> None:
        """timeout 传布尔值。"""
        result = await tool.execute(command="echo hi", timeout=True)  # type: ignore[arg-type]
        assert result.success is False

    async def test_timeout_is_non_int(self, tool: ShellTool) -> None:
        """timeout 传非整数。"""
        result = await tool.execute(command="echo hi", timeout="abc")  # type: ignore[arg-type]
        assert result.success is False

    async def test_no_workspace_root(self, tool: ShellTool) -> None:
        """workspace_root 未设置时返回错误。"""
        result = await tool.execute(command="echo hi")
        assert result.success is False
        assert "工作区" in result.output


# =========================================================================
# 执行行为
# =========================================================================


class TestShellExecution:
    """Shell 命令执行场景。"""

    async def test_simple_echo(self, tool_with_root: ShellTool) -> None:
        """简单 echo 命令成功执行。"""
        result = await tool_with_root.execute(command="echo hello")
        assert result.success is True
        assert "退出码" in result.output
        assert "hello" in result.output

    async def test_exit_code_zero(self, tool_with_root: ShellTool) -> None:
        """退出码为 0 时 success=True。"""
        result = await tool_with_root.execute(command="exit 0")
        assert result.success is True
        assert "退出码" in result.output
        assert "0" in result.output

    async def test_exit_code_non_zero(self, tool_with_root: ShellTool) -> None:
        """退出码非零时 success=False。"""
        result = await tool_with_root.execute(command="exit 42")
        assert result.success is False
        assert "退出码" in result.output
        assert "42" in result.output

    async def test_command_not_found(self, tool_with_root: ShellTool) -> None:
        """不存在的命令返回 success=False 并包含错误信息。"""
        result = await tool_with_root.execute(
            command="nonexistent_cmd_xyz_123_assert"
        )
        assert result.success is False
        assert "退出码" in result.output


# =========================================================================
# 超时处理
# =========================================================================


class TestShellTimeout:
    """超时行为测试。"""

    async def test_timeout_kills_process_and_returns_partial_output(
        self, tool_with_root: ShellTool,
    ) -> None:
        """超时后终止进程并返回部分输出。"""
        # 使用长时间运行的命令，设置 1 秒超时
        if sys.platform.startswith("win"):
            cmd = "Start-Sleep -Seconds 30"
        else:
            cmd = "sleep 30"
        result = await tool_with_root.execute(command=cmd, timeout=1)
        assert result.success is False
        assert "超时" in result.output
        assert "终止" in result.output

    async def test_normal_command_within_timeout(self, tool_with_root: ShellTool) -> None:
        """正常命令在 timeout 内执行完毕。"""
        result = await tool_with_root.execute(
            command=rf"""{sys.executable} -X utf8 -c "print('fast')" """,
            timeout=30,
        )
        assert result.success is True
        assert "fast" in result.output


# =========================================================================
# 输出截断
# =========================================================================


class TestShellTruncation:
    """输出截断测试。"""

    async def test_stdout_truncated(self, tool_with_root: ShellTool) -> None:
        """stdout 超过 16000 字符时截断。"""
        cmd = rf"""{sys.executable} -X utf8 -c "print('x'*20000)" """
        result = await tool_with_root.execute(command=cmd)
        assert result.success is True
        assert "输出已截断" in result.output
        assert "原始长度" in result.output

    async def test_short_output_not_truncated(self, tool_with_root: ShellTool) -> None:
        """短输出不截断。"""
        cmd = rf"""{sys.executable} -X utf8 -c "print('short')" """
        result = await tool_with_root.execute(command=cmd)
        assert result.success is True
        assert "输出已截断" not in result.output


# =========================================================================
# Windows PowerShell 原生命令测试（仅 Windows 运行）
# =========================================================================


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="PowerShell 原生命令测试仅适用于 Windows",
)
class TestShellWindowsNative:
    """Windows PowerShell 原生命令测试。

    使用 Get-Location、$env:PATH、Write-Output 等 PowerShell 原生 cmdlet，
    验证 PowerShell 在当前平台的正确行为，不依赖 Python 子进程。
    """

    async def test_get_location_contains_workspace_root(
        self, tool_with_root: ShellTool, tmp_path: Path,
    ) -> None:
        """Get-Location 输出的路径包含 workspace_root。"""
        result = await tool_with_root.execute(command="Get-Location")
        assert result.success is True
        assert str(tmp_path) in result.output

    async def test_env_path_readable(
        self, tool_with_root: ShellTool,
    ) -> None:
        """$env:PATH 可读取 PATH 环境变量。"""
        result = await tool_with_root.execute(command="Write-Output $env:PATH")
        assert result.success is True
        # PATH 应包含 "Windows" 或 "System32" 等系统目录
        assert "Windows" in result.output or "System32" in result.output

    async def test_chinese_write_output(
        self, tool_with_root: ShellTool,
    ) -> None:
        """Write-Output 输出 UTF-8 中文。"""
        result = await tool_with_root.execute(
            command="Write-Output '你好，世界！PowerShell 原生支持'",
        )
        assert result.success is True
        assert "你好" in result.output
        assert "PowerShell" in result.output

    async def test_get_child_item_root(
        self, tool_with_root: ShellTool, tmp_path: Path,
    ) -> None:
        """Get-ChildItem 列出工作区文件。"""
        (tmp_path / "ps_test.txt").write_text("ps_content", encoding="utf-8")
        result = await tool_with_root.execute(command="Get-ChildItem -Name")
        assert result.success is True
        assert "ps_test.txt" in result.output

    async def test_exit_code_via_powershell(
        self, tool_with_root: ShellTool,
    ) -> None:
        """PowerShell exit 命令正确传递退出码。"""
        result = await tool_with_root.execute(command="exit 42")
        assert result.success is False
        assert "退出码" in result.output
        assert "42" in result.output

    async def test_write_error_to_stderr(
        self, tool_with_root: ShellTool,
    ) -> None:
        """Write-Error 输出到 stderr。"""
        result = await tool_with_root.execute(
            command="Write-Error '自定义错误信息'",
        )
        assert result.success is False
        assert "stderr" in result.output
        assert "自定义错误信息" in result.output

    async def test_long_running_command_timeout(
        self, tool_with_root: ShellTool,
    ) -> None:
        """PowerShell Start-Sleep 超时后终止。"""
        result = await tool_with_root.execute(
            command="Start-Sleep -Seconds 30",
            timeout=1,
        )
        assert result.success is False
        assert "超时" in result.output

    async def test_batch_echo_with_separator(
        self, tool_with_root: ShellTool,
    ) -> None:
        """多条命令使用分号分隔执行。"""
        result = await tool_with_root.execute(
            command="Write-Output 'first'; Write-Output 'second'; Write-Output 'third'",
        )
        assert result.success is True
        assert "退出码" in result.output
        assert "first" in result.output
        assert "second" in result.output
        assert "third" in result.output

    async def test_get_date(
        self, tool_with_root: ShellTool,
    ) -> None:
        """Get-Date 执行成功。"""
        result = await tool_with_root.execute(command="Get-Date")
        assert result.success is True
        assert "202" in result.output or "20" in result.output


# =========================================================================
# 中文编码
# =========================================================================


class TestShellChinese:
    """中文 UTF-8 输出测试。"""

    async def test_chinese_output(self, tool_with_root: ShellTool) -> None:
        """中文内容正常输出。"""
        cmd = rf"""{sys.executable} -X utf8 -c "print('你好，世界！这是一个中文测试。')" """
        result = await tool_with_root.execute(command=cmd)
        assert result.success is True
        assert "你好" in result.output
        assert "中文测试" in result.output

    async def test_chinese_in_stderr(self, tool_with_root: ShellTool) -> None:
        """stderr 中的中文内容正常输出。"""
        code = "import sys; sys.stderr.write('错误信息：测试'); sys.exit(1)"
        cmd = rf"""{sys.executable} -X utf8 -c "{code}" """
        result = await tool_with_root.execute(command=cmd)
        assert result.success is False
        assert "错误信息" in result.output


# =========================================================================
# 工作目录与环境变量
# =========================================================================


class TestShellCwdEnv:
    """cwd 和环境变量继承测试。"""

    async def test_cwd_is_workspace_root(self, tool_with_root: ShellTool, tmp_path: Path) -> None:
        """cwd 固定为 workspace_root。"""
        (tmp_path / "cwd_marker.txt").write_text("cwd_ok", encoding="utf-8")
        cmd = rf"""{sys.executable} -X utf8 -c "import os; print(os.getcwd())" """
        result = await tool_with_root.execute(command=cmd)
        assert result.success is True
        assert str(tmp_path) in result.output

    async def test_env_is_inherited(self, tool_with_root: ShellTool) -> None:
        """环境变量继承 os.environ。"""
        cmd = rf"""{sys.executable} -X utf8 -c "import os; print('PATH' in os.environ)" """
        result = await tool_with_root.execute(command=cmd)
        assert result.success is True
        assert "True" in result.output


# =========================================================================
# 输出格式
# =========================================================================


class TestShellOutputFormat:
    """输出格式验证：退出码、stdout、stderr 必须全部包含。"""

    async def test_output_contains_exit_code_stdout_stderr(
        self, tool_with_root: ShellTool,
    ) -> None:
        """成功命令的输出包含退出码、stdout、stderr 三个部分。"""
        result = await tool_with_root.execute(command="echo hello")
        assert "退出码" in result.output
        assert "stdout" in result.output or "标准输出" in result.output
        assert "stderr" in result.output or "标准错误" in result.output

    async def test_failure_output_contains_all_sections(
        self, tool_with_root: ShellTool,
    ) -> None:
        """失败命令的输出同样包含三个部分。"""
        result = await tool_with_root.execute(command="exit 1")
        assert "退出码" in result.output
        assert "stdout" in result.output or "标准输出" in result.output
        assert "stderr" in result.output or "标准错误" in result.output


# =========================================================================
# 集成测试
# =========================================================================


class TestShellIntegration:
    """注册器集成验证。"""

    async def test_execute_injects_workspace(self, tmp_path: Path) -> None:
        """通过 ToolRegistry 执行时 workspace_root 被正确注入。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(ShellTool)

        result = await registry.execute_tool(
            "shell",
            {"command": f"{sys.executable} -X utf8 -c \"print('injected')\""},
            tmp_path,
        )
        assert result.success is True
        assert "injected" in result.output

    async def test_tool_schema_compatible(self) -> None:
        """工具 schema 符合 OpenAI function calling 格式。"""
        tool = ShellTool()
        schema = tool.get_tool_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "shell"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"

        props = schema["function"]["parameters"]["properties"]
        assert "command" in props
        assert props["command"]["type"] == "string"
        assert "timeout" in props
        assert props["timeout"]["type"] == "integer"

        required = schema["function"]["parameters"]["required"]
        assert "command" in required
        assert "timeout" not in required

        assert schema["function"]["parameters"].get("additionalProperties") is False

    async def test_schema_registered_in_registry(self) -> None:
        """注册后 schema 通过 registry 正确导出。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(ShellTool)

        schemas = registry.get_tools_schema()
        names = [s["function"]["name"] for s in schemas]
        assert "shell" in names

    async def test_default_registry_contains_shell(self) -> None:
        """create_default_registry 包含 shell 工具。"""
        from minicode.tools import create_default_registry

        registry = create_default_registry()
        assert registry.has_tool("shell") is True
