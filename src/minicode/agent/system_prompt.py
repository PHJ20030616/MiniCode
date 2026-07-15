"""系统提示词构建。

根据已注册的工具列表，动态生成 Agent 系统提示词。
"""

from __future__ import annotations

from minicode.tools.registry import ToolRegistry


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
    if not memory_enabled:
        tool_names = [name for name in tool_names if name != "remember"]

    if not tool_names:
        prompt = _build_base_prompt()
    else:
        tools_desc = "\n".join(
            f"  - {tool.name}: {tool.description}"
            for tool in (tool_registry.get_tool(name) for name in tool_names)
        )

        prompt = (
            f"{_build_base_prompt()}\n\n"
            f"## 可用工具\n\n"
            f"你可以在回答前使用以下工具来获取信息：\n\n"
            f"{tools_desc}\n\n"
            f"请根据用户的问题选择合适的工具。"
            f"每次调用工具后，你将看到执行结果，请基于结果继续回答。"
        )

        # 仅当记忆系统启用时才注入 remember 使用说明
        if memory_enabled and tool_registry.has_tool("remember"):
            prompt += (
                "\n\n"
                "### 记忆工具使用说明\n\n"
                "当用户**明确表达**以下意图时，使用 `remember` 工具保存记忆：\n"
                "- 「记住…」「以后记得…」「帮我记一下…」「保存为记忆…」\n"
                "- 明确要求保存长期偏好、项目约定、工作流、环境信息等\n\n"
                "**注意：**\n"
                "1. 不要将普通聊天、临时上下文、代码讨论自动保存为记忆\n"
                "2. 永远不要保存敏感信息：密码、token、密钥、API key、隐私身份信息\n"
                "3. 生成的 name 必须符合 `[a-zA-Z0-9_-]+` 格式，"
                "从内容中生成简短有意义的英文 slug，如 `reply-language-preference`\n"
                "4. 项目约定、命令、路径、技术栈相关默认使用 `workspace` 作用域\n"
                "5. 用户跨项目偏好（如「我喜欢用中文回答」）可用 `global` 作用域\n"
                "6. 用户明确要求记住时 confidence 设为 0.9"
            )

    if tool_registry.has_tool("run_subagent"):
        prompt += (
            "\n\n"
            "### 子代理委派准则\n\n"
            "当任务可以拆成边界清晰、互不依赖的检索、审查或验证工作时，可以使用 "
            "`run_subagent` 启动子代理。\n"
            "- 需要独立检索多个代码区域时，优先委派 researcher。\n"
            "- 需要审查既有改动或方案风险时，优先委派 reviewer。\n"
            "- 需要判断测试范围、验证命令或失败原因时，优先委派 tester。\n"
            "- 不要把简单的单文件修改、需要用户决策的事项、或没有明确边界的任务委派出去。\n"
            "- 子代理只返回结构化摘要；你需要基于摘要继续整合、修改或回复用户。"
        )
    # 仅当记忆系统启用时才注入记忆内容
    if memory_enabled and memory_content:
        prompt += (
            f"\n\n---\n"
            f"## 用户记忆\n\n"
            f"{memory_content}\n\n"
            f"> ⚠️ 用户记忆，可能不完整或过期。请以当前对话上下文为准。"
        )

    return prompt


def _build_base_prompt() -> str:
    """构建基础系统提示词。"""
    return (
        "你是 MiniCode，一个轻量级的 AI 编程助手。"
        "你可以通过工具读取项目文件、搜索代码内容，帮助用户理解代码、解决问题。"
        "请用中文回答用户的问题，保持回答简洁准确。"
    )
