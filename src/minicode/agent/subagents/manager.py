"""Subagent 并行调度与运行记录保存。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from minicode.agent.context import _compress_text
from minicode.agent.subagents.models import SubagentResult, SubagentRunRecord, SubagentTask
from minicode.agent.subagents.runner import SubagentRunner
from minicode.agent.subagents.tool_filter import resolve_allowed_tools
from minicode.providers.base import Message
from minicode.utils.exceptions import ToolError

if TYPE_CHECKING:
    from minicode.cli.confirm import ConfirmerResult, PermissionConfirmer
    from minicode.cli.renderer import StreamingRenderer
    from minicode.config.models import AppConfig
    from minicode.permissions.models import PermissionDecision
    from minicode.permissions.store import PermissionStore
    from minicode.providers.base import BaseProvider
    from minicode.tools.registry import ToolRegistry


class PermissionPromptQueue:
    """串行化 subagent 的权限确认提示。"""

    def __init__(self, confirmer: PermissionConfirmer, renderer: StreamingRenderer | None) -> None:
        self._confirmer = confirmer
        self._renderer = renderer
        self._lock = asyncio.Lock()

    async def confirm(
        self,
        decision: PermissionDecision,
        task: SubagentTask,
    ) -> ConfirmerResult:
        """排队显示确认提示，避免多个 subagent 同时抢占输入。"""
        async with self._lock:
            if self._renderer is not None:
                self._renderer.show_info(
                    f"子代理「{task.name}」请求权限：{decision.operation}"
                )
                self._renderer.show_info(f"任务：{task.task[:120]}")
            return await self._confirmer.confirm(decision)


class SubagentManager:
    """创建、并行运行和记录 subagent。"""

    def __init__(
        self,
        *,
        provider: BaseProvider,
        parent_registry: ToolRegistry,
        config: AppConfig,
        workspace_root: Path,
        permission_store: PermissionStore | None = None,
        permission_confirmer: PermissionConfirmer | None = None,
        renderer: StreamingRenderer | None = None,
    ) -> None:
        self.provider = provider
        self.parent_registry = parent_registry
        self.config = config
        self.workspace_root = workspace_root
        self.permission_store = permission_store
        self.renderer = renderer
        self.permission_queue = (
            PermissionPromptQueue(permission_confirmer, renderer)
            if permission_confirmer is not None
            else None
        )
        self._completed_counter = 0

    async def run_many(self, tasks: list[SubagentTask]) -> list[SubagentResult]:
        """并行运行多个 subagent，并按启动顺序返回结果。"""
        if len(tasks) > self.config.agent.subagents.max_agents:
            raise ToolError(
                f"本轮最多允许启动 {self.config.agent.subagents.max_agents} 个子代理，"
                f"当前请求 {len(tasks)} 个。"
            )

        semaphore = asyncio.Semaphore(self.config.agent.subagents.concurrency)
        results: list[SubagentResult | None] = [None] * len(tasks)

        async def _run_one(index: int, task: SubagentTask) -> None:
            async with semaphore:
                results[index] = await self._run_one(task, index)

        await asyncio.gather(*(_run_one(index, task) for index, task in enumerate(tasks)))
        return [result for result in results if result is not None]

    async def _run_one(self, task: SubagentTask, started_order: int) -> SubagentResult:
        run_id = f"sub_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
        created_at = datetime.now(UTC)
        allowed_tools: list[str] = []
        record: SubagentRunRecord | None = None
        runner: SubagentRunner | None = None

        try:
            allowed_tools = resolve_allowed_tools(
                requested_tools=task.allowed_tools,
                role=task.role,
                config=self.config.agent.subagents,
                available_tools=self.parent_registry.tool_names,
            )
            scoped_registry = self.parent_registry.scope(allowed_tools)
            runner = SubagentRunner(
                provider=self.provider,
                tool_registry=scoped_registry,
                workspace_root=self.workspace_root,
                app_config=self.config,
                subagent_config=self.config.agent.subagents,
                allowed_tools=allowed_tools,
                permission_store=self.permission_store,
                permission_queue=self.permission_queue,
            )
            if self.renderer is not None:
                self.renderer.show_info(f"正在启动子代理：{task.name}")
            result = await runner.run(task, run_id)
        except Exception as e:
            result = SubagentResult(
                run_id=run_id,
                name=task.name,
                role=task.role,
                status="failed",
                summary=f"子代理「{task.name}」执行失败。",
                errors=[str(e)],
            )

        self._completed_counter += 1
        record = SubagentRunRecord(
            run_id=run_id,
            name=task.name,
            role=task.role,
            task=task.task,
            status=result.status,
            created_at=created_at,
            completed_at=datetime.now(UTC),
            allowed_tools=allowed_tools,
            started_order=started_order,
            completed_order=self._completed_counter,
            result=result,
            transcript=self._trim_transcript(runner.messages if runner is not None else []),
        )
        self._save_record(record)
        if self.renderer is not None:
            seconds = result.elapsed_ms / 1000
            self.renderer.show_info(
                f"子代理「{task.name}」状态：{result.status}，"
                f"用时 {seconds:.1f}s，工具调用 {result.tool_call_count} 次"
            )
        return result

    def _save_record(self, record: SubagentRunRecord) -> None:
        """保存运行记录；保存失败不影响主流程。"""
        try:
            day = record.created_at.strftime("%Y%m%d")
            directory = self.workspace_root / ".minicode" / "subagents" / "runs" / day
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{record.run_id}.json"
            path.write_text(
                record.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except Exception:
            return

    @staticmethod
    def _trim_transcript(messages: list[Message]) -> list[Message]:
        """压缩运行记录中的超长 tool 输出。"""
        trimmed: list[Message] = []
        for message in messages:
            if message.role == "tool" and isinstance(message.content, str):
                content, _compressed = _compress_text(message.content, 2000)
                trimmed.append(
                    Message(
                        role="tool",
                        content=content,
                        tool_call_id=message.tool_call_id,
                        name=message.name,
                    )
                )
            else:
                trimmed.append(message)
        return trimmed


def format_subagent_result(result: SubagentResult, max_chars: int) -> str:
    """格式化为返回给主 Agent 的中文工具结果。"""
    lines = [
        f"子代理「{result.name}」状态：{result.status}",
        "",
        f"结论：{result.summary}",
    ]
    if result.findings:
        lines.extend(["", "发现："])
        lines.extend(f"- {item}" for item in result.findings)
    if result.changed_files:
        lines.extend(["", "相关文件："])
        lines.extend(f"- {item}" for item in result.changed_files)
    if result.verification:
        lines.extend(["", "验证建议："])
        lines.extend(f"- {item}" for item in result.verification)
    if result.errors:
        lines.extend(["", "错误："])
        lines.extend(f"- {item}" for item in result.errors)
    lines.append("")
    lines.append(f"工具调用：{result.tool_call_count} 次；用时：{result.elapsed_ms / 1000:.1f}s")
    text = "\n".join(lines)
    compressed, _ = _compress_text(text, max_chars)
    return compressed
