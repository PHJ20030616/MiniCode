"""文件写入工具单元测试。

覆盖创建新文件、覆盖写入、追加写入、参数校验、错误场景和集成验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minicode.tools.file_write import WriteFile


@pytest.fixture
def tool() -> WriteFile:
    """创建 WriteFile 工具实例（不含 workspace_root）。"""
    return WriteFile()


@pytest.fixture
def tool_with_root(tmp_path: Path) -> WriteFile:
    """创建已设置 workspace_root 的 WriteFile 工具实例。"""
    return WriteFile(workspace_root=tmp_path)


class TestWriteFileCreate:
    """创建新文件场景。"""

    async def test_create_new_file_relative_path(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """相对路径创建新文件。"""
        result = await tool_with_root.execute(file_path="hello.txt", content="Hello, World!")
        assert result.success is True
        assert (tmp_path / "hello.txt").exists()
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "Hello, World!"

    async def test_create_new_file_absolute_path(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """绝对路径创建新文件。"""
        target = tmp_path / "abs.txt"
        result = await tool_with_root.execute(file_path=str(target), content="absolute path")
        assert result.success is True
        assert target.read_text(encoding="utf-8") == "absolute path"

    async def test_create_file_in_subdirectory(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """子目录中创建文件（验证父目录自动创建）。"""
        result = await tool_with_root.execute(
            file_path="subdir/nested.txt",
            content="nested content",
        )
        assert result.success is True
        assert (tmp_path / "subdir" / "nested.txt").read_text(encoding="utf-8") == "nested content"

    async def test_create_deeply_nested_file(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """多层嵌套父目录自动创建。"""
        result = await tool_with_root.execute(
            file_path="a/b/c/d/e/deep.txt",
            content="deep",
        )
        assert result.success is True
        target = tmp_path / "a" / "b" / "c" / "d" / "e" / "deep.txt"
        assert target.read_text(encoding="utf-8") == "deep"

    async def test_create_file_with_chinese_content(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """写入中文内容。"""
        content = "你好，世界！\n这是中文测试文件。\n"
        result = await tool_with_root.execute(file_path="chinese.txt", content=content)
        assert result.success is True
        assert (tmp_path / "chinese.txt").read_text(encoding="utf-8") == content

    async def test_create_file_with_multiline_content(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """写入多行内容（验证换行符保留）。"""
        content = "line1\nline2\nline3\n"
        result = await tool_with_root.execute(file_path="multiline.txt", content=content)
        assert result.success is True
        assert (tmp_path / "multiline.txt").read_text(encoding="utf-8") == content

    async def test_create_file_with_special_characters(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """写入特殊字符（\\t、\\\\、引号等）。"""
        content = "tab\there\nbackslash\\here\nquote\"here\n"
        result = await tool_with_root.execute(file_path="special.txt", content=content)
        assert result.success is True
        assert (tmp_path / "special.txt").read_text(encoding="utf-8") == content

    async def test_create_empty_file(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """写入空内容（content=""）。"""
        result = await tool_with_root.execute(file_path="empty.txt", content="")
        assert result.success is True
        assert (tmp_path / "empty.txt").read_text(encoding="utf-8") == ""
        assert (tmp_path / "empty.txt").stat().st_size == 0

    async def test_create_file_default_mode_is_overwrite(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """不传 mode 时默认 overwrite 且创建成功。"""
        result = await tool_with_root.execute(file_path="default.txt", content="hello")
        assert result.success is True


class TestWriteFileOverwrite:
    """覆盖已有文件场景。"""

    async def test_overwrite_existing_file(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """覆盖已有文件，内容被完全替换。"""
        target = tmp_path / "existing.txt"
        target.write_text("old content", encoding="utf-8")

        result = await tool_with_root.execute(file_path="existing.txt", content="new content")
        assert result.success is True
        assert target.read_text(encoding="utf-8") == "new content"

    async def test_overwrite_reduces_file_size(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """覆盖后文件变小。"""
        target = tmp_path / "shrink.txt"
        target.write_text("long content that will be replaced", encoding="utf-8")

        result = await tool_with_root.execute(file_path="shrink.txt", content="short")
        assert result.success is True
        assert target.read_text(encoding="utf-8") == "short"
        assert target.stat().st_size == 5

    async def test_overwrite_increases_file_size(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """覆盖后文件变大。"""
        target = tmp_path / "grow.txt"
        target.write_text("short", encoding="utf-8")

        result = await tool_with_root.execute(
            file_path="grow.txt",
            content="much longer content that replaces the short text",
        )
        assert result.success is True
        expected = "much longer content that replaces the short text"
        assert target.read_text(encoding="utf-8") == expected


class TestWriteFileAppend:
    """追加模式场景。"""

    async def test_append_to_existing_file(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """追加内容到已有文件末尾。"""
        target = tmp_path / "append_me.txt"
        target.write_text("original\n", encoding="utf-8")

        result = await tool_with_root.execute(
            file_path="append_me.txt",
            content="appended",
            mode="append",
        )
        assert result.success is True
        assert target.read_text(encoding="utf-8") == "original\nappended"

    async def test_append_to_nonexistent_file(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """追加到不存在的文件（自动创建，等价于 overwrite）。"""
        result = await tool_with_root.execute(
            file_path="new_append.txt",
            content="first line",
            mode="append",
        )
        assert result.success is True
        assert (tmp_path / "new_append.txt").read_text(encoding="utf-8") == "first line"

    async def test_append_preserves_original_content(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """追加后原有内容不变。"""
        target = tmp_path / "preserve.txt"
        target.write_text("original content\n", encoding="utf-8")

        result = await tool_with_root.execute(
            file_path="preserve.txt",
            content=" more content",
            mode="append",
        )
        assert result.success is True
        # 原有内容应完整保留
        assert target.read_text(encoding="utf-8") == "original content\n more content"

    async def test_append_multiple_times(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """多次追加内容。"""
        target = tmp_path / "multi_append.txt"
        target.write_text("start\n", encoding="utf-8")

        await tool_with_root.execute(
            file_path="multi_append.txt", content="second\n", mode="append"
        )
        await tool_with_root.execute(
            file_path="multi_append.txt", content="third\n", mode="append"
        )
        await tool_with_root.execute(
            file_path="multi_append.txt", content="fourth", mode="append"
        )

        assert target.read_text(encoding="utf-8") == "start\nsecond\nthird\nfourth"


class TestWriteFileParameterValidation:
    """参数校验场景。"""

    async def test_missing_file_path(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """file_path 缺失。"""
        result = await tool_with_root.execute(content="hello")
        assert result.success is False

    async def test_file_path_is_none(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """file_path 为 None。"""
        result = await tool_with_root.execute(file_path=None, content="hello")  # type: ignore[arg-type]
        assert result.success is False

    async def test_file_path_is_empty(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """file_path 为空字符串。"""
        result = await tool_with_root.execute(file_path="", content="hello")
        assert result.success is False

    async def test_file_path_is_whitespace(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """file_path 为纯空白。"""
        result = await tool_with_root.execute(file_path="   ", content="hello")
        assert result.success is False

    async def test_file_path_is_non_string(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """file_path 为非字符串（如 int）。"""
        result = await tool_with_root.execute(file_path=123, content="hello")  # type: ignore[arg-type]
        assert result.success is False

    async def test_missing_content(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """content 缺失。"""
        result = await tool_with_root.execute(file_path="test.txt")
        assert result.success is False

    async def test_content_is_none(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """content 为 None。"""
        result = await tool_with_root.execute(file_path="test.txt", content=None)  # type: ignore[arg-type]
        assert result.success is False

    async def test_content_is_non_string(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """content 为非字符串。"""
        result = await tool_with_root.execute(file_path="test.txt", content=123)  # type: ignore[arg-type]
        assert result.success is False

    async def test_invalid_mode_value(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """mode 为无效值（如 "delete"）。"""
        result = await tool_with_root.execute(
            file_path="test.txt",
            content="hello",
            mode="delete",
        )
        assert result.success is False

    async def test_mode_is_non_string(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """mode 为非字符串。"""
        result = await tool_with_root.execute(
            file_path="test.txt",
            content="hello",
            mode=123,  # type: ignore[arg-type]
        )
        assert result.success is False

    async def test_mode_is_none(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """mode 显式传入 None。"""
        result = await tool_with_root.execute(
            file_path="test.txt",
            content="hello",
            mode=None,  # type: ignore[arg-type]
        )
        assert result.success is False


class TestWriteFileErrors:
    """错误场景。"""

    async def test_write_outside_workspace(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """workspace 外路径被拒绝。"""
        result = await tool_with_root.execute(
            file_path=str(tmp_path.parent / "outside.txt"),
            content="data",
        )
        assert result.success is False
        assert "路径越界" in result.output

    async def test_write_parent_path_escape(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """../ 逃逸被拒绝。"""
        result = await tool_with_root.execute(file_path="../escape.txt", content="data")
        assert result.success is False
        assert "路径越界" in result.output

    async def test_write_sensitive_file_env(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """.env 敏感文件被拒绝。"""
        result = await tool_with_root.execute(file_path=".env", content="SECRET=123")
        assert result.success is False
        assert "敏感文件" in result.output

    async def test_write_sensitive_file_ssh_key(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """SSH 密钥文件被拒绝。"""
        result = await tool_with_root.execute(
            file_path=".ssh/id_rsa",
            content="private key",
        )
        assert result.success is False
        assert "敏感文件" in result.output

    async def test_write_to_existing_directory(
        self, tool_with_root: WriteFile, tmp_path: Path
    ) -> None:
        """目标路径是已存在的目录。"""
        (tmp_path / "mydir").mkdir()

        result = await tool_with_root.execute(file_path="mydir", content="data")
        assert result.success is False
        assert "目录" in result.output

    async def test_no_workspace_root(self, tool: WriteFile) -> None:
        """workspace_root 未设置时返回错误。"""
        result = await tool.execute(file_path="any.txt", content="data")
        assert result.success is False
        assert "工作区根路径未设置" in result.output

    async def test_create_parent_dir_permission_error(
        self, tool_with_root: WriteFile, tmp_path: Path, mocker
    ) -> None:
        """创建父目录时权限不足。"""
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("Access denied"))

        result = await tool_with_root.execute(
            file_path="deep/new/file.txt", content="hello"
        )
        assert result.success is False
        assert "无权限创建目录" in result.output

    async def test_create_parent_dir_os_error(
        self, tool_with_root: WriteFile, tmp_path: Path, mocker
    ) -> None:
        """创建父目录时操作系统错误。"""
        mocker.patch.object(Path, "mkdir", side_effect=OSError("Disk full"))

        result = await tool_with_root.execute(
            file_path="deep/new/file.txt", content="hello"
        )
        assert result.success is False
        assert "创建目录时出错" in result.output

    async def test_write_unicode_encode_error(
        self, tool_with_root: WriteFile, tmp_path: Path, mocker
    ) -> None:
        """写入时编码错误。"""
        enc_err = UnicodeEncodeError("utf-8", "", 0, 1, "can't encode")
        mocker.patch.object(Path, "write_text", side_effect=enc_err)

        result = await tool_with_root.execute(
            file_path="test.txt", content="hello"
        )
        assert result.success is False
        assert "无法以 UTF-8 编码写入" in result.output

    async def test_write_permission_error(
        self, tool_with_root: WriteFile, tmp_path: Path, mocker
    ) -> None:
        """写入时权限不足。"""
        mocker.patch.object(Path, "write_text", side_effect=PermissionError("Access denied"))

        result = await tool_with_root.execute(
            file_path="test.txt", content="hello"
        )
        assert result.success is False
        assert "无权限写入文件" in result.output

    async def test_write_os_error(
        self, tool_with_root: WriteFile, tmp_path: Path, mocker
    ) -> None:
        """写入时操作系统错误。"""
        mocker.patch.object(Path, "write_text", side_effect=OSError("Disk full"))

        result = await tool_with_root.execute(
            file_path="test.txt", content="hello"
        )
        assert result.success is False
        assert "写入文件时出错" in result.output


class TestWriteFileIntegration:
    """集成验证。"""

    async def test_execute_injects_workspace(self, tmp_path: Path) -> None:
        """通过 ToolRegistry 执行时 workspace_root 被正确注入。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(WriteFile)

        result = await registry.execute_tool(
            "write_file",
            {"file_path": "injected.txt", "content": "injected"},
            tmp_path,
        )
        assert result.success is True
        assert (tmp_path / "injected.txt").read_text(encoding="utf-8") == "injected"

    async def test_tool_schema_compatible(self) -> None:
        """工具 schema 符合 OpenAI function calling 格式。"""
        tool = WriteFile()
        schema = tool.get_tool_schema()

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "write_file"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"

        props = schema["function"]["parameters"]["properties"]
        assert "file_path" in props
        assert props["file_path"]["type"] == "string"
        assert "content" in props
        assert props["content"]["type"] == "string"
        assert "mode" in props
        assert props["mode"]["type"] == "string"
        assert "overwrite" in props["mode"]["enum"]
        assert "append" in props["mode"]["enum"]
        assert props["mode"].get("default") == "overwrite"

        required = schema["function"]["parameters"]["required"]
        assert "file_path" in required
        assert "content" in required

        assert schema["function"]["parameters"].get("additionalProperties") is False

    async def test_schema_registered_in_registry(self) -> None:
        """注册后 schema 通过 registry 正确导出。"""
        from minicode.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(WriteFile)

        schemas = registry.get_tools_schema()
        names = [s["function"]["name"] for s in schemas]
        assert "write_file" in names

    async def test_create_default_registry(self) -> None:
        """create_default_registry 返回包含 write_file 的注册器。"""
        from minicode.tools import create_default_registry

        registry = create_default_registry()
        assert registry.has_tool("write_file") is True
