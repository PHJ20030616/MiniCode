"""MiniCode 命令行入口。"""

from pathlib import Path

import structlog
import typer

from minicode import __version__
from minicode.config.loader import load
from minicode.utils.exceptions import ConfigError, MiniCodeError
from minicode.utils.log import get_logger, setup_logging

logger = get_logger(__name__)

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
        # 确定工作目录和日志目录
        workspace_path = workspace or Path.cwd()
        log_base_dir = workspace_path / ".minicode"

        # 先初始化日志系统（早于配置加载，以便捕获配置阶段的日志）
        log_file = setup_logging(
            debug=debug,
            log_base_dir=log_base_dir,
            provider=provider,
            model=model,
            workspace=str(workspace_path),
        )

        if log_file:
            logger.debug("日志文件已创建", path=str(log_file))

        logger.debug(
            "MiniCode 启动",
            version=__version__,
            debug=debug,
            model=model,
            provider=provider,
            workspace=str(workspace_path),
        )

        cli_overrides: dict[str, str] = {}
        if model is not None:
            cli_overrides["model"] = model
        if provider is not None:
            cli_overrides["provider"] = provider

        app_config = load(
            config_path=config,
            workspace=workspace_path,
            cli_overrides=cli_overrides or None,
        )

        # 配置加载完成后，用最终值重新绑定日志上下文
        structlog.contextvars.bind_contextvars(
            provider=app_config.default_provider,
            model=app_config.default_model,
        )

        logger.debug(
            "配置加载完成",
            default_provider=app_config.default_provider,
            default_model=app_config.default_model,
        )

        # TODO: 进入 Agent Loop（Phase 1+ 实现）
        typer.echo("MiniCode 启动成功。进入对话模式的功能将在后续版本实现。")

    except ConfigError as e:
        typer.echo(f"配置错误：{e}", err=True)
        logger.debug("配置错误", error=str(e), exc_info=True)
        raise typer.Exit(code=1) from None
    except MiniCodeError as e:
        typer.echo(f"错误：{e}", err=True)
        logger.debug("运行时错误", error=str(e), exc_info=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
