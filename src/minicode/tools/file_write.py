"""文件写入工具。

提供文件写入功能，支持覆盖写入和追加写入两种模式。
自动创建不存在的父目录。
"""

from __future__ import annotations

from minicode.tools.base import BaseTool, ToolResult
from minicode.tools.path_safety import resolve_and_validate_path
from minicode.utils.exceptions import ToolError

# 用于区分"未传入参数"和"显式传入 None"
_MISSING = object()


class WriteFile(BaseTool):
    """写入文本内容到文件。

    支持覆盖写入和追加写入两种模式。
    自动创建不存在的父目录。
    """

    name: str = "write_file"
    description: str = (
        "写入文本内容到文件。支持覆盖写入（overwrite）和追加写入（append）两种模式。"
        "默认使用覆盖写入模式，将完全替换文件内容。"
        "追加模式会将内容添加到文件末尾。"
        "如果文件所在的父目录不存在，会自动创建。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要写入的文件路径（相对工作区根目录的路径，或绝对路径）",
            },
            "content": {
                "type": "string",
                "description": "要写入文件的文本内容",
            },
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append"],
                "description": "写入模式：overwrite=覆盖写入（默认），append=追加到文件末尾",
                "default": "overwrite",
            },
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        # ----- file_path 参数校验 -----
        file_path = kwargs.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            return ToolResult(success=False, output="参数 file_path 必须是有效的非空文件路径")

        # ----- content 参数校验 -----
        content = kwargs.get("content")
        if content is None:
            return ToolResult(success=False, output="参数 content 不能为 None")
        # bool 是 int 的子类，需要优先排除
        if isinstance(content, bool) or not isinstance(content, str):
            return ToolResult(success=False, output="参数 content 必须是字符串")

        # ----- mode 参数校验 -----
        mode_raw = kwargs.get("mode", _MISSING)
        if mode_raw is _MISSING:
            mode = "overwrite"
        elif mode_raw is None:
            return ToolResult(success=False, output="参数 mode 不能为 None")
        elif isinstance(mode_raw, bool) or not isinstance(mode_raw, str):
            return ToolResult(success=False, output="参数 mode 必须是字符串")
        elif mode_raw not in ("overwrite", "append"):
            return ToolResult(success=False, output="参数 mode 必须是 'overwrite' 或 'append'")
        else:
            mode = mode_raw

        if self.workspace_root is None:
            return ToolResult(success=False, output="工作区根路径未设置")

        # 路径安全检查
        try:
            resolved_path = resolve_and_validate_path(file_path, self.workspace_root)
        except ToolError as e:
            return ToolResult(success=False, output=str(e))

        # 检查目标路径是否是已存在的目录
        if resolved_path.is_dir():
            return ToolResult(
                success=False,
                output=f"路径是已存在的目录，无法写入：{resolved_path}",
            )

        # 确定操作类型
        file_exists = resolved_path.exists()
        if not file_exists:
            operation_type = "创建新文件"
        elif mode == "overwrite":
            operation_type = "覆盖已有文件"
        else:
            operation_type = "追加内容到文件"

        # 自动创建父目录
        try:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return ToolResult(success=False, output=f"无权限创建目录：{resolved_path.parent}")
        except OSError as e:
            return ToolResult(success=False, output=f"创建目录时出错：{e}")

        # 写入/追加内容
        try:
            if mode == "append" and file_exists:
                with open(resolved_path, "a", encoding="utf-8") as f:
                    f.write(content)
            else:
                resolved_path.write_text(content, encoding="utf-8")
        except UnicodeEncodeError:
            return ToolResult(success=False, output=f"无法以 UTF-8 编码写入文件：{resolved_path}")
        except PermissionError:
            return ToolResult(success=False, output=f"无权限写入文件：{resolved_path}")
        except OSError as e:
            return ToolResult(success=False, output=f"写入文件时出错：{e}")

        # 计算统计信息
        written_content = resolved_path.read_text(encoding="utf-8")
        byte_count = len(written_content.encode("utf-8"))
        line_count = len(written_content.splitlines())

        # 构建返回信息
        if mode == "append" and file_exists:
            append_bytes = len(content.encode("utf-8"))
            output = (
                f"操作类型：{operation_type}\n"
                f"路径：{resolved_path}\n"
                f"大小：{byte_count} 字节，{line_count} 行\n"
                f"追加：{append_bytes} 字节"
            )
        else:
            output = (
                f"操作类型：{operation_type}\n"
                f"路径：{resolved_path}\n"
                f"大小：{byte_count} 字节，{line_count} 行"
            )

        return ToolResult(success=True, output=output)
