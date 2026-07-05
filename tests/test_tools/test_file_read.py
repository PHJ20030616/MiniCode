"""文件读取工具单元测试。

覆盖成功读取、不存在、目录、越界行范围、超长截断、敏感文件拒绝等场景。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.tools.file_read import DEFAULT_MAX_CHARS, ReadFile


@pytest.fixture
def tool() -> ReadFile:
    """创建 ReadFile 工具实例（不含 workspace_root）。"""
    return ReadFile()


@pytest.fixture
def tool_with_root(tmp_path: Path) -> ReadFile:
    """创建已设置 workspace_root 的 ReadFile 工具实例。"""
    return ReadFile(workspace_root=tmp_path)


class TestReadFileSuccess:
    """成功读取文件的各种场景。"""

    async def test_read_small_text_file(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """读取一个小型文本文件。"""
        target = tmp_path / "hello.txt"
        target.write_text("Hello, World!\n第二行\n第三行\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path="hello.txt")
        assert result.success is True
        assert "Hello, World!" in result.output
        assert "第二行" in result.output
        assert "第三行" in result.output
        assert "共 3 行" in result.output

    async def test_read_with_relative_path(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """使用相对路径读取子目录中的文件。"""
        sub = tmp_path / "subdir"
        sub.mkdir()
        target = sub / "nested.txt"
        target.write_text("nested content\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path="subdir/nested.txt")
        assert result.success is True
        assert "nested content" in result.output

    async def test_read_with_absolute_path(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """使用绝对路径读取文件。"""
        target = tmp_path / "abs.txt"
        target.write_text("absolute path test\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path=str(target))
        assert result.success is True
        assert "absolute path test" in result.output

    async def test_read_empty_file(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """读取空文件。"""
        target = tmp_path / "empty.txt"
        target.write_text("", encoding="utf-8")

        result = await tool_with_root.execute(file_path="empty.txt")
        assert result.success is True
        assert "共 0 行" in result.output

    async def test_read_single_line_file(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """读取只有一行的文件（不含换行符）。"""
        target = tmp_path / "single.txt"
        target.write_text("just one line", encoding="utf-8")

        result = await tool_with_root.execute(file_path="single.txt")
        assert result.success is True
        assert "just one line" in result.output
        assert "共 1 行" in result.output

    async def test_read_file_with_chinese(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """读取包含中文的文件。"""
        target = tmp_path / "chinese.txt"
        target.write_text("你好，世界！\n这是中文测试文件。\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path="chinese.txt")
        assert result.success is True
        assert "你好" in result.output
        assert "中文测试" in result.output


class TestReadFileWithOffsetAndLimit:
    """行范围选择相关测试。"""

    async def _make_ten_lines(self, tmp_path: Path, name: str = "lines.txt") -> Path:
        """创建含 10 行内容的测试文件并返回路径。"""
        target = tmp_path / name
        lines = "".join(f"第{i}行\n" for i in range(10))
        target.write_text(lines, encoding="utf-8")
        return target

    async def test_read_with_offset(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """从指定行开始读取。"""
        await self._make_ten_lines(tmp_path)

        result = await tool_with_root.execute(file_path="lines.txt", offset=5)
        assert result.success is True
        assert "第5行" in result.output
        assert "第9行" in result.output
        assert "第0行" not in result.output
        assert "第4行" not in result.output

    async def test_read_with_limit(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """限制读取行数。"""
        await self._make_ten_lines(tmp_path)

        result = await tool_with_root.execute(file_path="lines.txt", limit=3)
        assert result.success is True
        assert "第0行" in result.output
        assert "第1行" in result.output
        assert "第2行" in result.output
        assert "第3行" not in result.output

    async def test_read_with_offset_and_limit(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """同时指定 offset 和 limit。"""
        await self._make_ten_lines(tmp_path)

        result = await tool_with_root.execute(
            file_path="lines.txt", offset=3, limit=4
        )
        assert result.success is True
        assert "第3行" in result.output
        assert "第4行" in result.output
        assert "第5行" in result.output
        assert "第6行" in result.output
        assert "第2行" not in result.output
        assert "第7行" not in result.output

    async def test_read_offset_zero(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """offset=0 等价于从头读取。"""
        target = tmp_path / "start.txt"
        target.write_text("第一行\n第二行\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path="start.txt", offset=0)
        assert result.success is True
        assert "第一行" in result.output

    async def test_read_limit_zero(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """limit=0 时返回空内容（但文件元信息仍在）。"""
        target = tmp_path / "empty_limit.txt"
        target.write_text("第一行\n第二行\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path="empty_limit.txt", limit=0)
        assert result.success is True
        assert "共 2 行" in result.output
        assert "显示第 0-0 行" in result.output

    async def test_read_offset_beyond_file(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """offset 超出文件总行数时返回提示信息。"""
        target = tmp_path / "short.txt"
        target.write_text("仅有一行\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path="short.txt", offset=10)
        assert result.success is True
        assert "超出范围" in result.output

    async def test_read_offset_equal_file_length(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """offset 等于文件总行数时返回提示信息。"""
        target = tmp_path / "two_lines.txt"
        target.write_text("第一行\n第二行\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path="two_lines.txt", offset=2)
        assert result.success is True
        assert "超出范围" in result.output

    async def test_read_limit_exceeds_file(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """limit 超出文件剩余行数时自动截断到文件末尾。"""
        lines = "".join(f"第{i}行\n" for i in range(5))
        target = tmp_path / "small.txt"
        target.write_text(lines, encoding="utf-8")

        result = await tool_with_root.execute(file_path="small.txt", limit=100)
        assert result.success is True
        assert "第4行" in result.output
        assert "共 5 行" in result.output
        assert "显示第 0-5 行" in result.output


class TestReadFileTruncation:
    """超长输出截断测试。"""

    async def test_truncation_long_content(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """内容超过 DEFAULT_MAX_CHARS 时被截断。"""
        target = tmp_path / "long.txt"
        content = "A" * (DEFAULT_MAX_CHARS + 1000)
        target.write_text(content, encoding="utf-8")

        result = await tool_with_root.execute(file_path="long.txt")
        assert result.success is True
        assert "输出已截断" in result.output

        # 验证截断后的内容正好为 DEFAULT_MAX_CHARS
        body_start = result.output.find("\n\n")
        assert body_start != -1
        trunc_msg = result.output.find("\n...（输出已截断")
        assert trunc_msg != -1
        body = result.output[body_start + 2 : trunc_msg]
        assert len(body) == DEFAULT_MAX_CHARS

    async def test_truncation_boundary(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """内容恰好等于 DEFAULT_MAX_CHARS 时不截断。"""
        target = tmp_path / "boundary.txt"
        target.write_text("A" * DEFAULT_MAX_CHARS, encoding="utf-8")

        result = await tool_with_root.execute(file_path="boundary.txt")
        assert result.success is True
        assert "输出已截断" not in result.output

    async def test_truncation_just_under(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """内容刚好低于 DEFAULT_MAX_CHARS 时不截断。"""
        target = tmp_path / "almost.txt"
        target.write_text("B" * (DEFAULT_MAX_CHARS - 1), encoding="utf-8")

        result = await tool_with_root.execute(file_path="almost.txt")
        assert result.success is True
        assert "输出已截断" not in result.output


class TestReadFileErrors:
    """各种错误场景测试。"""

    async def test_read_nonexistent_file(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """读取不存在的文件。"""
        result = await tool_with_root.execute(file_path="nonexistent.txt")
        assert result.success is False
        assert "文件不存在" in result.output

    async def test_read_directory(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """尝试读取目录。"""
        sub = tmp_path / "mydir"
        sub.mkdir()

        result = await tool_with_root.execute(file_path="mydir")
        assert result.success is False
        assert "目录" in result.output

    async def test_read_sensitive_file_env(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """读取 .env 敏感文件被拒绝。"""
        target = tmp_path / ".env"
        target.write_text("SECRET=123", encoding="utf-8")

        result = await tool_with_root.execute(file_path=".env")
        assert result.success is False
        assert "敏感文件" in result.output

    async def test_read_sensitive_file_in_subdirectory(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """读取子目录中的敏感文件被拒绝。"""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        key = ssh_dir / "id_rsa"
        key.write_text("private key", encoding="utf-8")

        result = await tool_with_root.execute(file_path=".ssh/id_rsa")
        assert result.success is False
        assert "敏感文件" in result.output

    async def test_read_outside_workspace(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """读取工作区外的文件被拒绝。"""
        result = await tool_with_root.execute(
            file_path=str(tmp_path.parent / "outside.txt")
        )
        assert result.success is False
        assert "路径越界" in result.output

    async def test_read_parent_path_escape(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """使用 ../ 逃逸工作区被拒绝。"""
        result = await tool_with_root.execute(file_path="../outside.txt")
        assert result.success is False
        assert "路径越界" in result.output

    async def test_read_empty_file_path(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """file_path 为空字符串。"""
        result = await tool_with_root.execute(file_path="")
        assert result.success is False
        assert "不能为空" in result.output or "非空" in result.output

    async def test_read_negative_offset(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """offset 为负数。"""
        result = await tool_with_root.execute(file_path="some.txt", offset=-1)
        assert result.success is False
        assert "不能为负数" in result.output

    async def test_read_negative_limit(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """limit 为负数。"""
        result = await tool_with_root.execute(file_path="some.txt", limit=-5)
        assert result.success is False
        assert "不能为负数" in result.output

    async def test_read_no_workspace_root(self, tool: ReadFile) -> None:
        """workspace_root 未设置时返回错误。"""
        result = await tool.execute(file_path="any.txt")
        assert result.success is False
        assert "工作区根路径未设置" in result.output

    async def test_read_binary_file(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """读取二进制文件返回错误（UTF-8 解码失败）。"""
        target = tmp_path / "binary.bin"
        target.write_bytes(bytes(range(256)))

        result = await tool_with_root.execute(file_path="binary.bin")
        assert result.success is False
        assert "二进制文件" in result.output

    async def test_read_file_with_nul_bytes(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """包含 NUL 字节的文件被检测为二进制。"""
        target = tmp_path / "nul_file.bin"
        target.write_bytes(b"hello\x00world")

        result = await tool_with_root.execute(file_path="nul_file.bin")
        assert result.success is False
        assert "NUL" in result.output or "二进制" in result.output

    async def test_read_file_with_excessive_control_chars(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """包含大量控制字符的文件被检测为可能为二进制。"""
        target = tmp_path / "ctrl_heavy.bin"
        # 构造一个大部分是控制字符（不含空白类控制符）的文件
        # 范围 1-31 排除 9(\t)、10(\n)、13(\r)，共 28 个控制字符
        # 再加上少量可打印字符，使控制字符占比 > 30%
        control_part = "".join(chr(i) for i in range(1, 32))
        text_part = "x" * 10
        target.write_text(control_part + text_part, encoding="utf-8")

        result = await tool_with_root.execute(file_path="ctrl_heavy.bin")
        assert result.success is False
        assert "控制字符" in result.output or "二进制" in result.output


class TestReadFileParameterValidation:
    """参数校验相关测试。"""

    async def test_file_path_is_none(
        self, tool_with_root: ReadFile
    ) -> None:
        """file_path 为 None。"""
        result = await tool_with_root.execute(file_path=None)  # type: ignore[arg-type]
        assert result.success is False
        assert "非空" in result.output

    async def test_file_path_is_whitespace(
        self, tool_with_root: ReadFile
    ) -> None:
        """file_path 为空白字符串。"""
        result = await tool_with_root.execute(file_path="   ")
        assert result.success is False
        assert "非空" in result.output

    async def test_file_path_is_non_string(
        self, tool_with_root: ReadFile
    ) -> None:
        """file_path 为非字符串类型。"""
        result = await tool_with_root.execute(file_path=123)  # type: ignore[arg-type]
        assert result.success is False
        assert "非空" in result.output

    async def test_offset_is_float(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """offset 为浮点数。"""
        target = tmp_path / "f.txt"
        target.write_text("content\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path="f.txt", offset=1.5)
        assert result.success is False

    async def test_offset_is_string(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """offset 为字符串。"""
        result = await tool_with_root.execute(file_path="x.txt", offset="abc")
        assert result.success is False

    async def test_offset_is_bool(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """offset 为布尔值。"""
        target = tmp_path / "b.txt"
        target.write_text("content\n", encoding="utf-8")

        result = await tool_with_root.execute(file_path="b.txt", offset=True)
        assert result.success is False

    async def test_limit_is_float(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """limit 为浮点数。"""
        result = await tool_with_root.execute(file_path="x.txt", limit=2.5)
        assert result.success is False

    async def test_limit_is_bool(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """limit 为布尔值。"""
        result = await tool_with_root.execute(file_path="x.txt", limit=True)
        assert result.success is False

    async def test_offset_is_none(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """offset 显式传入 None 应报错。"""
        result = await tool_with_root.execute(file_path="x.txt", offset=None)
        assert result.success is False

    async def test_limit_is_none(
        self, tool_with_root: ReadFile, tmp_path: Path
    ) -> None:
        """limit 显式传入 None 应报错。"""
        result = await tool_with_root.execute(file_path="x.txt", limit=None)
        assert result.success is False


class TestReadFileIntegration:
    """集成层面的验证测试。"""

    async def test_execute_injects_workspace(self, tmp_path: Path) -> None:
        """通过 ToolRegistry 执行时 workspace_root 被正确注入。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(ReadFile)

        target = tmp_path / "injected.txt"
        target.write_text("injected workspace test\n", encoding="utf-8")

        result = await registry.execute_tool(
            "read_file", {"file_path": str(target)}, tmp_path
        )
        assert result.success is True
        assert "injected workspace" in result.output

    async def test_tool_schema_compatible(self) -> None:
        """工具 schema 可直接用于 OpenAI-compatible API。"""
        tool = ReadFile()
        schema = tool.get_tool_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "read_file"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"

        props = schema["function"]["parameters"]["properties"]
        assert "file_path" in props
        assert props["file_path"]["type"] == "string"

        required = schema["function"]["parameters"]["required"]
        assert "file_path" in required

        assert schema["function"]["parameters"]["properties"]["offset"]["type"] == "integer"
        assert schema["function"]["parameters"]["properties"]["offset"]["default"] == 0
        assert schema["function"]["parameters"]["properties"]["offset"]["minimum"] == 0

        assert schema["function"]["parameters"]["properties"]["limit"]["type"] == "integer"
        assert schema["function"]["parameters"]["properties"]["limit"]["minimum"] == 0

        assert schema["function"]["parameters"].get("additionalProperties") is False

    async def test_schema_registered_in_registry(self) -> None:
        """工具注册后 schema 通过 registry 正确导出。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(ReadFile)

        schemas = registry.get_tools_schema()
        names = [s["function"]["name"] for s in schemas]
        assert "read_file" in names

    async def test_create_default_registry(self) -> None:
        """create_default_registry 返回包含 read_file 的注册器。"""
        from minicode.tools import create_default_registry

        registry = create_default_registry()
        assert registry.has_tool("read_file") is True

    async def test_register_builtin_tools(self) -> None:
        """register_builtin_tools 将内置工具注册到已有注册器。"""
        from minicode.tools import register_builtin_tools
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        register_builtin_tools(registry)
        assert registry.has_tool("read_file") is True
