"""单个 subagent 的受限 ReAct 执行器。"""

from __future__ import annotations

import inspect
import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from minicode.agent.context import build_messages
from minicode.agent.context_models import ContextConfig
from minicode.agent.subagents.models import SubagentConfig, SubagentResult, SubagentTask
from minicode.agent.subagents.prompts import build_subagent_system_prompt
from minicode.permissions.checker import check_permission
from minicode.providers.base import (
    FunctionCall,
    Message,
    StreamChunk,
    ToolCall,
    ToolMessage,
)
from minicode.utils.exceptions import ProviderError

if TYPE_CHECKING:
    from minicode.agent.subagents.manager import PermissionPromptQueue
    from minicode.config.models import AppConfig
    from minicode.permissions.store import PermissionStore
    from minicode.providers.base import BaseProvider
    from minicode.tools.registry import ToolRegistry


class SubagentRunner:
    """运行单个隔离 subagent。"""

    def __init__(
        self,
        *,
        provider: BaseProvider,
        tool_registry: ToolRegistry,
        workspace_root: Path,
        app_config: AppConfig,
        subagent_config: SubagentConfig,
        allowed_tools: list[str],
        permission_store: PermissionStore | None = None,
        permission_queue: PermissionPromptQueue | None = None,
    ) -> None:
        self.provider = provider
        self.tool_registry = tool_registry
        self.workspace_root = workspace_root
        self.app_config = app_config
        self.subagent_config = subagent_config
        self.allowed_tools = allowed_tools
        self.permission_store = permission_store
        self.permission_queue = permission_queue
        self.messages: list[Message] = []
        self.tool_call_count = 0

    async def run(self, task: SubagentTask, run_id: str) -> SubagentResult:
        """执行 subagent 任务并返回结构化结果。"""
        started = time.perf_counter()
        self.messages = [Message(role="user", content=task.task)]
        max_rounds = task.max_rounds or self.subagent_config.max_rounds
        system_prompt = build_subagent_system_prompt(task, self.allowed_tools)
        context_config = ContextConfig(
            max_input_tokens=self.subagent_config.max_context_tokens,
            recent_messages=10,
            max_tool_output_chars=6000,
            keep_first_user_message=True,
        )

        try:
            for _round_num in range(1, max_rounds + 1):
                context_result = build_messages(
                    self.messages,
                    system_prompt,
                    context_config,
                )
                stream = self.provider.chat(
                    messages=context_result.messages,
                    tools=self.tool_registry.get_tools_schema(),
                    stream=self.app_config.agent.stream,
                    max_tokens=self.app_config.max_tokens,
                )
                if inspect.iscoroutine(stream):
                    stream = await stream

                text_content, tool_calls = await self._collect_response(stream)
                self.messages.append(
                    Message(
                        role="assistant",
                        content=text_content,
                        tool_calls=tool_calls or None,
                    )
                )

                if not tool_calls:
                    return self._result_from_text(
                        task=task,
                        run_id=run_id,
                        text=text_content or "",
                        status="completed",
                        started=started,
                    )

                await self._execute_tools(tool_calls, task)

            return self._make_result(
                task=task,
                run_id=run_id,
                status="max_rounds",
                summary=f"子代理「{task.name}」已达到最大轮次，结果可能不完整。",
                started=started,
                errors=[f"已达到最大轮次：{max_rounds}"],
            )
        except ProviderError as e:
            return self._make_result(
                task=task,
                run_id=run_id,
                status="failed",
                summary=f"子代理「{task.name}」调用模型失败。",
                started=started,
                errors=[str(e)],
            )
        except Exception as e:
            return self._make_result(
                task=task,
                run_id=run_id,
                status="failed",
                summary=f"子代理「{task.name}」执行失败。",
                started=started,
                errors=[str(e)],
            )

    async def _collect_response(
        self,
        stream: AsyncIterator[StreamChunk],
    ) -> tuple[str | None, list[ToolCall] | None]:
        """收集 provider 响应，但不把 subagent 正文逐字刷到主 UI。"""
        text_buffer = ""
        deltas: list[StreamChunk] = []
        async for chunk in stream:
            if chunk.type == "text_delta" and chunk.text:
                text_buffer += chunk.text
            elif chunk.type == "tool_call_delta" and chunk.tool_call is not None:
                deltas.append(chunk)
            elif chunk.type == "done":
                break
            elif chunk.type == "error":
                raise ProviderError(chunk.text or "模型响应出错。")

        tool_calls = self._assemble_tool_calls(deltas) if deltas else None
        return text_buffer or None, tool_calls

    @staticmethod
    def _assemble_tool_calls(deltas: list[StreamChunk]) -> list[ToolCall]:
        """组装流式 tool_call delta。"""
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

        return [
            ToolCall(
                id=entry["id"] or f"call_{index}",
                function=FunctionCall(
                    name=entry["name"] or "",
                    arguments=entry["arguments"] or "",
                ),
            )
            for index, entry in sorted(assembled.items())
        ]

    async def _execute_tools(self, tool_calls: list[ToolCall], task: SubagentTask) -> None:
        """执行 subagent 工具调用。"""
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError as e:
                self.messages.append(
                    ToolMessage(
                        content=f"参数解析错误：{e}",
                        tool_call_id=tc.id,
                        name=name,
                    )
                )
                continue

            refusal = await self._check_permission(name, args, tc, task)
            if refusal is not None:
                self.messages.append(refusal)
                continue

            result = await self.tool_registry.execute_tool(
                name=name,
                args=args,
                workspace_root=self.workspace_root,
            )
            self.tool_call_count += 1
            self.messages.append(
                ToolMessage(
                    content=result.output,
                    tool_call_id=tc.id,
                    name=name,
                )
            )

    async def _check_permission(
        self,
        tool_name: str,
        arguments: dict[str, object],
        tc: ToolCall,
        task: SubagentTask,
    ) -> ToolMessage | None:
        """复用主权限体系，返回拒绝消息或 None。"""
        decision = check_permission(
            tool_name=tool_name,
            arguments=arguments,
            workspace_root=self.workspace_root,
            trust_mode=self.app_config.permissions.trust_mode,
        )
        if decision.allowed_without_prompt or self.app_config.permissions.trust_mode:
            return None
        if decision.denied:
            return ToolMessage(
                content=f"权限拒绝：{decision.summary}",
                tool_call_id=tc.id,
                name=tool_name,
            )
        if self.permission_store is not None and decision.target_paths:  # noqa: SIM102
            if self.permission_store.find_match(tool_name, decision.target_paths):
                return None
        if self.permission_queue is None:
            return ToolMessage(
                content=f"权限拒绝：{decision.summary}",
                tool_call_id=tc.id,
                name=tool_name,
            )

        result = await self.permission_queue.confirm(decision, task)
        if result.action == "deny":
            return ToolMessage(
                content=f"用户拒绝：{decision.summary}",
                tool_call_id=tc.id,
                name=tool_name,
            )
        if result.action == "always_allow":
            if self.permission_store is not None and decision.target_paths:
                try:
                    pattern = decision.target_paths[0].relative_to(self.workspace_root).as_posix()
                    self.permission_store.add_rule(tool_name, pattern)
                except ValueError:
                    pass
            return None
        if result.action == "allow":
            return None
        return ToolMessage(
            content=f"权限拒绝：确认返回值未知（{result.action}）",
            tool_call_id=tc.id,
            name=tool_name,
        )

    def _result_from_text(
        self,
        *,
        task: SubagentTask,
        run_id: str,
        text: str,
        status: str,
        started: float,
    ) -> SubagentResult:
        """解析最终 JSON；解析失败时使用文本 fallback。"""
        try:
            data = self._extract_json(text)
            return SubagentResult(
                run_id=run_id,
                name=task.name,
                role=task.role,
                status=status,  # type: ignore[arg-type]
                summary=str(data.get("summary") or "子代理已完成。"),
                findings=self._string_list(data.get("findings")),
                changed_files=self._string_list(data.get("changed_files")),
                verification=self._string_list(data.get("verification")),
                errors=self._string_list(data.get("errors")),
                tool_call_count=self.tool_call_count,
                elapsed_ms=self._elapsed_ms(started),
            )
        except (ValueError, TypeError, ValidationError):
            return self._fallback_result(task, run_id, text, status, started)

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """从模型文本中抽取 JSON 对象。"""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError("未找到 JSON 对象")
        data = json.loads(cleaned[start : end + 1])
        if not isinstance(data, dict):
            raise TypeError("JSON 结果必须是对象")
        return data

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value]
        return []

    def _fallback_result(
        self,
        task: SubagentTask,
        run_id: str,
        text: str,
        status: str,
        started: float,
    ) -> SubagentResult:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        findings = [
            line.lstrip("-0123456789. ")
            for line in lines
            if line.startswith("-") or line[:2].endswith(".")
        ]
        changed_files = [
            line
            for line in lines
            if any(prefix in line for prefix in ("src/", "tests/", "docs/"))
        ]
        return self._make_result(
            task=task,
            run_id=run_id,
            status=status,
            summary=text.strip() or "子代理已完成，但没有返回摘要。",
            started=started,
            findings=findings,
            changed_files=changed_files,
        )

    def _make_result(
        self,
        *,
        task: SubagentTask,
        run_id: str,
        status: str,
        summary: str,
        started: float,
        findings: list[str] | None = None,
        changed_files: list[str] | None = None,
        verification: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> SubagentResult:
        return SubagentResult(
            run_id=run_id,
            name=task.name,
            role=task.role,
            status=status,  # type: ignore[arg-type]
            summary=summary,
            findings=findings or [],
            changed_files=changed_files or [],
            verification=verification or [],
            errors=errors or [],
            tool_call_count=self.tool_call_count,
            elapsed_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)
