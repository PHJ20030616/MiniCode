"""Subagent 工具过滤策略。"""

from __future__ import annotations

from minicode.agent.subagents.models import SubagentConfig, SubagentRole
from minicode.utils.exceptions import ToolError

READ_ONLY_TOOLS = frozenset({"read_file", "grep", "glob"})
WRITE_TOOLS = frozenset({"write_file", "edit_file", "delete_file", "remove_file", "remember"})
SHELL_TOOLS = frozenset({"shell"})
RECURSIVE_TOOLS = frozenset({"run_subagent"})

ROLE_DEFAULT_TOOLS: dict[SubagentRole, list[str]] = {
    SubagentRole.RESEARCHER: ["read_file", "grep", "glob"],
    SubagentRole.REVIEWER: ["read_file", "grep", "glob"],
    SubagentRole.TESTER: ["read_file", "grep", "glob", "shell"],
    SubagentRole.IMPLEMENTER: ["read_file", "grep", "glob"],
    SubagentRole.GENERAL: ["read_file", "grep", "glob"],
}


def resolve_allowed_tools(
    *,
    requested_tools: list[str] | None,
    role: SubagentRole,
    config: SubagentConfig,
    available_tools: list[str],
) -> list[str]:
    """计算 subagent 最终允许使用的工具列表。"""
    available = set(available_tools)
    if requested_tools is None:
        candidates = (
            list(config.default_allowed_tools)
            if role == SubagentRole.GENERAL
            else list(ROLE_DEFAULT_TOOLS[role])
        )
    else:
        candidates = list(dict.fromkeys(requested_tools))

    if not candidates:
        raise ToolError("子代理没有可用工具，请至少允许一个工具。")

    unknown = [name for name in candidates if name not in available]
    if unknown:
        raise ToolError(f"子代理请求了不存在的工具：{', '.join(unknown)}")

    recursive = [name for name in candidates if name in RECURSIVE_TOOLS]
    if recursive:
        raise ToolError("子代理不能继续调用 run_subagent，已阻止递归委派。")

    write_tools = [name for name in candidates if name in WRITE_TOOLS]
    if write_tools and not config.allow_write_tools:
        raise ToolError(
            "子代理默认禁止写入工具；如需允许，请启用 "
            "agent.subagents.allow_write_tools 并显式传入 allowed_tools。"
        )
    if write_tools and requested_tools is None:
        raise ToolError("写入工具必须在 allowed_tools 中显式声明。")

    allowed = [name for name in candidates if name in available and name not in RECURSIVE_TOOLS]
    if not allowed:
        raise ToolError("过滤后没有可用工具，请调整 allowed_tools。")
    return allowed
