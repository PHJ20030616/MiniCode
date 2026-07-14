"""ReAct Agent Loop — 模型推理与工具调用的闭环。

实现 ReAct（Reasoning + Acting）模式：
1. 调用模型（携带工具 schema）
2. 流式渲染文本回复
3. 收集 tool_call，串行执行工具
4. 将工具结果追加回对话
5. 循环直到模型返回纯文本回复或达到最大轮次
"""

from __future__ import annotations

import inspect
import json
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markdown import Markdown
from rich.text import Text

from minicode.agent.context import build_messages
from minicode.agent.context_models import ContextBuildReport
from minicode.agent.planner import TaskPlanner
from minicode.agent.planning_models import ExecutionPlan
from minicode.agent.system_prompt import build_system_prompt
from minicode.memory.manager import MemoryManager
from minicode.permissions.checker import check_permission
from minicode.providers.base import (
    FunctionCall,
    Message,
    StreamChunk,
    ToolCall,
    ToolMessage,
    UsageInfo,
)
from minicode.utils.exceptions import ProviderError
from minicode.utils.log import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from minicode.cli.confirm import PermissionConfirmer
    from minicode.cli.renderer import StreamingRenderer
    from minicode.config.models import AppConfig
    from minicode.permissions.store import PermissionStore
    from minicode.providers.base import BaseProvider
    from minicode.tools.registry import ToolRegistry

logger = get_logger(__name__)


class AgentLoop:
    """ReAct Agent Loop。

    管理完整对话上下文，负责：
    - 构建 messages（system + history + 最新工具结果）
    - 调用 provider.chat() 并流式渲染
    - 收集并组装 tool_call delta
    - 串行执行工具，将结果追加回上下文
    - 循环直到模型返回纯文本或达到最大轮次

    用法：
        loop = AgentLoop(provider, registry, renderer, config)
        await loop.run("请读取 README.md 并总结")
    """

    def __init__(
        self,
        provider: BaseProvider,
        tool_registry: ToolRegistry,
        renderer: StreamingRenderer,
        config: AppConfig,
        workspace_root: Path | None = None,
        permission_store: PermissionStore | None = None,
        permission_confirmer: PermissionConfirmer | None = None,
    ) -> None:
        """初始化 Agent Loop。

        Args:
            provider: LLM Provider 实例。
            tool_registry: 工具注册器。
            renderer: 流式渲染器。
            config: 应用配置。
            workspace_root: 工作区根路径，默认为当前目录。
        """
        self.provider = provider
        self.tool_registry = tool_registry
        self.renderer = renderer
        self.config = config
        self.workspace_root = workspace_root or Path.cwd()
        self.permission_store = permission_store
        self.permission_confirmer = permission_confirmer
        self.messages: list[Message] = []
        self.last_context_report: ContextBuildReport | None = None
        self.last_execution_plan: ExecutionPlan | None = None
        self._memory_enabled = config.memory.enabled

        # 加载记忆内容（如果启用）
        memory_content: str | None = None
        if self._memory_enabled:
            mm = MemoryManager(self.workspace_root)
            memory_content = mm.get_all_content(
                max_chars=config.memory.max_chars,
                workspace=str(self.workspace_root),
            )

        self.system_prompt = build_system_prompt(
            tool_registry,
            memory_content=memory_content,
            memory_enabled=self._memory_enabled,
        )

    def reload_memory(self) -> None:
        """重新加载记忆并刷新系统提示词。

        在记忆被添加或删除后调用，使当前会话立即反映最新记忆状态。
        如果记忆系统已禁用，构建不含记忆内容和不含 remember 指令的 prompt。
        """
        memory_content: str | None = None
        if self._memory_enabled:
            mm = MemoryManager(self.workspace_root)
            memory_content = mm.get_all_content(
                max_chars=self.config.memory.max_chars,
                workspace=str(self.workspace_root),
            )

        self.system_prompt = build_system_prompt(
            self.tool_registry,
            memory_content=memory_content,
            memory_enabled=self._memory_enabled,
        )

    async def _create_execution_plan(self, user_input: str) -> ExecutionPlan:
        """生成并展示执行计划。

        规划阶段只读取当前会话历史，不执行工具；生成后的计划会作为 assistant
        消息进入历史，使后续 ReAct 阶段可以基于该计划继续行动。
        """
        self.renderer.show_info("正在制定执行计划...")
        planner = TaskPlanner(
            provider=self.provider,
            planning_config=self.config.agent.planning,
            context_config=self.config.agent.context,
            stream=self.config.agent.stream,
        )
        plan = await planner.create_plan(
            messages=self.messages,
            user_input=user_input,
            max_tokens=self.config.max_tokens,
        )
        plan_markdown = plan.to_markdown()
        self.renderer.console.print(Markdown(plan_markdown))
        self.messages.append(Message(role="assistant", content=plan_markdown))
        self.last_execution_plan = plan
        return plan

    async def run(self, user_input: str, *, force_plan: bool = False) -> str | None:
        """运行 ReAct 循环处理用户输入。

        Args:
            user_input: 用户输入文本。
            force_plan: 是否强制本轮先生成计划，供后续命令扩展复用。

        Returns:
            最终回复文本。若过程中出现无法恢复的错误则返回 None。
        """
        history_len = len(self.messages)  # 记录初始长度，用于错误回滚
        self.messages.append(Message(role="user", content=user_input))
        self.last_execution_plan = None

        try:
            if force_plan or self.config.agent.planning.enabled:
                await self._create_execution_plan(user_input)
        except ProviderError as e:
            logger.debug("规划阶段失败", error=str(e))
            self.renderer.show_error(f"计划生成失败：{e}")
            del self.messages[history_len:]
            self.last_execution_plan = None
            return None

        logger.debug("AgentLoop 开始", max_rounds=self.config.agent.max_rounds)

        for round_num in range(1, self.config.agent.max_rounds + 1):
            logger.debug("ReAct 轮次", round=round_num)

            # 构建 API 消息（system + history），含上下文预算控制
            context_result = build_messages(
                self.messages,
                self.system_prompt,
                self.config.agent.context,
            )
            self.last_context_report = context_result.report
            api_messages = context_result.messages

            # 调用 Provider
            tools_schema = self.tool_registry.get_tools_schema()
            # memory 禁用时从 tools schema 中过滤掉 remember
            if not self._memory_enabled:
                tools_schema = [
                    t for t in tools_schema
                    if t.get("function", {}).get("name") != "remember"
                ]

            try:
                stream = self.provider.chat(
                    messages=api_messages,
                    tools=tools_schema,
                    stream=self.config.agent.stream,
                    max_tokens=self.config.max_tokens,
                )
                # 兼容 chat() 直接返回协程并抛出 ProviderError 的场景
                if inspect.iscoroutine(stream):
                    stream = await stream
            except ProviderError as e:
                logger.debug("Provider 调用失败", round=round_num, error=str(e))
                self.renderer.show_error(f"{e}")
                del self.messages[history_len:]  # 回滚本轮所有消息
                return None

            # 处理流式响应：渲染文本 + 收集 tool_call
            try:
                text_content, tool_calls, usage = await self._process_stream(stream)
            except ProviderError as e:
                logger.debug("Provider 流式处理失败", round=round_num, error=str(e))
                self.renderer.show_error(f"{e}")
                del self.messages[history_len:]  # 回滚本轮所有消息
                return None

            # 如果流式处理中发生错误
            if text_content is None and tool_calls is None:
                del self.messages[history_len:]  # 回滚本轮所有消息
                return None

            # 构建 assistant 消息并追加到历史
            assistant_msg = Message(
                role="assistant",
                content=text_content,
                tool_calls=tool_calls or None,
            )
            self.messages.append(assistant_msg)

            # 显示 token 用量
            if usage:
                self.renderer.show_usage(usage)

            # 有工具调用：执行工具后继续循环
            if tool_calls:
                await self._execute_tools(tool_calls)

                if round_num >= self.config.agent.max_rounds:
                    self.renderer.show_error(
                        f"已到达最大推理轮次（{self.config.agent.max_rounds}），"
                        "回复可能不完整。"
                    )
                    # 仍然返回已有的文本内容
                    return text_content

                # 继续下一轮
                continue

            # 无工具调用：纯文本回复，结束循环
            logger.debug("AgentLoop 完成", rounds=round_num)
            return text_content

        # 超出 max_rounds 的兜底
        self.renderer.show_error("推理轮次已达上限，回复已截断。")
        return None

    async def _process_stream(
        self,
        stream: AsyncIterator[StreamChunk],
    ) -> tuple[str | None, list[ToolCall] | None, UsageInfo | None]:
        """处理 Provider 流式响应。

        实时渲染文本 delta，同时收集 tool_call delta，
        流结束后组装完整的 ToolCall 列表。

        Args:
            stream: Provider 返回的流式响应。

        Returns:
            (text_content, tool_calls, usage)：
            - text_content: 完整文本回复（无文本时为 None）
            - tool_calls: 完整工具调用列表（无工具调用时为 None）
            - usage: token 用量信息
            返回 (None, None, None) 表示发生错误。
        """
        text_buffer = ""
        tool_call_deltas: list[StreamChunk] = []
        final_usage: UsageInfo | None = None
        has_text = False

        error_text: str | None = None

        # Collect the stream while showing a lightweight status indicator.
        # Rendering once avoids duplicate frames in terminals that do not repaint Live cleanly.
        with self.renderer.console.status(Text("正在思考...", style="dim"), spinner="dots"):
            async for chunk in stream:
                if chunk.type == "text_delta" and chunk.text:
                    text_buffer += chunk.text
                    has_text = True

                elif chunk.type == "tool_call_delta" and chunk.tool_call is not None:
                    tool_call_deltas.append(chunk)

                elif chunk.type == "done":
                    final_usage = chunk.usage
                    break

                elif chunk.type == "error":
                    logger.debug("流式响应错误", error=chunk.text)
                    error_text = chunk.text or "模型响应出错。"
                    break

        if error_text is not None:
            self.renderer.show_error(error_text)
            return None, None, None

        if not has_text and not tool_call_deltas:
            return None, None, final_usage

        # 组装工具调用
        tool_calls = self._assemble_tool_calls(tool_call_deltas) if tool_call_deltas else None

        text_content = text_buffer if has_text else None
        if text_content:
            self.renderer.console.print(Markdown(text_content))
        return text_content, tool_calls, final_usage

    @staticmethod
    def _assemble_tool_calls(
        deltas: list[StreamChunk],
    ) -> list[ToolCall]:
        """将 tool_call delta 流组装为完整的 ToolCall 列表。

        OpenAI 流式 API 将每个工具调用的信息分散到多个 chunk 中：
        - id 通常只在第一个 chunk 出现
        - name 通常只在第一个 chunk 出现
        - arguments 是逐步累积的 JSON 片段

        按 index 分组后合并各字段。

        Args:
            deltas: 包含 tool_call delta 的 StreamChunk 列表。

        Returns:
            完整的 ToolCall 列表。
        """
        assembled: dict[int, dict[str, str | None]] = defaultdict(
            lambda: {"id": None, "name": None, "arguments": ""}
        )

        for chunk in deltas:
            if chunk.tool_call is None:
                continue
            tc = chunk.tool_call
            entry = assembled[tc.index]
            if tc.id:
                entry["id"] = tc.id
            if tc.name:
                entry["name"] = tc.name
            if tc.arguments:
                entry["arguments"] = (entry["arguments"] or "") + tc.arguments

        tool_calls: list[ToolCall] = []
        for index in sorted(assembled.keys()):
            entry = assembled[index]
            tool_calls.append(
                ToolCall(
                    id=entry["id"] or f"call_{index}",
                    function=FunctionCall(
                        name=entry["name"] or "",
                        arguments=entry["arguments"] or "",
                    ),
                )
            )

        return tool_calls

    async def _execute_tools(self, tool_calls: list[ToolCall]) -> None:
        """串行执行工具调用，并将结果追加到对话历史。

        在工具执行前进行权限检查：
        - safe 级别直接执行
        - deny 级别不执行，追加拒绝信息
        - caution/dangerous 级别根据 trust_mode、store、confirmer 决策

        Args:
            tool_calls: 模型返回的工具调用列表。
        """
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError as e:
                logger.debug("工具参数解析失败", tool=name, error=str(e))
                self.messages.append(
                    ToolMessage(
                        content=f"参数解析错误：{e}",
                        tool_call_id=tc.id,
                        name=name,
                    )
                )
                continue

            # 权限检查：工具执行前调用 check_permission()
            if await self._check_tool_permission(name, args, tc):
                # 返回 True 表示已拒绝，跳过执行
                continue

            # memory 禁用时拒绝 remember 工具（防御层）
            if name == "remember" and not self._memory_enabled:
                logger.debug("记忆系统已禁用，拒绝 remember 工具调用")
                self.messages.append(
                    ToolMessage(
                        content="记忆系统已禁用，无法保存记忆。",
                        tool_call_id=tc.id,
                        name=name,
                    )
                )
                continue

            logger.debug("执行工具", tool=name, args=args)
            self.renderer.console.print(
                Text(f"\n── 正在调用工具：{name} ──", style="dim")
            )

            result = await self.tool_registry.execute_tool(
                name=name,
                args=args,
                workspace_root=self.workspace_root,
            )

            log_msg = f"工具 '{name}' 执行{'成功' if result.success else '失败'}"
            logger.debug(log_msg, output_length=len(result.output))

            if result.success:
                self.renderer.show_info(f"工具执行成功（{len(result.output)} 字符）")
            else:
                error_detail = result.error or result.output
                self.renderer.show_error(f"工具执行失败：{name} — {error_detail[:200]}")

            # remember 工具成功执行后刷新系统提示词
            if name == "remember" and result.success and self._memory_enabled:
                self.reload_memory()

            self.messages.append(
                ToolMessage(
                    content=result.output,
                    tool_call_id=tc.id,
                    name=name,
                )
            )

    async def _check_tool_permission(
        self,
        tool_name: str,
        arguments: dict[str, object],
        tc: ToolCall,
    ) -> bool:
        """检查工具权限。返回 True 表示已拒绝（跳过执行）。

        - safe：允许执行
        - deny：拒绝执行，追加拒绝 ToolMessage
        - caution/dangerous：
          - trust_mode → 允许执行
          - store 有匹配 → 允许执行
          - confirmer 可用 → 询问用户
          - 无 confirmer → 允许执行（向后兼容）

        Args:
            tool_name: 工具名称
            arguments: 工具参数
            tc: 原始 ToolCall（用于构造拒绝消息）

        Returns:
            True 表示不应执行工具，False 表示可以执行。
        """
        decision = check_permission(
            tool_name=tool_name,
            arguments=arguments,
            workspace_root=self.workspace_root,
            trust_mode=self.config.permissions.trust_mode,
        )

        # safe：直接执行
        if decision.allowed_without_prompt:
            return False

        # deny：不执行，追加拒绝 ToolMessage
        if decision.denied:
            self.renderer.show_error(f"权限拒绝：{decision.summary}")
            self.messages.append(
                ToolMessage(
                    content=f"权限拒绝：{decision.summary}",
                    tool_call_id=tc.id,
                    name=tool_name,
                )
            )
            return True

        # caution / dangerous：需要确认
        # trust_mode 跳过确认
        if self.config.permissions.trust_mode:
            return False

        # 检查 always-allow 存储
        if self.permission_store is not None and decision.target_paths:  # noqa: SIM102
            if self.permission_store.find_match(tool_name, decision.target_paths):
                return False

        # 询问用户
        if self.permission_confirmer is not None:
            result = await self.permission_confirmer.confirm(decision)

            if result.action == "deny":
                self.renderer.show_error(f"用户拒绝：{decision.summary}")
                self.messages.append(
                    ToolMessage(
                        content=f"用户拒绝：{decision.summary}",
                        tool_call_id=tc.id,
                        name=tool_name,
                    )
                )
                return True

            if result.action == "always_allow":
                # 将第一个目标路径转为 workspace 相对路径作为 pattern
                if self.permission_store is not None and decision.target_paths:
                    try:
                        rel = decision.target_paths[0].relative_to(
                            self.workspace_root
                        )
                        pattern = rel.as_posix()
                        self.permission_store.add_rule(tool_name, pattern)
                        self.renderer.show_info(
                            f"已添加规则：{tool_name} 允许 {pattern}"
                        )
                    except ValueError:
                        pass
                return False

            if result.action == "allow":
                # 允许本次执行
                return False

            # 未知 action → 拒绝执行（fail-close）
            self.renderer.show_error(f"权限确认返回值未知：{result.action}")
            self.messages.append(
                ToolMessage(
                    content=f"权限拒绝：确认返回值未知（{result.action}）",
                    tool_call_id=tc.id,
                    name=tool_name,
                )
            )
            return True

        # 没有 confirmer 但有确认需求 → 拒绝执行，防止 fail-open
        self.renderer.show_error(f"权限拒绝（无确认交互）：{decision.summary}")
        self.messages.append(
            ToolMessage(
                content=f"权限拒绝：{decision.summary}",
                tool_call_id=tc.id,
                name=tool_name,
            )
        )
        return True
