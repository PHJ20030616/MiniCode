"""记忆系统数据模型。

定义记忆的元数据、存储格式和序列化逻辑。
记忆以 Markdown 文件 + YAML frontmatter 格式存储。
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, Field, ValidationError

from minicode.utils.log import get_logger

logger = get_logger(__name__)


class MemorySource(StrEnum):
    """记忆来源。"""

    USER = "user"
    """用户主动告知的信息。"""
    CONVERSATION = "conversation"
    """从对话中自动提取的信息。"""
    MANUAL = "manual"
    """手动创建的记忆。"""


class MemoryScope(StrEnum):
    """记忆作用域。"""

    GLOBAL = "global"
    """全局记忆，跨工作区有效。"""
    WORKSPACE = "workspace"
    """工作区记忆，仅限当前项目。"""


class MemoryType(StrEnum):
    """记忆类型。"""

    USER = "user"
    """关于用户的信息。"""
    PROJECT = "project"
    """关于项目的信息。"""
    REFERENCE = "reference"
    """参考信息。"""
    FEEDBACK = "feedback"
    """用户反馈。"""


class MemoryMetadata(BaseModel):
    """记忆元数据。"""

    name: str
    """记忆的唯一标识名称，用于文件名。"""
    description: str = ""
    """记忆的简短描述。"""
    created_at: datetime
    """创建时间。"""
    updated_at: datetime
    """最后更新时间。"""
    source: MemorySource = MemorySource.USER
    """信息来源。"""
    scope: MemoryScope = MemoryScope.GLOBAL
    """作用域。"""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    """置信度，范围 0.0~1.0。"""
    type: MemoryType = MemoryType.USER
    """记忆类型。"""


class Memory(BaseModel):
    """完整的记忆条目，包含元数据和内容。"""

    metadata: MemoryMetadata
    """记忆元数据。"""
    content: str = ""
    """记忆正文内容。"""

    # 匹配 YAML frontmatter 的正则：---\n...\n---\n...
    FRONTMATTER_PATTERN: ClassVar[re.Pattern] = re.compile(
        r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL
    )

    @classmethod
    def parse_frontmatter(cls, content: str) -> tuple[dict[str, Any], str]:
        """解析记忆文件内容，分离 frontmatter 和正文。

        Args:
            content: 文件原始文本内容。

        Returns:
            (frontmatter_dict, body_text) 元组。
            无 frontmatter 或解析失败时返回空 dict + 原始内容。
        """
        match = cls.FRONTMATTER_PATTERN.match(content)
        if not match:
            return {}, content
        try:
            fm = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            logger.debug("记忆文件 frontmatter YAML 解析失败")
            return {}, content
        return fm, match.group(2).strip()

    @classmethod
    def from_file_content(cls, content: str) -> Memory:
        """从文件原始内容构造 Memory 实例。

        Args:
            content: 文件原始文本内容。

        Returns:
            解析成功的 Memory 实例。frontmatter 缺失或损坏时使用默认元数据。
        """
        fm, body = cls.parse_frontmatter(content)
        if not fm:
            return cls(
                metadata=MemoryMetadata(
                    name="unknown",
                    created_at=datetime.min,
                    updated_at=datetime.min,
                ),
                content=body or content.strip(),
            )
        try:
            metadata = MemoryMetadata(**fm)
        except ValidationError:
            logger.debug("记忆元数据验证失败，使用默认值")
            metadata = MemoryMetadata(
                name=fm.get("name", "unknown"),
                created_at=datetime.min,
                updated_at=datetime.min,
            )
        return cls(metadata=metadata, content=body)

    @classmethod
    def format_file(cls, metadata: MemoryMetadata, body: str) -> str:
        """将元数据和正文格式化为带 YAML frontmatter 的文件内容。

        Args:
            metadata: 记忆元数据。
            body: 正文内容。

        Returns:
            格式化的文件内容字符串。
        """
        data: dict[str, Any] = {}
        for k, v in metadata.model_dump().items():
            if isinstance(v, datetime):
                data[k] = v.isoformat()
            elif hasattr(v, "value"):
                data[k] = v.value
            else:
                data[k] = v
        frontmatter = yaml.dump(
            data,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
        return f"---\n{frontmatter}---\n\n{body}"
