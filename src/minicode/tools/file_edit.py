"""文件精确编辑工具。

提供基于精确字符串匹配的文件内容替换功能。
支持单次替换（默认）和全局替换（replace_all=true）两种模式。
使用 raw bytes 读写以保留文件原始的换行符格式。
"""

from __future__ import annotations

import difflib

from minicode.tools.base import BaseTool, ToolResult
from minicode.tools.path_safety import resolve_and_validate_path
from minicode.utils.exceptions import ToolError

# 用于区分"未传入参数"和"显式传入 None"
_MISSING = object()


def _find_non_overlapping_matches(text: str, old_string: str) -> list[int]:
    """返回 old_string 在 text 中所有非重叠匹配的起始索引。

    使用非重叠匹配语义（与 str.replace / str.count 一致）。
    例如 "aaaa" 中查找 "aa" 只匹配位置 0 和 2（而非 0/1/2）。

    Args:
        text: 文件内容
        old_string: 要查找的字符串

    Returns:
        所有非重叠匹配的起始索引列表
    """
    positions: list[int] = []
    start = 0
    step = len(old_string) or 1
    while True:
        idx = text.find(old_string, start)
        if idx == -1:
            break
        positions.append(idx)
        start = idx + step
    return positions


def _find_line_numbers(
    text: str,
    positions: list[int],
    old_string: str,
) -> list[tuple[int, int]]:
    """计算每个匹配位置的行号范围。

    Args:
        text: 文件内容
        positions: 匹配起始索引列表
        old_string: 被匹配的字符串（用于计算结束行）

    Returns:
        (起始行号, 结束行号) 的列表，行号为 1-indexed
    """
    line_ranges: list[tuple[int, int]] = []
    for pos in positions:
        start_line = text[:pos].count("\n") + 1  # 1-indexed
        end_line = start_line + old_string.count("\n")
        line_ranges.append((start_line, end_line))
    return line_ranges


def _build_line_numbers_summary(line_ranges: list[tuple[int, int]]) -> str:
    """构建行号描述字符串。

    单行显示为 "第 N 行"，多行显示为 "第 N-M 行"。
    多个位置用 "，" 分隔。

    Args:
        line_ranges: (起始行号, 结束行号) 的列表

    Returns:
        格式化后的行号描述
    """
    parts: list[str] = []
    for start, end in line_ranges:
        if start == end:
            parts.append(f"第 {start} 行")
        else:
            parts.append(f"第 {start}-{end} 行")
    return "，".join(parts)


def _generate_diff(file_path: str, old_text: str, new_text: str) -> str:
    """生成 unified diff 格式的变更摘要。

    Args:
        file_path: 文件路径（用于 diff 头）
        old_text: 编辑前内容
        new_text: 编辑后内容

    Returns:
        unified diff 字符串，无变更时返回空字符串
    """
    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=file_path,
            tofile=file_path,
            n=0,
        )
    )
    return "".join(diff_lines)


class EditFile(BaseTool):
    """精确编辑文件内容。

    通过精确字符串匹配替换文件中的指定文本。
    支持单次替换（默认）和全局替换（replace_all=true）。
    匹配是精确字面量匹配，不支持正则表达式。
    使用 raw bytes 读写，保留文件原有换行符格式。
    """

    name: str = "edit_file"
    description: str = (
        "精确编辑文件内容。通过字符串匹配替换文件中的指定文本，"
        "支持单次替换（默认）和全局替换（replace_all=true）。"
        "匹配是精确字面量匹配，不支持正则表达式。"
        "替换会保留文件原有换行符格式。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要编辑的文件路径（相对工作区根目录的路径，或绝对路径）",
            },
            "old_string": {
                "type": "string",
                "description": "要替换的原始文本（精确字面量匹配，非正则表达式）",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新文本（可为空字符串表示删除匹配文本）",
            },
            "replace_all": {
                "type": "boolean",
                "description": "是否替换所有匹配。false=仅替换唯一的匹配；true=替换所有匹配",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        """执行文件编辑操作。

        Args:
            **kwargs: 工具参数，包含 file_path、old_string、new_string、replace_all

        Returns:
            编辑结果，成功时包含 diff 修改摘要，失败时包含错误原因
        """
        # ----- file_path 参数校验 -----
        file_path = kwargs.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            return ToolResult(success=False, output="参数 file_path 必须是有效的非空文件路径")

        # ----- old_string 参数校验 -----
        old_string = kwargs.get("old_string")
        if not isinstance(old_string, str):
            return ToolResult(success=False, output="参数 old_string 必须是字符串")
        if not old_string:
            return ToolResult(success=False, output="参数 old_string 不能为空")

        # ----- new_string 参数校验 -----
        new_string = kwargs.get("new_string")
        if new_string is None:
            return ToolResult(success=False, output="参数 new_string 不能为 None")
        if isinstance(new_string, bool) or not isinstance(new_string, str):
            return ToolResult(success=False, output="参数 new_string 必须是字符串")

        # ----- replace_all 参数校验 -----
        replace_all_raw = kwargs.get("replace_all", _MISSING)
        if replace_all_raw is _MISSING:
            replace_all = False
        elif isinstance(replace_all_raw, bool):
            replace_all = replace_all_raw
        else:
            return ToolResult(success=False, output="参数 replace_all 必须是布尔值")

        if self.workspace_root is None:
            return ToolResult(success=False, output="工作区根路径未设置")

        # 路径安全检查
        try:
            resolved_path = resolve_and_validate_path(file_path, self.workspace_root)
        except ToolError as e:
            return ToolResult(success=False, output=str(e))

        # 检查文件是否存在
        if not resolved_path.exists():
            return ToolResult(success=False, output=f"文件不存在：{resolved_path}")

        # 检查目标是否是目录
        if resolved_path.is_dir():
            return ToolResult(
                success=False,
                output=f"路径是已存在的目录，无法编辑：{resolved_path}",
            )

        # 以 raw bytes 读取文件，保留原始换行符
        try:
            raw_bytes = resolved_path.read_bytes()
        except PermissionError:
            return ToolResult(success=False, output=f"无权限读取文件：{resolved_path}")
        except OSError as e:
            return ToolResult(success=False, output=f"读取文件时出错：{e}")

        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(success=False, output=f"文件不是有效的 UTF-8 编码：{resolved_path}")

        # 使用统一的非重叠匹配列表驱动所有后续逻辑
        positions = _find_non_overlapping_matches(content, old_string)
        occurrences = len(positions)

        if occurrences == 0:
            return ToolResult(success=False, output="未找到要替换的文本")

        line_ranges = _find_line_numbers(content, positions, old_string)

        # 唯一性检查（单次替换模式）
        if not replace_all and occurrences > 1:
            line_summary = _build_line_numbers_summary(line_ranges)
            return ToolResult(
                success=False,
                output=(
                    f"old_string 不唯一，找到 {occurrences} 处匹配，"
                    f"位于：{line_summary}\n"
                    f"如需替换所有匹配，请设置 replace_all=true"
                ),
            )

        # 执行替换（str.replace 使用非重叠语义，与 _find_non_overlapping_matches 一致）
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        actual_count = occurrences if replace_all else 1

        # 编码回 bytes 并写回文件
        try:
            new_raw_bytes = new_content.encode("utf-8")
        except UnicodeEncodeError:
            return ToolResult(success=False, output=f"无法以 UTF-8 编码写入文件：{resolved_path}")

        try:
            resolved_path.write_bytes(new_raw_bytes)
        except PermissionError:
            return ToolResult(success=False, output=f"无权限写入文件：{resolved_path}")
        except OSError as e:
            return ToolResult(success=False, output=f"写入文件时出错：{e}")

        # 构建 diff 摘要
        diff = _generate_diff(str(resolved_path), content, new_content)

        output = (
            f"路径：{resolved_path}\n"
            f"替换次数：{actual_count}\n"
            f"匹配位置：{_build_line_numbers_summary(line_ranges)}\n"
            f"{diff}"
        )

        return ToolResult(success=True, output=output)
