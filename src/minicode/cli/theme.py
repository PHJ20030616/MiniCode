"""MiniCode CLI 颜色主题配置。

定义统一的 Rich Theme，控制终端中各类文本的显示样式。
"""

from rich.theme import Theme

MINICODE_THEME = Theme(
    {
        "info": "bold cyan",
        "warning": "bold yellow",
        "error": "bold red",
        "success": "bold green",
        "user.label": "bold green",
        "assistant.label": "bold blue",
        "dim": "dim white",
        "usage": "dim cyan",
        "prompt": "bold",
    }
)
