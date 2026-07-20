"""Prompt 章节和列表的通用组合函数。"""

from collections.abc import Sequence

from minicode.prompts.models import ToolPromptInfo


def join_sections(*sections: str | None) -> str:
    """清理并用两个换行连接非空章节。"""
    normalized = [section.strip() for section in sections if section and section.strip()]
    return "\n\n".join(normalized)


def render_named_items(items: Sequence[ToolPromptInfo]) -> str:
    """按调用方提供的顺序渲染名称和描述列表。"""
    rendered = [
        f"  - {item.name.strip()}: {item.description.strip()}"
        for item in items
        if item.name.strip() and item.description.strip()
    ]
    return "\n".join(rendered)
