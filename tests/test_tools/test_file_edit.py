"""文件精确编辑工具单元测试。

覆盖单次替换、全局替换、无匹配、不唯一匹配、边界场景、
换行符保留（LF/CRLF）、重叠/非重叠语义和参数校验。
所有文件创建使用 write_bytes，断言使用 read_bytes 以保证平台无关性。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.tools.file_edit import EditFile


@pytest.fixture
def tool() -> EditFile:
    """创建 EditFile 工具实例（不含 workspace_root）。"""
    return EditFile()


@pytest.fixture
def tool_with_root(tmp_path: Path) -> EditFile:
    """创建已设置 workspace_root 的 EditFile 工具实例。"""
    return EditFile(workspace_root=tmp_path)


class TestEditFileOverlappingBase:
    """非重叠匹配的基础行为验证（驱动统一匹配语义）。"""

    async def test_boundary_aaa_with_aa(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """"aaa" 中匹配 "aa"：非重叠语义下仅 1 处匹配（位置 0），单次替换成功。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"aaa")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="aa",
            new_string="x",
        )
        assert result.success is True
        assert target.read_bytes() == b"xa"

    async def test_boundary_aaaa_with_aa_not_unique(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """"aaaa" 中匹配 "aa"：非重叠语义下 2 处匹配（位置 0 和 2），单次替换拒绝。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"aaaa")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="aa",
            new_string="x",
        )
        assert result.success is False
        assert "不唯一" in result.output
        assert "2 处匹配" in result.output

    async def test_boundary_aaaa_with_aa_replace_all(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """"aaaa" 中替换所有 "aa"：非重叠语义下替换 2 处。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"aaaa")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="aa",
            new_string="x",
            replace_all=True,
        )
        assert result.success is True
        assert "替换次数：2" in result.output
        assert target.read_bytes() == b"xx"


class TestEditFileSingleReplace:
    """单次替换场景（replace_all=false，默认）。"""

    async def test_single_replace_inline(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """替换行中的部分文本。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello world")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="world",
            new_string="there",
        )
        assert result.success is True
        assert "---" in result.output
        assert "+++" in result.output
        assert "@@ " in result.output
        assert target.read_bytes() == b"hello there"

    async def test_single_replace_full_line(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """替换完整行内容（文件中有一行匹配）。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"line1\nhello\nline3\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="hello",
            new_string="HELLO",
        )
        assert result.success is True
        assert target.read_bytes() == b"line1\nHELLO\nline3\n"

    async def test_replace_at_beginning(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """替换文件开头的内容。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"ab cd ef")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="ab",
            new_string="XY",
        )
        assert result.success is True
        assert target.read_bytes() == b"XY cd ef"

    async def test_replace_at_end(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """替换文件末尾的内容。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"ab cd ef")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="ef",
            new_string="YZ",
        )
        assert result.success is True
        assert target.read_bytes() == b"ab cd YZ"

    async def test_replace_with_longer(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """替换为更长的文本。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"short")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="short",
            new_string="much longer text",
        )
        assert result.success is True
        assert target.read_bytes() == b"much longer text"

    async def test_delete_text(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """替换为空字符串（删除匹配文本）。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello world foo")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string=" world",
            new_string="",
        )
        assert result.success is True
        assert target.read_bytes() == b"hello foo"


class TestEditFileReplaceAll:
    """全局替换场景（replace_all=true）。"""

    async def test_replace_all_three_matches(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """全局替换 3 处匹配。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"a,b,a,c,a")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="a",
            new_string="X",
            replace_all=True,
        )
        assert result.success is True
        assert "替换次数：3" in result.output
        assert target.read_bytes() == b"X,b,X,c,X"

    async def test_replace_all_one_match(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """全局替换仅 1 处匹配（与单次替换等效）。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello world")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="world",
            new_string="there",
            replace_all=True,
        )
        assert result.success is True
        assert "替换次数：1" in result.output
        assert target.read_bytes() == b"hello there"

    async def test_replace_all_special_chars(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """全局替换包含特殊字符的文本。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"foo\tbar\tbaz\tqux")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="\t",
            new_string=" ",
            replace_all=True,
        )
        assert result.success is True
        assert "替换次数：3" in result.output
        assert target.read_bytes() == b"foo bar baz qux"

    async def test_replace_all_multiline(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """全局替换跨行文本。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"X\nY\nX\nY\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="X\nY",
            new_string="A\nB",
            replace_all=True,
        )
        assert result.success is True
        assert "替换次数：2" in result.output
        assert target.read_bytes() == b"A\nB\nA\nB\n"


class TestEditFileNoMatch:
    """匹配不到的场景。"""

    async def test_no_match_single(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """单次替换模式，未找到匹配文本。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello world")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="xyz",
            new_string="replacement",
        )
        assert result.success is False
        assert "未找到要替换的文本" in result.output

    async def test_no_match_all(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """全局替换模式，未找到匹配文本。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello world")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="xyz",
            new_string="replacement",
            replace_all=True,
        )
        assert result.success is False
        assert "未找到要替换的文本" in result.output

    async def test_partial_match_failure(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """文件包含部分匹配但非精确匹配。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"bar foo baz")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="foobar",
            new_string="FOOBAR",
        )
        assert result.success is False
        assert "未找到要替换的文本" in result.output

    async def test_empty_file(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """空文件中无法匹配任何文本。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="anything",
            new_string="new",
        )
        assert result.success is False
        assert "未找到要替换的文本" in result.output


class TestEditFileNotUnique:
    """old_string 不唯一的场景。"""

    async def test_not_unique_two_matches(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """2 处匹配，分布在两行。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"foo bar\nhello foo end\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="foo",
            new_string="FOO",
        )
        assert result.success is False
        assert "不唯一" in result.output
        assert "2 处匹配" in result.output
        assert "第 1 行" in result.output
        assert "第 2 行" in result.output

    async def test_not_unique_five_matches(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """5 处匹配，分布在五行。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"a\na\na\na\na\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="a",
            new_string="A",
        )
        assert result.success is False
        assert "不唯一" in result.output
        assert "5 处匹配" in result.output
        assert "第 1 行" in result.output
        assert "第 5 行" in result.output

    async def test_not_unique_same_line(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """同一行内有 2 处匹配。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"foo bar foo end")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="foo",
            new_string="FOO",
        )
        assert result.success is False
        assert "不唯一" in result.output
        assert "2 处匹配" in result.output

    async def test_overlapping_not_unique(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """非重叠语义下 "aaaa" 中 "aa" 有 2 处匹配，单次替换被拒绝。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"aaaa")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="aa",
            new_string="x",
        )
        assert result.success is False
        assert "不唯一" in result.output
        assert "2 处匹配" in result.output


class TestEditFileEdgeCases:
    """边界场景。"""

    async def test_chinese_characters(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """替换中文文本。"""
        target = tmp_path / "test.txt"
        target.write_bytes("你好世界，这是测试文件".encode())

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="世界",
            new_string="MiniCode",
        )
        assert result.success is True
        assert target.read_bytes() == "你好MiniCode，这是测试文件".encode()

    async def test_emoji(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """替换包含 Emoji 的文本。"""
        target = tmp_path / "test.txt"
        target.write_bytes("hello 🔥 world".encode())

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="🔥",
            new_string="✨",
        )
        assert result.success is True
        assert target.read_bytes() == "hello ✨ world".encode()

    async def test_newline_matching(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """跨行文本匹配与替换（LF 文件）。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"line1\nline2\nline3\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="line1\nline2",
            new_string="changed",
        )
        assert result.success is True
        assert target.read_bytes() == b"changed\nline3\n"

    async def test_no_op(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """old_string 等于 new_string（内容不变）。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello world")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="hello",
            new_string="hello",
        )
        assert result.success is True
        assert "替换次数：1" in result.output
        assert target.read_bytes() == b"hello world"

    async def test_file_not_exist(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """编辑不存在的文件。"""
        result = await tool_with_root.execute(
            file_path="nonexistent.txt",
            old_string="old",
            new_string="new",
        )
        assert result.success is False
        assert "文件不存在" in result.output

    async def test_target_is_directory(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """目标是目录而非文件。"""
        (tmp_path / "mydir").mkdir()

        result = await tool_with_root.execute(
            file_path="mydir",
            old_string="old",
            new_string="new",
        )
        assert result.success is False
        assert "目录" in result.output

    async def test_empty_old_string(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """old_string 为空字符串时应被拒绝。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="",
            new_string="new",
        )
        assert result.success is False
        assert "不能为空" in result.output


class TestEditFileLineEndings:
    """换行符保留场景。"""

    async def test_preserve_lf(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """LF 文件编辑后仍是 LF。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello\nworld\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="world",
            new_string="there",
        )
        assert result.success is True
        assert target.read_bytes() == b"hello\nthere\n"

    async def test_preserve_crlf(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """CRLF 文件编辑后仍是 CRLF。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello\r\nworld\r\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="world",
            new_string="there",
        )
        assert result.success is True
        assert target.read_bytes() == b"hello\r\nthere\r\n"

    async def test_crlf_multiline_match(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """CRLF 文件中跨行匹配（old_string 含 \\r\\n 才能匹配）。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"line1\r\nline2\r\nline3\r\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="line1\r\nline2",
            new_string="CHANGED",
        )
        assert result.success is True
        assert target.read_bytes() == b"CHANGED\r\nline3\r\n"

    async def test_lf_replace_all_preserves_line_endings(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """LF 文件全局替换后仍是 LF。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"X\nY\nX\nY\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="X",
            new_string="A",
            replace_all=True,
        )
        assert result.success is True
        assert target.read_bytes() == b"A\nY\nA\nY\n"

    async def test_crlf_replace_all_preserves_line_endings(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """CRLF 文件全局替换后仍是 CRLF。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"X\r\nY\r\nX\r\nY\r\n")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="X",
            new_string="A",
            replace_all=True,
        )
        assert result.success is True
        assert target.read_bytes() == b"A\r\nY\r\nA\r\nY\r\n"


class TestEditFileParameterValidation:
    """参数校验场景。"""

    async def test_missing_file_path(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """file_path 参数缺失。"""
        result = await tool_with_root.execute(
            old_string="old",
            new_string="new",
        )
        assert result.success is False

    async def test_file_path_empty(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """file_path 为空字符串。"""
        result = await tool_with_root.execute(
            file_path="",
            old_string="old",
            new_string="new",
        )
        assert result.success is False

    async def test_file_path_whitespace(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """file_path 为纯空白。"""
        result = await tool_with_root.execute(
            file_path="   ",
            old_string="old",
            new_string="new",
        )
        assert result.success is False

    async def test_old_string_none(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """old_string 为 None。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string=None,  # type: ignore[arg-type]
            new_string="new",
        )
        assert result.success is False

    async def test_old_string_empty(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """old_string 为空字符串。"""
        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="",
            new_string="new",
        )
        assert result.success is False

    async def test_new_string_missing(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """new_string 参数缺失。"""
        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="old",
        )
        assert result.success is False

    async def test_replace_all_non_bool(
        self, tool_with_root: EditFile, tmp_path: Path
    ) -> None:
        """replace_all 为非布尔值。"""
        target = tmp_path / "test.txt"
        target.write_bytes(b"hello")

        result = await tool_with_root.execute(
            file_path="test.txt",
            old_string="hello",
            new_string="world",
            replace_all="yes",  # type: ignore[arg-type]
        )
        assert result.success is False
        assert "布尔值" in result.output

    async def test_no_workspace_root(self, tool: EditFile) -> None:
        """workspace_root 未设置时返回错误。"""
        result = await tool.execute(
            file_path="any.txt",
            old_string="old",
            new_string="new",
        )
        assert result.success is False
        assert "工作区根路径未设置" in result.output


class TestEditFileIntegration:
    """集成验证。"""

    async def test_execute_injects_workspace(self, tmp_path: Path) -> None:
        """通过 ToolRegistry 执行时 workspace_root 被正确注入。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(EditFile)

        target = tmp_path / "test.txt"
        target.write_bytes(b"hello world")

        result = await registry.execute_tool(
            "edit_file",
            {"file_path": "test.txt", "old_string": "world", "new_string": "there"},
            tmp_path,
        )
        assert result.success is True
        assert target.read_bytes() == b"hello there"

    async def test_tool_schema_compatible(self) -> None:
        """工具 schema 符合 OpenAI function calling 格式。"""
        tool = EditFile()
        schema = tool.get_tool_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "edit_file"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"

        props = schema["function"]["parameters"]["properties"]
        assert "file_path" in props
        assert props["file_path"]["type"] == "string"
        assert "old_string" in props
        assert props["old_string"]["type"] == "string"
        assert "new_string" in props
        assert props["new_string"]["type"] == "string"
        assert "replace_all" in props
        assert props["replace_all"]["type"] == "boolean"

        required = schema["function"]["parameters"]["required"]
        assert "file_path" in required
        assert "old_string" in required
        assert "new_string" in required

        assert schema["function"]["parameters"].get("additionalProperties") is False

    async def test_schema_registered_in_registry(self) -> None:
        """注册后 schema 通过 registry 正确导出。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(EditFile)

        schemas = registry.get_tools_schema()
        names = [s["function"]["name"] for s in schemas]
        assert "edit_file" in names
