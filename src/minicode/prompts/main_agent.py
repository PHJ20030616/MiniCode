"""主 Agent 系统 prompt。"""

from collections.abc import Sequence

from minicode.prompts.composition import join_sections, render_named_items
from minicode.prompts.models import ToolPromptInfo


def build_main_agent_prompt(
    tools: Sequence[ToolPromptInfo],
    memory_content: str | None = None,
    memory_enabled: bool = True,
    subagent_enabled: bool = False,
) -> str:
    """构建主 Agent 系统提示词，不访问注册器或配置对象。"""
    effective_tools = [
        tool for tool in tools if memory_enabled or tool.name.strip() != "remember"
    ]
    sections = [
        (
            "你是 MiniCode，一个轻量级的 AI 编程助手。"
            "你可以通过工具读取项目文件、搜索代码内容，帮助用户理解代码、解决问题。"
            "请用中文回答用户的问题，保持回答简洁准确。"
        )
    ]

    rendered_tools = render_named_items(effective_tools)
    if rendered_tools:
        sections.append(
            "## 可用工具\n\n"
            "你可以在回答前使用以下工具来获取信息：\n\n"
            f"{rendered_tools}\n\n"
            "请根据用户的问题选择合适的工具。"
            "每次调用工具后，你将看到执行结果，请基于结果继续回答。"
        )

    if memory_enabled and any(
        tool.name.strip() == "remember" for tool in effective_tools
    ):
        sections.append(
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

    if subagent_enabled:
        sections.append(
            "### 子代理委派准则\n\n"
            "当任务可以拆成边界清晰、互不依赖的检索、审查或验证工作时，可以使用 "
            "`run_subagent` 启动子代理。\n"
            "- 需要独立检索多个代码区域时，优先委派 researcher。\n"
            "- 需要审查既有改动或方案风险时，优先委派 reviewer。\n"
            "- 需要判断测试范围、验证命令或失败原因时，优先委派 tester。\n"
            "- 不要把简单的单文件修改、需要用户决策的事项、或没有明确边界的任务委派出去。\n"
            "- 子代理只返回结构化摘要；你需要基于摘要继续整合、修改或回复用户。"
        )

    if memory_enabled and memory_content:
        sections.append(
            "---\n"
            "## 用户记忆\n\n"
            f"{memory_content}\n\n"
            "> ⚠️ 用户记忆，可能不完整或过期。请以当前对话上下文为准。"
        )

    return join_sections(*sections)
