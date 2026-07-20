from minicode.prompts.composition import join_sections, render_named_items
from minicode.prompts.models import ToolPromptInfo


def test_join_sections_ignores_none_and_blank_sections() -> None:
    assert join_sections("  第一段  ", None, " \n ", "第二段") == "第一段\n\n第二段"


def test_join_sections_uses_stable_blank_line_separator() -> None:
    assert join_sections("第一段\n", "第二段\n\n") == "第一段\n\n第二段"


def test_render_named_items_preserves_order_and_skips_blank_items() -> None:
    items = [
        ToolPromptInfo(name="read_file", description="读取文件"),
        ToolPromptInfo(name=" ", description="忽略"),
        ToolPromptInfo(name="grep", description="搜索内容"),
    ]

    assert render_named_items(items) == (
        "  - read_file: 读取文件\n"
        "  - grep: 搜索内容"
    )


def test_render_named_items_returns_empty_text_for_no_items() -> None:
    assert render_named_items([]) == ""
