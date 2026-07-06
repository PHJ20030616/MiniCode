"""Glob 文件匹配工具单元测试。

覆盖基本匹配、递归匹配、无匹配、超长截断、错误场景。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.tools.glob import DEFAULT_MAX_LINES, GlobFiles


@pytest.fixture
def tool() -> GlobFiles:
    """创建 GlobFiles 工具实例（不含 workspace_root）。"""
    return GlobFiles()


@pytest.fixture
def tool_with_root(tmp_path: Path) -> GlobFiles:
    """创建已设置 workspace_root 的 GlobFiles 工具实例。"""
    return GlobFiles(workspace_root=tmp_path)


class TestGlobSuccess:
    """成功匹配的各种场景。"""

    async def test_basic_glob_py(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """基本 glob 模式匹配 .py 文件。"""
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "b.py").write_text("", encoding="utf-8")
        (tmp_path / "c.txt").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="*.py")
        assert result.success is True
        assert "a.py" in result.output
        assert "b.py" in result.output
        assert "c.txt" not in result.output
        assert "匹配到 2 个路径" in result.output

    async def test_recursive_glob(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """递归 glob 模式匹配所有子目录中的文件。"""
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.py").write_text("", encoding="utf-8")
        (sub / "c.txt").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="**/*.py")
        assert result.success is True
        assert "a.py" in result.output
        assert str(Path("sub/b.py")) in result.output
        assert str(Path("sub/c.txt")) not in result.output
        assert "匹配到 2 个路径" in result.output

    async def test_glob_all_files(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """匹配所有文件。"""
        (tmp_path / "a.txt").write_text("", encoding="utf-8")
        (tmp_path / "b.md").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="*")
        assert result.success is True
        assert "a.txt" in result.output
        assert "b.md" in result.output
        # 注意：* 会匹配当前目录下的所有文件和目录
        assert "匹配到" in result.output

    async def test_glob_nested_directories(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """匹配嵌套目录中的文件。"""
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        (nested / "file.txt").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="**/file.txt")
        assert result.success is True
        assert str(Path("a/b/c/file.txt")) in result.output

    async def test_glob_sorted_output(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """输出按路径排序。"""
        (tmp_path / "z.py").write_text("", encoding="utf-8")
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "m.py").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="*.py")
        assert result.success is True

        # 提取路径列表
        body = result.output.split("：\n\n", 1)[1]
        paths = [p for p in body.split("\n") if p.strip() and not p.startswith("...")]
        assert paths == sorted(paths)

    async def test_glob_with_directory_in_results(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """glob 模式可以匹配到目录。"""
        sub = tmp_path / "mydir"
        sub.mkdir()
        (sub / "f.txt").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="*")
        assert result.success is True
        assert "mydir" in result.output

    async def test_path_traversal(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """glob ../ 模式不会泄露工作区外的文件路径。"""
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="../*")
        assert result.success is True
        assert "outside.txt" not in result.output
        # 输出中不应该包含 parent 目录引用
        for line in result.output.split("\n"):
            line = line.strip()
            if not line or line.startswith(("没有匹配到", "匹配到", "...")):
                continue
            assert ".." not in line, f"输出包含 parent 目录引用：{line}"

    async def test_output_relative_paths(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """输出中的路径均为相对路径，不泄露绝对路径。"""
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="**/*.py")
        assert result.success is True

        abs_root = str(tmp_path)
        assert abs_root not in result.output, "输出包含绝对路径"


class TestGlobNoMatch:
    """无匹配场景。"""

    async def test_no_match(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """没有任何匹配的文件。"""
        result = await tool_with_root.execute(pattern="*.xyz")
        assert result.success is True
        assert "没有匹配到路径" in result.output

    async def test_empty_workspace(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """空工作区。"""
        result = await tool_with_root.execute(pattern="**/*")
        # 空工作区中 glob 可能匹配不到任何内容
        assert result.success is True
        assert "没有匹配到路径" in result.output or "匹配到" in result.output


class TestGlobTruncation:
    """超长输出截断测试。"""

    async def test_exceeds_max_lines(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """匹配结果超过 DEFAULT_MAX_LINES 时被截断。"""
        for i in range(DEFAULT_MAX_LINES + 20):
            (tmp_path / f"file_{i:04d}.txt").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="*.txt")
        assert result.success is True
        assert "结果已截断" in result.output
        assert f"共 {DEFAULT_MAX_LINES + 20} 个路径" in result.output
        assert f"仅显示前 {DEFAULT_MAX_LINES} 个" in result.output

    async def test_under_max_lines(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """匹配结果刚好低于阈值时不截断。"""
        for i in range(DEFAULT_MAX_LINES - 1):
            (tmp_path / f"file_{i:04d}.txt").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="*.txt")
        assert result.success is True
        assert "结果已截断" not in result.output
        assert f"匹配到 {DEFAULT_MAX_LINES - 1} 个路径" in result.output

    async def test_exactly_max_lines(
        self, tool_with_root: GlobFiles, tmp_path: Path
    ) -> None:
        """匹配结果刚好等于阈值时不截断。"""
        for i in range(DEFAULT_MAX_LINES):
            (tmp_path / f"file_{i:04d}.txt").write_text("", encoding="utf-8")

        result = await tool_with_root.execute(pattern="*.txt")
        assert result.success is True
        assert "结果已截断" not in result.output
        assert f"匹配到 {DEFAULT_MAX_LINES} 个路径" in result.output


class TestGlobErrors:
    """各种错误场景测试。"""

    async def test_no_workspace_root(self, tool: GlobFiles) -> None:
        """workspace_root 未设置时返回错误。"""
        result = await tool.execute(pattern="*.py")
        assert result.success is False
        assert "工作区根路径未设置" in result.output

    async def test_empty_pattern(self, tool_with_root: GlobFiles) -> None:
        """pattern 为空字符串。"""
        result = await tool_with_root.execute(pattern="")
        assert result.success is False
        assert "非空" in result.output

    async def test_pattern_is_none(
        self, tool_with_root: GlobFiles
    ) -> None:
        """pattern 为 None。"""
        result = await tool_with_root.execute(pattern=None)  # type: ignore[arg-type]
        assert result.success is False
        assert "非空" in result.output

    async def test_pattern_is_whitespace(
        self, tool_with_root: GlobFiles
    ) -> None:
        """pattern 为空白字符串。"""
        result = await tool_with_root.execute(pattern="   ")
        assert result.success is False
        assert "非空" in result.output

    async def test_pattern_is_non_string(
        self, tool_with_root: GlobFiles
    ) -> None:
        """pattern 为非字符串类型。"""
        result = await tool_with_root.execute(pattern=123)  # type: ignore[arg-type]
        assert result.success is False
        assert "非空" in result.output


class TestGlobIntegration:
    """集成层面的验证测试。"""

    async def test_execute_injects_workspace(self, tmp_path: Path) -> None:
        """通过 ToolRegistry 执行时 workspace_root 被正确注入。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(GlobFiles)

        (tmp_path / "test.py").write_text("", encoding="utf-8")

        result = await registry.execute_tool(
            "glob", {"pattern": "*.py"}, tmp_path
        )
        assert result.success is True
        assert "test.py" in result.output

    async def test_tool_schema_compatible(self) -> None:
        """工具 schema 可直接用于 OpenAI-compatible API。"""
        tool = GlobFiles()
        schema = tool.get_tool_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "glob"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"

        props = schema["function"]["parameters"]["properties"]
        assert "pattern" in props
        assert props["pattern"]["type"] == "string"

        required = schema["function"]["parameters"]["required"]
        assert "pattern" in required

        assert (
            schema["function"]["parameters"].get("additionalProperties") is False
        )

    async def test_schema_registered_in_registry(self) -> None:
        """工具注册后 schema 通过 registry 正确导出。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(GlobFiles)

        schemas = registry.get_tools_schema()
        names = [s["function"]["name"] for s in schemas]
        assert "glob" in names
