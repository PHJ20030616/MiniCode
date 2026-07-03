"""MiniCode 命令行入口。"""

from pathlib import Path

import typer

from minicode import __version__
from minicode.config.loader import ConfigError, load

app = typer.Typer(
    name="minicode",
    help="MiniCode — 简化的 AI 辅助编程命令行工具。",
    add_completion=False,
)


def version_callback(value: bool) -> None:
    """输出版本号并退出程序。"""
    if value:
        typer.echo(f"MiniCode {__version__}")
        raise typer.Exit


@app.callback(invoke_without_command=True)
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=version_callback,
        is_eager=True,
        help="显示版本信息并退出。",
    ),
    model: str | None = typer.Option(  # noqa: B008
        None,
        "--model",
        "-m",
        help="使用的模型名称（如 gpt-4o）。",
    ),
    provider: str | None = typer.Option(  # noqa: B008
        None,
        "--provider",
        "-p",
        help="AI 提供商名称（如 openai）。",
    ),
    config: Path | None = typer.Option(  # noqa: B008
        None,
        "--config",
        help="配置文件路径。",
    ),
    workspace: Path | None = typer.Option(  # noqa: B008
        None,
        "--workspace",
        help="项目工作目录根路径。默认使用当前目录。",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="启用调试模式，输出更详细的日志信息。",
    ),
) -> None:
    """运行 MiniCode，一个简化的 AI 辅助编程工具。"""
    try:
        cli_overrides: dict[str, str] = {}
        if model is not None:
            cli_overrides["model"] = model
        if provider is not None:
            cli_overrides["provider"] = provider

        load(
            config_path=config,
            workspace=workspace,
            cli_overrides=cli_overrides or None,
        )
    except ConfigError as e:
        typer.echo(f"配置错误：{e}", err=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
