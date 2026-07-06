"""系统提示词构建。

根据已注册的工具列表，动态生成 Agent 系统提示词。
"""

from __future__ import annotations

from minicode.tools.registry import ToolRegistry


def build_system_prompt(tool_registry: ToolRegistry) -> str:
    """构建 Agent 系统提示词。

    包含角色定义、能力说明和可用工具列表。
    工具列表从 registry 动态获取，确保与实际注册的工具一致。

    Args:
        tool_registry: 已注册工具的注册器实例。

    Returns:
        格式化的系统提示词文本。
    """
    tool_names = tool_registry.tool_names
    if not tool_names:
        return _build_base_prompt()

    tools_desc = "\n".join(
        f"  - {tool.name}: {tool.description}"
        for tool in (tool_registry.get_tool(name) for name in tool_names)
    )

    return (
        f"{_build_base_prompt()}\n\n"
        f"## 可用工具\n\n"
        f"你可以在回答前使用以下工具来获取信息：\n\n"
        f"{tools_desc}\n\n"
        f"请根据用户的问题选择合适的工具。"
        f"每次调用工具后，你将看到执行结果，请基于结果继续回答。"
    )


def _build_base_prompt() -> str:
    """构建基础系统提示词。"""
    return (
        "你是 MiniCode，一个轻量级的 AI 编程助手。"
        "你可以通过工具读取项目文件、搜索代码内容，帮助用户理解代码、解决问题。"
        "请用中文回答用户的问题，保持回答简洁准确。"
    )
