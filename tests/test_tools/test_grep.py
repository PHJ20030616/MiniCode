"""Grep 文件内容搜索工具单元测试。

覆盖 rg 路径、Python fallback 路径、文件 glob 过滤、输出截断、错误场景。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from minicode.tools.grep import DEFAULT_MAX_LINES, GrepFiles


@pytest.fixture
def tool() -> GrepFiles:
    """创建 GrepFiles 工具实例（不含 workspace_root）。"""
    return GrepFiles()


@pytest.fixture
def tool_with_root(tmp_path: Path) -> GrepFiles:
    """创建已设置 workspace_root 的 GrepFiles 工具实例。"""
    return GrepFiles(workspace_root=tmp_path)


# ============================================================
# Python fallback 路径测试
# 这些测试通过 mock shutils.which("rg") 返回 None 来强制走 Python 路径
# ============================================================


class TestGrepPythonFallback:
    """Python re 实现的搜索功能测试。"""

    @pytest.fixture(autouse=True)
    def _disable_rg(self, mocker) -> None:
        """禁用 ripgrep，强制使用 Python 实现。"""
        mocker.patch("minicode.tools.grep.shutil.which", return_value=None)

    async def test_basic_search(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """基本文本搜索。"""
        (tmp_path / "hello.py").write_text(
            "def hello():\n    print('hello world')\n", encoding="utf-8"
        )

        result = await tool_with_root.execute(pattern="hello")
        assert result.success is True
        assert "hello.py" in result.output
        assert "hello" in result.output
        assert "搜索到" in result.output

    async def test_no_match(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """无匹配结果。"""
        (tmp_path / "data.txt").write_text("nothing here", encoding="utf-8")

        result = await tool_with_root.execute(pattern="nonexistent")
        assert result.success is True
        assert "没有找到匹配的行" in result.output

    async def test_search_multiple_files(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """搜索多个文件。"""
        (tmp_path / "a.py").write_text("def foo(): pass\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def bar(): pass\n", encoding="utf-8")
        (tmp_path / "c.py").write_text("def baz(): pass\n", encoding="utf-8")

        result = await tool_with_root.execute(pattern="def ")
        assert result.success is True
        assert "a.py" in result.output
        assert "b.py" in result.output
        assert "c.py" in result.output

    async def test_search_with_file_glob(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """使用文件 glob 过滤搜索范围。"""
        (tmp_path / "code.py").write_text("target = 1\n", encoding="utf-8")
        (tmp_path / "data.md").write_text("target = 2\n", encoding="utf-8")

        result = await tool_with_root.execute(pattern="target", glob="*.py")
        assert result.success is True
        assert "code.py" in result.output
        assert "data.md" not in result.output

    async def test_search_recursive(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """递归搜索子目录中的文件。"""
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.txt").write_text("deep content\n", encoding="utf-8")

        result = await tool_with_root.execute(pattern="deep")
        assert result.success is True
        assert str(Path("subdir/nested.txt")) in result.output

    async def test_search_with_regex_pattern(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """使用正则表达式作为搜索模式。"""
        (tmp_path / "data.txt").write_text(
            "abc123\ndef456\nxyz789\n", encoding="utf-8"
        )

        result = await tool_with_root.execute(pattern=r"\d{3}")
        assert result.success is True
        assert "abc123" in result.output
        assert "def456" in result.output
        assert "xyz789" in result.output

    async def test_search_chinese(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """搜索中文内容。"""
        (tmp_path / "cn.txt").write_text(
            "你好世界\n这是测试文件\n", encoding="utf-8"
        )

        result = await tool_with_root.execute(pattern="你好")
        assert result.success is True
        assert "cn.txt" in result.output
        assert "你好" in result.output

    async def test_search_skips_git_dir(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """搜索跳过 .git 目录中的文件。"""
        git_dir = tmp_path / ".git" / "objects"
        git_dir.mkdir(parents=True)
        (git_dir / "pack").write_text("secret content\n", encoding="utf-8")

        (tmp_path / "code.py").write_text("content here\n", encoding="utf-8")

        result = await tool_with_root.execute(pattern="content")
        assert result.success is True
        assert "搜索到 1 行匹配" in result.output
        assert ".git" not in result.output

    async def test_search_skips_venv(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """搜索跳过 .venv 目录中的文件。"""
        venv_dir = tmp_path / ".venv" / "Lib"
        venv_dir.mkdir(parents=True)
        (venv_dir / "os.py").write_text("content here\n", encoding="utf-8")

        (tmp_path / "app.py").write_text("content here\n", encoding="utf-8")

        result = await tool_with_root.execute(pattern="content")
        assert result.success is True
        assert "搜索到 1 行匹配" in result.output
        assert ".venv" not in result.output

    async def test_search_timeout(
        self, mocker, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """Python 搜索超时处理。"""
        mocker.patch("asyncio.wait_for", side_effect=TimeoutError("模拟超时"))

        (tmp_path / "test.txt").write_text("content\n", encoding="utf-8")

        result = await tool_with_root.execute(pattern="content")
        assert result.success is False
        assert "超时" in result.output

    async def test_output_paths_are_relative(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """搜索结果中的路径为相对路径，不包含绝对路径。"""
        (tmp_path / "test.py").write_text("target content\n", encoding="utf-8")

        result = await tool_with_root.execute(pattern="target")
        assert result.success is True

        abs_root = str(tmp_path)
        assert abs_root not in result.output, "搜索结果包含绝对路径"

    async def test_invalid_regex(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """无效的正则表达式。"""
        result = await tool_with_root.execute(pattern=r"[invalid")
        assert result.success is False
        assert "正则表达式无效" in result.output

    async def test_empty_pattern(self, tool_with_root: GrepFiles) -> None:
        """空 pattern。"""
        result = await tool_with_root.execute(pattern="")
        assert result.success is False
        assert "非空" in result.output

    async def test_glob_filter_with_no_such_file(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """glob 过滤后没有匹配的文件。"""
        (tmp_path / "data.txt").write_text("content\n", encoding="utf-8")

        result = await tool_with_root.execute(pattern="content", glob="*.py")
        assert result.success is True
        assert "没有找到匹配的行" in result.output


class TestGrepPythonFallbackTruncation:
    """Python fallback 输出截断测试。"""

    @pytest.fixture(autouse=True)
    def _disable_rg(self, mocker) -> None:
        mocker.patch("minicode.tools.grep.shutil.which", return_value=None)

    async def test_exceeds_max_lines(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """匹配行数超过 DEFAULT_MAX_LINES 时被截断。"""
        lines = "\n".join(f"match line {i}" for i in range(DEFAULT_MAX_LINES + 20))
        (tmp_path / "big.txt").write_text(lines, encoding="utf-8")

        result = await tool_with_root.execute(pattern="match")
        assert result.success is True
        assert "结果已截断" in result.output
        assert f"共 {DEFAULT_MAX_LINES + 20} 行匹配" in result.output

    async def test_under_max_lines(
        self, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """匹配行数刚好低于阈值时不截断。"""
        lines = "\n".join(f"line {i}" for i in range(DEFAULT_MAX_LINES - 1))
        (tmp_path / "moderate.txt").write_text(lines, encoding="utf-8")

        result = await tool_with_root.execute(pattern=r"\d+")
        assert result.success is True
        assert "结果已截断" not in result.output


# ============================================================
# rg 路径测试
# 模拟 ripgrep 的行为来测试 rg 路径
# ============================================================


class TestGrepWithRg:
    """ripgrep 路径的功能测试。"""

    async def _make_rg_available(
        self, mocker, returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> AsyncMock:
        """模拟 rg 可用并返回指定结果。"""
        mocker.patch("minicode.tools.grep.shutil.which", return_value="/usr/bin/rg")
        mock_to_thread = mocker.patch(
            "minicode.tools.grep.asyncio.to_thread", new_callable=AsyncMock
        )
        mock_to_thread.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=returncode,
            stdout=stdout.encode("utf-8"),
            stderr=stderr.encode("utf-8"),
        )
        return mock_to_thread

    async def test_rg_basic_search(
        self, mocker, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """rg 基本搜索。"""
        mock_to_thread = await self._make_rg_available(
            mocker,
            returncode=0,
            stdout="hello.py:1:def hello():\nhello.py:2:    pass\n",
        )

        result = await tool_with_root.execute(pattern="hello")
        assert result.success is True
        assert "hello.py" in result.output
        assert "搜索到 2 行匹配" in result.output
        # 验证 rg 命令被正确调用
        cmd = mock_to_thread.call_args[0][1]
        assert "rg" in cmd[0]

    async def test_rg_no_match(
        self, mocker, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """rg 无匹配结果。"""
        await self._make_rg_available(mocker, returncode=1, stdout="")

        result = await tool_with_root.execute(pattern="nonexistent")
        assert result.success is True
        assert "没有找到匹配的行" in result.output

    async def test_rg_with_file_glob(
        self, mocker, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """rg 使用文件 glob 过滤。"""
        mock_to_thread = await self._make_rg_available(
            mocker,
            returncode=0,
            stdout="code.py:1:target = 1\n",
        )

        result = await tool_with_root.execute(pattern="target", glob="*.py")
        assert result.success is True
        # 验证 glob 参数被传递给了 rg
        cmd = mock_to_thread.call_args[0][1]
        assert "--glob" in cmd
        glob_idx = cmd.index("--glob")
        assert cmd[glob_idx + 1] == "*.py"

    async def test_rg_error(
        self, mocker, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """rg 执行出错。"""
        await self._make_rg_available(
            mocker,
            returncode=2,
            stderr="error: invalid pattern\n",
        )

        result = await tool_with_root.execute(pattern=r"[invalid")
        assert result.success is False
        assert "ripgrep 搜索失败" in result.output

    async def test_rg_truncation(
        self, mocker, tool_with_root: GrepFiles, tmp_path: Path
    ) -> None:
        """rg 结果超过限制时截断。"""
        lines = "\n".join(f"file.txt:{i}:match {i}" for i in range(DEFAULT_MAX_LINES + 10))
        await self._make_rg_available(mocker, returncode=0, stdout=lines)

        result = await tool_with_root.execute(pattern="match")
        assert result.success is True
        assert "结果已截断" in result.output


class TestGrepErrors:
    """通用错误场景测试。"""

    async def test_no_workspace_root(self, tool: GrepFiles) -> None:
        """workspace_root 未设置时返回错误。"""
        result = await tool.execute(pattern="test")
        assert result.success is False
        assert "工作区根路径未设置" in result.output

    async def test_pattern_is_none(self, tool_with_root: GrepFiles) -> None:
        """pattern 为 None。"""
        result = await tool_with_root.execute(pattern=None)  # type: ignore[arg-type]
        assert result.success is False
        assert "非空" in result.output

    async def test_pattern_is_whitespace(
        self, tool_with_root: GrepFiles
    ) -> None:
        """pattern 为空白字符串。"""
        result = await tool_with_root.execute(pattern="   ")
        assert result.success is False
        assert "非空" in result.output

    async def test_pattern_is_non_string(
        self, tool_with_root: GrepFiles
    ) -> None:
        """pattern 为非字符串类型。"""
        result = await tool_with_root.execute(pattern=123)  # type: ignore[arg-type]
        assert result.success is False
        assert "非空" in result.output

    async def test_glob_is_non_string(
        self, tool_with_root: GrepFiles, mocker
    ) -> None:
        """glob 参数为非字符串类型。"""
        mocker.patch("minicode.tools.grep.shutil.which", return_value=None)

        result = await tool_with_root.execute(pattern="test", glob=123)  # type: ignore[arg-type]
        assert result.success is False
        assert "必须是字符串" in result.output


class TestGrepIntegration:
    """集成层面的验证测试。"""

    async def test_execute_injects_workspace(
        self, tmp_path: Path, mocker
    ) -> None:
        """通过 ToolRegistry 执行时 workspace_root 被正确注入。"""
        from minicode.tools.registry import ToolRegistry

        mocker.patch("minicode.tools.grep.shutil.which", return_value=None)

        registry = ToolRegistry()
        registry.register(GrepFiles)

        (tmp_path / "test.py").write_text("content here\n", encoding="utf-8")

        result = await registry.execute_tool(
            "grep", {"pattern": "content"}, tmp_path
        )
        assert result.success is True
        assert "test.py" in result.output

    async def test_tool_schema_compatible(self) -> None:
        """工具 schema 可直接用于 OpenAI-compatible API。"""
        tool = GrepFiles()
        schema = tool.get_tool_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "grep"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"

        props = schema["function"]["parameters"]["properties"]
        assert "pattern" in props
        assert props["pattern"]["type"] == "string"
        assert "glob" in props

        required = schema["function"]["parameters"]["required"]
        assert "pattern" in required
        assert "glob" not in required

        assert (
            schema["function"]["parameters"].get("additionalProperties") is False
        )

    async def test_schema_registered_in_registry(self) -> None:
        """工具注册后 schema 通过 registry 正确导出。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(GrepFiles)

        schemas = registry.get_tools_schema()
        names = [s["function"]["name"] for s in schemas]
        assert "grep" in names
