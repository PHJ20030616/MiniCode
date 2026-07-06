"""Glob 文件路径匹配工具。

使用 pathlib 的 glob 实现文件搜索，支持 **/*.py 等递归模式。
匹配结果按路径排序后返回，默认最多输出 250 条。
"""

from __future__ import annotations

from minicode.tools.base import BaseTool, ToolResult

# 默认最大输出行数
DEFAULT_MAX_LINES = 250


class GlobFiles(BaseTool):
    """使用 glob 模式匹配工作区中的文件路径。

    支持 **/*.py 等递归 glob 模式，匹配结果按路径排序后返回。
    默认最多输出 250 条匹配结果，超出部分会提示已截断。
    """

    name: str = "glob"
    description: str = (
        "使用 glob 模式匹配工作区中的文件路径。"
        "支持 **/*.py 等递归模式，匹配结果按路径排序后返回。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "glob 匹配模式，例如 **/*.py、*.txt、src/**/*.ts",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        # ----- pattern 参数校验 -----
        pattern = kwargs.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            return ToolResult(success=False, output="参数 pattern 必须是有效的非空 glob 模式")

        if self.workspace_root is None:
            return ToolResult(success=False, output="工作区根路径未设置")

        # 执行 glob 匹配
        try:
            matches = sorted(self.workspace_root.glob(pattern))
        except (NotImplementedError, ValueError):
            return ToolResult(success=False, output=f"无效的 glob 模式：{pattern}")

        # 转换为相对路径，过滤工作区外的路径
        paths: list[str] = []
        for m in matches:
            try:
                rel = m.relative_to(self.workspace_root)
            except ValueError:
                continue
            # Windows 上 relative_to 不会解析 ..，需额外检查
            if ".." in rel.parts:
                continue
            paths.append(str(rel))

        if not paths:
            return ToolResult(success=True, output=f"没有匹配到路径（模式：{pattern}）")

        # 输出截断
        total = len(paths)
        truncated = False
        if total > DEFAULT_MAX_LINES:
            paths = paths[:DEFAULT_MAX_LINES]
            truncated = True

        output = "\n".join(paths)
        summary = f"匹配到 {total} 个路径"
        if truncated:
            summary += f"（显示前 {DEFAULT_MAX_LINES} 个）"

        result = f"{summary}：\n\n{output}"
        if truncated:
            result += f"\n...（结果已截断，共 {total} 个路径，仅显示前 {DEFAULT_MAX_LINES} 个）"

        return ToolResult(success=True, output=result)
