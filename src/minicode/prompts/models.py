"""Prompt 构建所需的轻量数据模型。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolPromptInfo:
    """供 prompt 层使用的工具名称和描述。"""

    name: str
    description: str
