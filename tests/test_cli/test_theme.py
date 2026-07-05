"""测试 CLI 主题配置。"""

from minicode.cli.theme import MINICODE_THEME


class TestTheme:
    """验证主题配置包含所有需要的样式键。"""

    def test_theme_has_required_styles(self) -> None:
        """主题应包含所有必需的样式定义。"""
        required_styles = [
            "info",
            "warning",
            "error",
            "success",
            "user.label",
            "assistant.label",
            "dim",
            "usage",
            "prompt",
        ]
        for style in required_styles:
            assert style in MINICODE_THEME.styles, f"缺少样式：{style}"

    def test_theme_styles_are_valid(self) -> None:
        """所有样式值应为有效的 Rich 样式（字符串或 Style 对象）。"""
        from rich.style import Style

        for name, style in MINICODE_THEME.styles.items():
            assert isinstance(style, (str, Style)), (
                f"样式 {name} 的类型无效：{type(style).__name__}"
            )
