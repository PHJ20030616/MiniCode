"""LLM Provider 基础抽象与内部消息模型。

内部消息模型采用 OpenAI 兼容风格，但设计上避免强依赖 OpenAI 独有字段，
确保后续可转换为 Anthropic 等其它 Provider 的消息格式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Literal

from pydantic import BaseModel


class FunctionCall(BaseModel):
    """工具调用的函数信息。

    name: 函数名称
    arguments: JSON 字符串格式的参数
    """

    name: str
    arguments: str


class ContentBlock(BaseModel):
    """内容块。

    v0.1 仅支持文本类型。
    后续版本可扩展支持 image、PDF、audio 等多模态类型。
    """

    type: Literal["text"] = "text"
    text: str | None = None


class ToolCall(BaseModel):
    """模型返回的工具调用。

    id: 工具调用唯一标识
    type: 固定为 "function"
    function: 调用的函数信息
    """

    id: str
    type: Literal["function"] = "function"
    function: FunctionCall


class Message(BaseModel):
    """统一的内部消息格式。

    role 支持四种类型：
    - system: 系统提示词，content 为文本
    - user: 用户消息，content 为文本或内容块列表
    - assistant: 模型回复，可包含文本和工具调用
    - tool: 工具执行结果（推荐使用 ToolMessage 以获得更严格的类型约束）

    设计原则：不包含任何 OpenAI 独有字段名，所有字段都可映射到 Anthropic 等其它格式。
    """

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentBlock] | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    kind: Literal["compact_summary"] | None = None


class ToolMessage(Message):
    """工具执行结果消息。

    与通用 Message 不同，ToolMessage 对 tool 消息有明确约束：
    - role 固定为 "tool"，不允许其他值
    - tool_call_id 为必填字段（而非可选）
    - content 限定为纯文本（不支持多模态内容块）
    - 不允许包含 tool_calls

    用法示例：
        msg = ToolMessage(content="查询结果：42", tool_call_id="call_abc123")
    """

    role: Literal["tool"] = "tool"
    content: str | None = None
    tool_call_id: str  # type: ignore[assignment]  # 必填，覆盖父类的可选字段
    tool_calls: None = None  # type: ignore[assignment]  # tool 消息不含 tool_calls
    consumed_by_main_model: bool = False


class PartialToolCall(BaseModel):
    """流式输出中不完整的工具调用。

    用于 tool_call_delta 类型的 StreamChunk，
    模型逐步输出工具调用信息时使用。
    """

    id: str | None = None
    index: int = 0
    name: str | None = None
    arguments: str = ""


class UsageInfo(BaseModel):
    """Token 使用量统计。"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class StreamChunk(BaseModel):
    """统一的流式响应块。

    type 决定块类型：
    - text_delta: 文本增量
    - tool_call_delta: 工具调用增量（非流式收集时直接使用完整 tool_call）
    - done: 流结束，携带最终 usage 信息
    - error: 错误信息
    """

    type: Literal["text_delta", "tool_call_delta", "done", "error"]
    text: str | None = None
    tool_call: PartialToolCall | None = None
    usage: UsageInfo | None = None


class BaseProvider(ABC):
    """LLM Provider 抽象基类。

    所有 Provider 适配器必须实现 chat 和 list_models 方法。
    Agent Loop 只依赖此抽象，不直接访问 Provider SDK 返回对象。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称，如 'openai', 'deepseek', 'anthropic'。"""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        stream: bool = True,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """发送对话请求并返回流式响应。

        Args:
            messages: 对话消息列表
            tools: OpenAI 格式的工具定义列表，None 表示纯文本对话
            stream: 是否使用流式响应。v0.1 工具调用阶段允许非流式以降低复杂度
            max_tokens: 最大输出 token 数

        Yields:
            StreamChunk: 流式响应块
        """
        # 为了让 Python 识别此方法为异步生成器
        # 子类必须实现此方法并正确 yield StreamChunk
        if False:  # pragma: no cover
            yield

    @abstractmethod
    async def list_models(self) -> list[str]:
        """获取当前 Provider 可用的模型列表。"""
