"""记忆删除工具 — 供 Agent 在用户明确表达删除意图时调用。

允许大模型在识别到用户自然语言中的删除意图后，
通过此工具删除指定名称的长期记忆。

触发场景示例：
- "忘记我之前说的..."
- "删除关于...的记忆"
- "不要记住..."
- "移除记忆..."
"""
from __future__ import annotations

from minicode.memory.manager import MemoryManager
from minicode.tools.base import BaseTool, ToolResult


class Forget(BaseTool):
    """删除指定名称的长期记忆。

    仅在用户明确表达「忘记…」「删除记忆…」「不要记住…」「移除记忆…」
    等语义时调用此工具。不要主动删除记忆。
    """

    name: str = "forget"
    description: str = (
        "删除指定名称的长期记忆。"
        "当用户说「忘记…」「删除记忆…」「不要记住…」「移除记忆…」等时调用此工具。"
        "仅在用户明确要求删除时使用，不要主动删除记忆。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要删除的记忆唯一标识名，如 'reply-lang'",
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    risk_level: str = "caution"  # 谨慎操作，但可恢复

    async def execute(self, **kwargs: object) -> ToolResult:
        """执行记忆删除。

        Args:
            **kwargs: 必须包含 name 参数。

        Returns:
            删除结果。
        """
        # ── 参数提取与验证 ──
        name = kwargs.get("name")

        if not isinstance(name, str) or not name.strip():
            return ToolResult(
                success=False,
                output="参数 name 必须是有效的非空字符串",
            )

        name = name.strip()

        # ── 工作区路径检查 ──
        if self.workspace_root is None:
            return ToolResult(
                success=False,
                output="工作区根路径未设置",
            )

        # ── 名称合法性验证 ──
        try:
            MemoryManager._validate_memory_name(name)
        except ValueError as e:
            return ToolResult(success=False, output=str(e))

        # ── 执行删除 ──
        try:
            manager = MemoryManager(self.workspace_root)
            deleted = manager.delete(name)

            if not deleted:
                # 记忆不存在，但目标已达成
                return ToolResult(
                    success=True,
                    output=f"记忆「{name}」不存在或已被删除。",
                )

            return ToolResult(
                success=True,
                output=f"已忘记记忆「{name}」。",
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output=f"删除记忆失败：{e}",
            )
