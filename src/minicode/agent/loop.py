"""ReAct Agent Loop — 模型推理与工具调用的闭环。

实现 ReAct（Reasoning + Acting）模式：
1. 调用模型（携带工具 schema）
2. 流式渲染文本回复
3. 收集 tool_call，分块并发/串行执行工具
4. 将工具结果追加回对话
5. 循环直到模型返回纯文本回复或达到最大轮次

工具执行优化策略：
- 读工具（read_file, grep, glob 等）：相邻的分到同一块，块内并发执行（上限3）
- 写工具（write_file, bash 等）：独占一块，串行执行
- 块间串行执行，保持工具调用的整体顺序
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markdown import Markdown
from rich.text import Text

from minicode.agent.compaction import ContextCompactor, format_compaction_report
from minicode.agent.context import build_strict_messages, estimate_context_usage
from minicode.agent.context_models import (
    CompactionReport,
    CompactionResult,
    CompactionTrigger,
    ContextUsageReport,
)
from minicode.agent.planner import PLANNING_SYSTEM_PROMPT, TaskPlanner
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
from minicode.utils.exceptions import (
    ContextCompactionError,
    ContextWindowExceededError,
    ProviderError,
)
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


class ToolCategory(Enum):
    """工具类别。"""

    READ = "read"  # 只读工具，可并发执行
    WRITE = "write"  # 写工具，必须串行执行


@dataclass
class ToolBlock:
    """工具执行块。

    将工具调用按读/写分块，块内根据类别决定并发或串行执行。
    """

    category: ToolCategory
    tool_calls: list[ToolCall]


@dataclass
class AgentTaskSnapshot:
    """单次任务开始前需要事务保护的 Agent 状态。"""

    messages: list[Message]
    last_context_report: ContextUsageReport | None
    last_compaction_report: CompactionReport | None
    compaction_count: int
    last_execution_plan: ExecutionPlan | None


class AgentLoop:
    """ReAct Agent Loop。

    管理完整对话上下文，负责：
    - 构建 messages（system + history + 最新工具结果）
    - 调用 provider.chat() 并流式渲染
    - 收集并组装 tool_call delta
    - 分块并发/串行执行工具，将结果追加回上下文
    - 循环直到模型返回纯文本或达到最大轮次

    用法：
        loop = AgentLoop(provider, registry, renderer, config)
        await loop.run("请读取 README.md 并总结")
    """

    # 只读工具集合（可并发执行）
    READ_TOOLS: set[str] = {
        "read_file",
        "grep",
        "glob",
        "list_directory",
    }
    # 其余工具视为写工具，必须串行执行
    # 包括: write_file, edit_file, bash, remember, forget, run_subagent

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
        self.context_compactor = ContextCompactor(
            provider=self.provider,
            context_config=self.config.agent.context,
        )
        self.last_context_report: ContextUsageReport | None = None
        self.last_compaction_report: CompactionReport | None = None
        self.compaction_count = 0
        self.last_execution_plan: ExecutionPlan | None = None
        self._subagents_started_this_run = 0
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

    def _take_task_snapshot(self) -> AgentTaskSnapshot:
        """深拷贝任务级状态，隔离执行期间的原地修改。"""
        return AgentTaskSnapshot(
            messages=[
                message.model_copy(deep=True) for message in self.messages
            ],
            last_context_report=(
                self.last_context_report.model_copy(deep=True)
                if self.last_context_report is not None
                else None
            ),
            last_compaction_report=(
                self.last_compaction_report.model_copy(deep=True)
                if self.last_compaction_report is not None
                else None
            ),
            compaction_count=self.compaction_count,
            last_execution_plan=(
                self.last_execution_plan.model_copy(deep=True)
                if self.last_execution_plan is not None
                else None
            ),
        )

    def _restore_task_snapshot(self, snapshot: AgentTaskSnapshot) -> None:
        """从深快照恢复状态，且不向后续任务暴露快照内部对象。"""
        self.messages.clear()
        self.messages.extend(
            message.model_copy(deep=True) for message in snapshot.messages
        )
        self.last_context_report = (
            snapshot.last_context_report.model_copy(deep=True)
            if snapshot.last_context_report is not None
            else None
        )
        self.last_compaction_report = (
            snapshot.last_compaction_report.model_copy(deep=True)
            if snapshot.last_compaction_report is not None
            else None
        )
        self.compaction_count = snapshot.compaction_count
        self.last_execution_plan = (
            snapshot.last_execution_plan.model_copy(deep=True)
            if snapshot.last_execution_plan is not None
            else None
        )

    @staticmethod
    def _unconsumed_tool_ids(messages: list[Message]) -> set[str]:
        """返回消息中尚未被主模型消费的工具调用 ID。"""
        return {
            message.tool_call_id
            for message in messages
            if isinstance(message, ToolMessage)
            and not message.consumed_by_main_model
        }

    def _mark_tool_results_consumed(self, tool_call_ids: set[str]) -> None:
        """确认当前历史中指定 ID 的工具结果已被主模型消费。"""
        for message in self.messages:
            if (
                isinstance(message, ToolMessage)
                and message.tool_call_id in tool_call_ids
            ):
                message.consumed_by_main_model = True

    def _get_tools_schema(self) -> list[dict]:
        """获取主 Agent 工具 schema，并按记忆配置过滤 remember。"""
        tools_schema = self.tool_registry.get_tools_schema()
        if self._memory_enabled:
            return tools_schema
        return [
            tool
            for tool in tools_schema
            if tool.get("function", {}).get("name") != "remember"
        ]

    def get_context_usage(self) -> ContextUsageReport:
        """返回当前 Agent 历史的实时上下文用量估算。"""
        tools_schema = self._get_tools_schema()
        return estimate_context_usage(
            messages=self.messages,
            system_prompt=self.system_prompt,
            tools_schema=tools_schema,
            max_input_tokens=self.config.agent.context.max_input_tokens,
        )

    async def compact_context(
        self,
        focus: str | None = None,
    ) -> CompactionResult:
        """手动压缩当前旧上下文，并在成功时提交压缩状态。"""
        tools_schema = self._get_tools_schema()
        result = await self.context_compactor.compact(
            messages=self.messages,
            system_prompt=self.system_prompt,
            tools_schema=tools_schema,
            trigger=CompactionTrigger.MANUAL,
            focus=focus,
        )
        if not result.changed:
            return result

        # 压缩器已完成候选校验；保留列表对象，避免破坏外部状态引用。
        self.messages.clear()
        self.messages.extend(result.messages)
        self.last_compaction_report = result.report
        self.compaction_count += 1
        strict = build_strict_messages(
            self.messages,
            self.system_prompt,
            tools_schema,
            self.config.agent.context,
        )
        self.last_context_report = strict.report
        return result

    async def _prepare_main_call(
        self,
        system_prompt: str,
        tools_schema: list[dict],
    ) -> list[Message]:
        """在主模型调用前按占用率自动压缩，并构建严格上下文。"""
        context_config = self.config.agent.context
        usage = estimate_context_usage(
            self.messages,
            system_prompt,
            tools_schema,
            context_config.max_input_tokens,
        )
        compaction_config = context_config.compaction
        if (
            compaction_config.auto_enabled
            and usage.occupancy_ratio >= compaction_config.trigger_ratio
        ):
            result = await self.context_compactor.compact(
                self.messages,
                system_prompt,
                tools_schema,
                trigger=CompactionTrigger.AUTOMATIC,
            )
            if result.changed:
                # 压缩器先完成候选构建与校验；这里只原子替换已确认的结果。
                self.messages.clear()
                self.messages.extend(result.messages)
                self.last_compaction_report = result.report
                self.compaction_count += 1
                if result.report is not None:
                    self.renderer.show_info(
                        format_compaction_report(result.report)
                    )

        strict = build_strict_messages(
            self.messages,
            system_prompt,
            tools_schema,
            context_config,
        )
        self.last_context_report = strict.report
        return strict.messages

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
        api_messages = await self._prepare_main_call(
            PLANNING_SYSTEM_PROMPT,
            [],
        )
        sent_unconsumed_tool_ids = self._unconsumed_tool_ids(api_messages)
        planner = TaskPlanner(
            provider=self.provider,
            planning_config=self.config.agent.planning,
            stream=self.config.agent.stream,
        )
        plan = await planner.create_plan(
            api_messages=api_messages,
            user_input=user_input,
            max_tokens=self.config.max_tokens,
        )
        plan_markdown = plan.to_markdown()
        self.renderer.console.print(Markdown(plan_markdown))
        self.messages.append(Message(role="assistant", content=plan_markdown))
        self._mark_tool_results_consumed(sent_unconsumed_tool_ids)
        self.last_execution_plan = plan
        return plan

    async def run(self, user_input: str, *, force_plan: bool = False) -> str | None:
        """以任务级事务运行 ReAct 循环。

        Args:
            user_input: 用户输入文本。
            force_plan: 是否强制本轮先生成计划，供后续命令扩展复用。

        Returns:
            最终回复文本。若过程中出现无法恢复的错误则返回 None。
        """
        snapshot = self._take_task_snapshot()
        self.messages.append(Message(role="user", content=user_input))
        self.last_execution_plan = None
        self._subagents_started_this_run = 0

        try:
            result = await self._run_task(user_input, force_plan=force_plan)
        except asyncio.CancelledError:
            self._restore_task_snapshot(snapshot)
            raise
        except ProviderError as e:
            self._restore_task_snapshot(snapshot)
            logger.debug("Provider 调用失败", error=str(e))
            self.renderer.show_error(f"{e}")
            return None
        except Exception as e:
            self._restore_task_snapshot(snapshot)
            logger.debug("任务执行失败", error=str(e), exc_info=True)
            self.renderer.show_error(f"任务执行失败：{e}")
            return None

        if result is None:
            self._restore_task_snapshot(snapshot)
        return result

    async def _run_task(
        self,
        user_input: str,
        *,
        force_plan: bool = False,
    ) -> str | None:
        """执行已进入事务的规划与 ReAct 主体。"""
        try:
            if force_plan or self.config.agent.planning.enabled:
                await self._create_execution_plan(user_input)
        except (ContextCompactionError, ContextWindowExceededError) as e:
            logger.debug("规划上下文准备失败", error=str(e))
            self.renderer.show_error(f"规划上下文准备失败：{e}")
            return None
        except ProviderError as e:
            logger.debug("规划阶段失败", error=str(e))
            self.renderer.show_error(f"计划生成失败：{e}")
            return None

        logger.debug("AgentLoop 开始", max_rounds=self.config.agent.max_rounds)

        for round_num in range(1, self.config.agent.max_rounds + 1):
            logger.debug("ReAct 轮次", round=round_num)

            try:
                tools_schema = self._get_tools_schema()
                api_messages = await self._prepare_main_call(
                    self.system_prompt,
                    tools_schema,
                )
            except (ContextCompactionError, ContextWindowExceededError) as e:
                logger.debug("ReAct 上下文准备失败", round=round_num, error=str(e))
                self.renderer.show_error(f"上下文准备失败：{e}")
                return None

            sent_unconsumed_tool_ids = self._unconsumed_tool_ids(api_messages)

            # 调用 Provider
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
                return None

            # 处理流式响应：渲染文本 + 收集 tool_call
            try:
                text_content, tool_calls, usage = await self._process_stream(stream)
            except ProviderError as e:
                logger.debug("Provider 流式处理失败", round=round_num, error=str(e))
                self.renderer.show_error(f"{e}")
                return None

            # 如果流式处理中发生错误
            if text_content is None and tool_calls is None:
                return None

            # 构建 assistant 消息并追加到历史
            assistant_msg = Message(
                role="assistant",
                content=text_content,
                tool_calls=tool_calls or None,
            )
            self.messages.append(assistant_msg)
            self._mark_tool_results_consumed(sent_unconsumed_tool_ids)

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

    def _partition_tool_calls(self, tool_calls: list[ToolCall]) -> list[ToolBlock]:
        """将工具调用列表按读/写分块。

        规则：
        1. 相邻的读工具分到同一块
        2. 每个普通写工具独占一块
        3. 连续的 run_subagent 合并到同一块（保持现有批量并发逻辑）
        4. 保持原始调用顺序

        Args:
            tool_calls: 原始工具调用列表

        Returns:
            分块后的 ToolBlock 列表
        """
        if not tool_calls:
            return []

        blocks: list[ToolBlock] = []
        current_read_batch: list[ToolCall] = []
        current_subagent_batch: list[ToolCall] = []

        for tc in tool_calls:
            tool_name = tc.function.name

            if tool_name in self.READ_TOOLS:
                # 读工具：先提交 subagent 批次，然后加入读批次
                if current_subagent_batch:
                    blocks.append(
                        ToolBlock(category=ToolCategory.WRITE, tool_calls=current_subagent_batch)
                    )
                    current_subagent_batch = []
                current_read_batch.append(tc)

            elif tool_name == "run_subagent":
                # run_subagent：先提交读批次，然后加入 subagent 批次
                if current_read_batch:
                    blocks.append(
                        ToolBlock(category=ToolCategory.READ, tool_calls=current_read_batch)
                    )
                    current_read_batch = []
                current_subagent_batch.append(tc)

            else:
                # 普通写工具：先提交读批次和 subagent 批次，然后独占一块
                if current_read_batch:
                    blocks.append(
                        ToolBlock(category=ToolCategory.READ, tool_calls=current_read_batch)
                    )
                    current_read_batch = []
                if current_subagent_batch:
                    blocks.append(
                        ToolBlock(category=ToolCategory.WRITE, tool_calls=current_subagent_batch)
                    )
                    current_subagent_batch = []

                blocks.append(ToolBlock(category=ToolCategory.WRITE, tool_calls=[tc]))

        # 处理末尾的批次
        if current_read_batch:
            blocks.append(
                ToolBlock(category=ToolCategory.READ, tool_calls=current_read_batch)
            )
        if current_subagent_batch:
            blocks.append(
                ToolBlock(category=ToolCategory.WRITE, tool_calls=current_subagent_batch)
            )

        return blocks

    async def _execute_tools(self, tool_calls: list[ToolCall]) -> None:
        """按分块策略执行工具调用。

        - 读工具块：并发执行（上限3）
        - 写工具块（普通写工具）：串行执行
        - 写工具块（run_subagent 批次）：批量并发执行
        - 块间：串行执行
        """
        blocks = self._partition_tool_calls(tool_calls)

        for block_idx, block in enumerate(blocks):
            logger.debug(
                "执行工具块",
                block_index=block_idx + 1,
                total_blocks=len(blocks),
                category=block.category.value,
                count=len(block.tool_calls),
            )

            if block.category == ToolCategory.READ:
                # 读工具块：并发执行
                await self._execute_read_block(block.tool_calls)
            else:
                # 写工具块：检查是否全是 run_subagent
                if all(tc.function.name == "run_subagent" for tc in block.tool_calls):
                    # 批量并发执行 run_subagent
                    await self._execute_subagent_batch(block.tool_calls)
                else:
                    # 普通写工具：逐个串行执行
                    for tc in block.tool_calls:
                        await self._execute_single_tool(tc)

    async def _execute_read_block(self, tool_calls: list[ToolCall]) -> None:
        """并发执行一组读工具调用。

        - 并发上限为 3
        - 按原始顺序追加 ToolMessage 到 self.messages
        - 即使某个工具失败，其他工具仍继续执行
        - 处理权限检查（虽然读工具通常是 safe）

        Args:
            tool_calls: 读工具调用列表
        """
        if not tool_calls:
            return

        # 并发上限为 3
        semaphore = asyncio.Semaphore(3)

        async def _execute_one(index: int, tc: ToolCall) -> tuple[int, ToolMessage]:
            """执行单个读工具，返回 (原始索引, ToolMessage)。"""
            async with semaphore:
                name = tc.function.name

                # 解析参数
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError as e:
                    logger.debug("工具参数解析失败", tool=name, error=str(e))
                    return (
                        index,
                        ToolMessage(
                            content=f"参数解析错误：{e}",
                            tool_call_id=tc.id,
                            name=name,
                        ),
                    )

                # 权限检查
                decision = check_permission(
                    tool_name=name,
                    arguments=args,
                    workspace_root=self.workspace_root,
                    trust_mode=self.config.permissions.trust_mode,
                )

                # denied：拒绝执行
                if decision.denied:
                    logger.debug("工具权限拒绝", tool=name, reason=decision.summary)
                    return (
                        index,
                        ToolMessage(
                            content=f"权限拒绝：{decision.summary}",
                            tool_call_id=tc.id,
                            name=name,
                        ),
                    )

                # requires_confirmation：需要用户确认
                if decision.requires_confirmation:
                    # trust_mode 跳过确认
                    if self.config.permissions.trust_mode:
                        pass  # 允许执行
                    else:
                        # 检查 always-allow 存储
                        store_matched = False
                        if self.permission_store is not None and decision.target_paths:
                            store_matched = self.permission_store.find_match(
                                name, decision.target_paths
                            )

                        if not store_matched:
                            # 需要 confirmer
                            if self.permission_confirmer is None:
                                # 无 confirmer → 拒绝执行
                                logger.debug("工具权限需要确认但无 confirmer", tool=name)
                                return (
                                    index,
                                    ToolMessage(
                                        content=f"权限拒绝：{decision.summary}",
                                        tool_call_id=tc.id,
                                        name=name,
                                    ),
                                )
                            else:
                                # 有 confirmer → 询问用户（注意：这会在并发中阻塞）
                                confirmation = await self.permission_confirmer.confirm(decision)

                                if confirmation.action == "deny":
                                    logger.debug("用户拒绝工具执行", tool=name)
                                    return (
                                        index,
                                        ToolMessage(
                                            content=f"用户拒绝：{decision.summary}",
                                            tool_call_id=tc.id,
                                            name=name,
                                        ),
                                    )

                                if (
                                    confirmation.action == "always_allow"
                                    and self.permission_store is not None
                                    and decision.target_paths
                                ):
                                    try:
                                        rel = decision.target_paths[0].relative_to(
                                            self.workspace_root
                                        )
                                        pattern = rel.as_posix()
                                        self.permission_store.add_rule(name, pattern)
                                    except ValueError:
                                        pass
                                # allow 或 always_allow 都继续执行

                # 执行工具
                logger.debug("并发执行读工具", tool=name, args=args)
                try:
                    tool_result = await self.tool_registry.execute_tool(
                        name=name,
                        args=args,
                        workspace_root=self.workspace_root,
                    )
                except Exception as e:
                    logger.debug("工具执行异常", tool=name, error=str(e), exc_info=True)
                    return (
                        index,
                        ToolMessage(
                            content=f"工具执行失败：{e}",
                            tool_call_id=tc.id,
                            name=name,
                        ),
                    )

                # 构造 ToolMessage
                if not tool_result.success:
                    error_detail = tool_result.error or tool_result.output
                    logger.debug("工具执行失败", tool=name, error=error_detail[:100])

                return (
                    index,
                    ToolMessage(
                        content=tool_result.output,
                        tool_call_id=tc.id,
                        name=name,
                    ),
                )

        # 并发执行所有读工具
        if len(tool_calls) > 1:
            self.renderer.console.print(
                Text(f"\n── 正在并发执行 {len(tool_calls)} 个读工具 ──", style="dim")
            )
        else:
            self.renderer.console.print(
                Text(f"\n── 正在调用工具：{tool_calls[0].function.name} ──", style="dim")
            )

        results = await asyncio.gather(
            *(_execute_one(idx, tc) for idx, tc in enumerate(tool_calls))
        )

        # 按原始顺序排序并追加到 messages
        results_sorted = sorted(results, key=lambda x: x[0])
        for _, tool_msg in results_sorted:
            self.messages.append(tool_msg)

        # 显示执行完成信息
        success_count = 0
        for _, message in results_sorted:
            content = message.content or ""
            if not content.startswith(
                ("参数解析错误", "权限拒绝", "工具执行失败")
            ):
                success_count += 1
        if len(tool_calls) > 1:
            self.renderer.show_info(f"读工具执行完成：{success_count}/{len(tool_calls)} 成功")
        else:
            result_content = results_sorted[0][1].content or ""
            if success_count == 1:
                self.renderer.show_info(
                    f"工具执行成功（{len(result_content)} 字符）"
                )
            else:
                self.renderer.show_error(
                    f"工具执行失败：{tool_calls[0].function.name} — "
                    f"{result_content[:200]}"
                )

    async def _execute_subagent_batch(self, tool_calls: list[ToolCall]) -> None:
        """并行执行一组连续的 run_subagent 调用，按原始顺序追加 ToolMessage。"""
        if not tool_calls:
            return

        subagent_config = self.config.agent.subagents
        remaining_agents = subagent_config.max_agents - self._subagents_started_this_run
        if len(tool_calls) > remaining_agents:
            error = (
                f"本轮最多允许启动 {subagent_config.max_agents} 个子代理，"
                f"当前请求 {len(tool_calls)} 个，剩余额度 {max(remaining_agents, 0)} 个。"
            )
            self.renderer.show_error(error)
            self.messages.extend(
                ToolMessage(content=error, tool_call_id=tc.id, name=tc.function.name)
                for tc in tool_calls
            )
            return
        self._subagents_started_this_run += len(tool_calls)

        self.renderer.console.print(Text("\n── 正在并行启动子代理 ──", style="dim"))
        semaphore = asyncio.Semaphore(subagent_config.concurrency)

        async def _run(tc: ToolCall) -> ToolMessage:
            async with semaphore:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError as e:
                    logger.debug("子代理参数解析失败", tool=name, error=str(e))
                    return ToolMessage(
                        content=f"参数解析错误：{e}",
                        tool_call_id=tc.id,
                        name=name,
                    )

                try:
                    result = await self.tool_registry.execute_tool(
                        name=name,
                        args=args,
                        workspace_root=self.workspace_root,
                    )
                except Exception as e:
                    logger.debug("子代理工具执行异常", tool=name, error=str(e), exc_info=True)
                    return ToolMessage(
                        content=f"子代理执行失败：{e}",
                        tool_call_id=tc.id,
                        name=name,
                    )

                if result.success:
                    self.renderer.show_info(f"子代理执行完成（{len(result.output)} 字符）")
                else:
                    error_detail = result.error or result.output
                    self.renderer.show_error(f"子代理执行失败：{error_detail[:200]}")
                return ToolMessage(
                    content=result.output,
                    tool_call_id=tc.id,
                    name=name,
                )

        messages = await asyncio.gather(*(_run(tc) for tc in tool_calls))
        self.messages.extend(messages)

    async def _execute_single_tool(self, tc: ToolCall) -> None:
        """按原有串行语义执行一个普通工具调用。"""
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
            return

        # 权限检查：工具执行前调用 check_permission()
        if await self._check_tool_permission(name, args, tc):
            return

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
            return

        logger.debug("执行工具", tool=name, args=args)
        self.renderer.console.print(Text(f"\n── 正在调用工具：{name} ──", style="dim"))

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
        if tool_name == "run_subagent":
            return False

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
