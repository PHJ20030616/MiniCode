"""文件读取工具。

提供只读的文件内容读取功能，支持行范围选择和字符数截断。
"""

from __future__ import annotations

from minicode.tools.base import BaseTool, ToolResult
from minicode.tools.path_safety import resolve_and_validate_path
from minicode.utils.exceptions import ToolError

# 默认最大输出字符数
DEFAULT_MAX_CHARS = 20_000

# 文本文件启发式检测参数
_MAX_CONTROL_CHAR_RATIO = 0.30
_SAFE_CONTROL_CHARS = frozenset("\n\r\t")
# 用于区分"未传入参数"和"显式传入 None"
_MISSING = object()


class ReadFile(BaseTool):
    """读取文本文件内容。

    支持通过 offset（行偏移）和 limit（行数限制）选择读取范围。
    默认最多输出 20,000 个字符，超出部分会被截断。
    """

    name: str = "read_file"
    description: str = (
        "读取文本文件内容，支持按行范围选择和字符数截断。"
        "适用于代码文件、配置文件、文档等文本格式文件。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要读取的文件路径（相对工作区根目录的路径，或绝对路径）",
            },
            "offset": {
                "type": "integer",
                "description": "起始行号（从 0 开始），默认为 0",
                "default": 0,
                "minimum": 0,
            },
            "limit": {
                "type": "integer",
                "description": "最大读取行数，不指定则读取到文件末尾",
                "minimum": 0,
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        # ----- file_path 参数校验 -----
        file_path = kwargs.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            return ToolResult(success=False, output="参数 file_path 必须是有效的非空文件路径")
        # 保留原始 file_path 用于路径解析，strip 仅用于空白判断

        # ----- offset 参数校验 -----
        offset_raw = kwargs.get("offset", _MISSING)
        if offset_raw is _MISSING:
            offset = 0
        elif offset_raw is None:
            return ToolResult(success=False, output="参数 offset 不能为 None")
        elif isinstance(offset_raw, bool) or not isinstance(offset_raw, int):
            return ToolResult(success=False, output="参数 offset 必须是整数")
        elif offset_raw < 0:
            return ToolResult(success=False, output="参数 offset 不能为负数")
        else:
            offset = offset_raw

        # ----- limit 参数校验 -----
        limit_raw = kwargs.get("limit", _MISSING)
        limit: int | None = None
        if limit_raw is _MISSING:
            pass
        elif limit_raw is None:
            return ToolResult(success=False, output="参数 limit 不能为 None")
        elif isinstance(limit_raw, bool) or not isinstance(limit_raw, int):
            return ToolResult(success=False, output="参数 limit 必须是整数")
        elif limit_raw < 0:
            return ToolResult(success=False, output="参数 limit 不能为负数")
        else:
            limit = limit_raw

        if self.workspace_root is None:
            return ToolResult(success=False, output="工作区根路径未设置")

        # 路径安全检查
        try:
            resolved_path = resolve_and_validate_path(file_path, self.workspace_root)
        except ToolError as e:
            return ToolResult(success=False, output=str(e))

        # 文件存在性与类型检查
        if not resolved_path.exists():
            return ToolResult(success=False, output=f"文件不存在：{resolved_path}")

        if resolved_path.is_dir():
            return ToolResult(success=False, output=f"路径是目录，不是文件：{resolved_path}")

        if not resolved_path.is_file():
            return ToolResult(success=False, output=f"路径不是常规文件：{resolved_path}")

        # 读取文件内容
        try:
            content = resolved_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                success=False,
                output=f"无法以文本格式读取文件（可能是二进制文件）：{resolved_path}",
            )
        except PermissionError:
            return ToolResult(success=False, output=f"无权限读取文件：{resolved_path}")
        except OSError as e:
            return ToolResult(success=False, output=f"读取文件时出错：{e}")

        # 二进制内容启发式检测（解码后的附加检查）
        if "\x00" in content:
            return ToolResult(
                success=False,
                output=f"文件包含 NUL 字节，不是文本文件：{resolved_path}",
            )

        total_chars = len(content)
        if total_chars > 0:
            control_count = sum(
                1 for c in content if c not in _SAFE_CONTROL_CHARS and ord(c) < 32
            )
            if control_count / total_chars > _MAX_CONTROL_CHAR_RATIO:
                return ToolResult(
                    success=False,
                    output=f"文件包含大量控制字符，可能是二进制文件：{resolved_path}",
                )

        # 按行分割，使用 keepends=True 保留原始行格式
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        # 处理 offset 超出范围
        if offset >= total_lines:
            return ToolResult(
                success=True,
                output=f"（文件共 {total_lines} 行，起始行 {offset} 超出范围）",
            )

        # 选取行范围
        end_line: int = total_lines if limit is None else min(offset + limit, total_lines)
        selected_lines = lines[offset:end_line]
        result = "".join(selected_lines)

        # 字符数截断
        truncated = False
        if len(result) > DEFAULT_MAX_CHARS:
            result = result[:DEFAULT_MAX_CHARS]
            truncated = True

        # 构建返回信息
        file_info = f"文件：{resolved_path}（共 {total_lines} 行"
        if offset > 0 or limit is not None:
            file_info += f"，显示第 {offset}-{end_line} 行"
        file_info += "）"

        output = f"{file_info}\n\n{result}"
        if truncated:
            output += f"\n...（输出已截断，仅显示前 {DEFAULT_MAX_CHARS} 个字符）"

        return ToolResult(success=True, output=output)
