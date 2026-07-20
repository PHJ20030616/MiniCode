"""系统提示词构建。

根据已注册的工具列表，动态生成 Agent 系统提示词。
"""

from __future__ import annotations

from minicode.tools.registry import ToolRegistry
from minicode.prompts import ToolPromptInfo, build_main_agent_prompt


def build_system_prompt(
    tool_registry: ToolRegistry,
    memory_content: str | None = None,
    memory_enabled: bool = True,
) -> str:
    """构建 Agent 系统提示词。

    包含角色定义、能力说明和可用工具列表。
    工具列表从 registry 动态获取，确保与实际注册的工具一致。
    当 memory_enabled 为 False 时，跳过记忆使用说明和记忆内容注入。

    Args:
        tool_registry: 已注册工具的注册器实例。
        memory_content: 可选的记忆内容，非空时注入到提示词末尾。
        memory_enabled: 记忆系统是否启用。为 False 时跳过所有记忆相关注入。

    Returns:
        格式化的系统提示词文本。
    """
    tool_names = tool_registry.tool_names
    tools = [
        ToolPromptInfo(
            name=tool.name,
            description=tool.description,
        )
        for name in tool_names
        if memory_enabled or name != "remember"
        for tool in [tool_registry.get_tool(name)]
    ]
    return build_main_agent_prompt(
        tools,
        memory_content=memory_content,
        memory_enabled=memory_enabled,
        subagent_enabled=tool_registry.has_tool("run_subagent"),
    )
