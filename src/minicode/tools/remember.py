"""记忆写入工具 — 供 Agent 在用户明确表达记忆意图时调用。

允许大模型在识别到用户自然语言中的记忆意图后，
通过此工具将用户指定的信息保存为长期记忆。

安全策略：对 name、content、description 三个字段进行敏感信息检测，
包括中英文关键词和常见密钥模式匹配，拒绝保存密码、token、密钥等。
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from minicode.memory.manager import MemoryManager
from minicode.memory.models import MemoryMetadata, MemoryScope, MemorySource, MemoryType
from minicode.tools.base import BaseTool, ToolResult

# ── 敏感中文关键词 ──────────────────────────────────────────────
_CHINESE_SENSITIVE_KEYWORDS: list[str] = [
    "密码", "密钥", "私钥", "令牌", "凭据",
    "访问密钥", "API密钥", "api密钥", "API 密钥",
    "口令", "secret", "token", "密码本",
]

# ── 敏感英文关键词 ──────────────────────────────────────────────
_ENGLISH_SENSITIVE_KEYWORDS: list[str] = [
    "password", "token", "api_key", "api key", "secret",
    "credential", "private key", "ssh key", "access_key",
    "access key",
]

# ── 常见密钥值模式 ──────────────────────────────────────────────
_SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    # OpenAI / DeepSeek 风格 sk-...（允许 dashes，如 sk-proj-xxxx）
    re.compile(r"(?<![a-zA-Z])sk-[A-Za-z0-9-]{10,}"),
    # GitHub 个人访问令牌
    re.compile(r"(?<![a-zA-Z])ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?<![a-zA-Z])github_pat_[A-Za-z0-9_]{20,}"),
    # PEM 私钥头
    re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),
    # AWS 访问密钥
    re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}"),
]


def _check_sensitive(name: str, content: str, description: str) -> str | None:
    """检查三个字段是否包含敏感信息。

    Args:
        name: 记忆名称。
        content: 记忆正文。
        description: 记忆描述。

    Returns:
        发现敏感信息时返回错误消息，否则返回 None。
    """
    fields = {"名称": name, "内容": content, "描述": description}
    all_keywords = _CHINESE_SENSITIVE_KEYWORDS + _ENGLISH_SENSITIVE_KEYWORDS

    for field_label, field_value in fields.items():
        if not field_value:
            continue
        value_lower = field_value.lower()

        # 关键词检查
        for keyword in all_keywords:
            if keyword in value_lower:
                return (
                    f"拒绝保存：在「{field_label}」中检测到敏感关键词「{keyword}」。"
                    "请勿将密码、token、密钥、凭据等敏感信息保存为记忆。"
                )

        # 模式检查（对 name 和 description 也做检查）
        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(field_value):
                return (
                    f"拒绝保存：在「{field_label}」中检测到可能的密钥或令牌格式。"
                    "请勿将密钥、token 等敏感信息保存为记忆。"
                )

    return None


class Remember(BaseTool):
    """将用户明确要求记住的信息保存为长期记忆。

    仅在用户明确表达「记住…」「以后记得…」「保存为记忆…」「帮我记一下…」
    等语义时调用此工具。不要将普通对话内容自动保存为记忆。
    """

    name: str = "remember"
    description: str = (
        "将用户明确要求记住的信息保存为长期记忆。"
        "当用户说「记住…」「以后记得…」「保存为记忆…」「帮我记一下…」等时调用此工具。"
        "不要保存敏感信息（密码、token、密钥、隐私身份信息）。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "记忆唯一标识名，仅允许字母数字下划线连字符，如 'reply-lang'",
            },
            "content": {
                "type": "string",
                "description": "记忆正文内容，用户要求记住的具体信息",
            },
            "description": {
                "type": "string",
                "description": "记忆的简短描述（可选）",
            },
            "scope": {
                "type": "string",
                "enum": ["workspace", "global"],
                "description": "作用域：项目相关默认 workspace；跨项目偏好可用 global",
            },
            "type": {
                "type": "string",
                "enum": ["user", "project", "reference", "feedback"],
                "description": "记忆类型：用户信息/项目信息/参考信息/用户反馈",
            },
            "confidence": {
                "type": "number",
                "description": "置信度 0.0~1.0，用户明确要求记住时设为 0.9",
                "default": 0.9,
            },
        },
        "required": ["name", "content"],
        "additionalProperties": False,
    }

    async def execute(self, **kwargs: object) -> ToolResult:
        # ── 参数提取 ──
        name = kwargs.get("name")
        content = kwargs.get("content")
        description = kwargs.get("description", "")
        scope_raw = kwargs.get("scope", "workspace")
        type_raw = kwargs.get("type", "project")
        confidence = kwargs.get("confidence", 0.9)

        if not isinstance(name, str) or not name.strip():
            return ToolResult(success=False, output="参数 name 必须是有效的非空字符串")
        if not isinstance(content, str) or not content.strip():
            return ToolResult(success=False, output="参数 content 必须是有效的非空字符串")
        if not isinstance(description, str):
            description = ""

        # ── 名称验证 ──
        try:
            MemoryManager._validate_memory_name(name)
        except ValueError as e:
            return ToolResult(success=False, output=str(e))

        # ── 枚举解析 ──
        try:
            scope = MemoryScope(scope_raw) if isinstance(scope_raw, str) else MemoryScope.WORKSPACE
        except ValueError:
            return ToolResult(success=False, output=f"无效的 scope 值：{scope_raw}")

        try:
            mem_type = MemoryType(type_raw) if isinstance(type_raw, str) else MemoryType.PROJECT
        except ValueError:
            return ToolResult(success=False, output=f"无效的 type 值：{type_raw}")

        if not isinstance(confidence, (int, float)):
            confidence = 0.9

        # ── 安全检查：拒绝敏感信息（检查 name、content、description） ──
        error_msg = _check_sensitive(
            name=str(name),
            content=str(content),
            description=str(description),
        )
        if error_msg:
            return ToolResult(success=False, output=error_msg)

        # ── 构建元数据并保存 ──
        now = datetime.now(UTC)
        metadata = MemoryMetadata(
            name=name,
            description=str(description),
            created_at=now,
            updated_at=now,
            source=MemorySource.USER,
            scope=scope,
            confidence=float(confidence),
            type=mem_type,
        )

        if self.workspace_root is None:
            return ToolResult(success=False, output="工作区根路径未设置")

        try:
            manager = MemoryManager(self.workspace_root)
            manager.add(metadata, content)
            return ToolResult(
                success=True,
                output=f"已记住：{content}",
            )
        except Exception as e:
            return ToolResult(success=False, output=f"保存记忆失败：{e}")
