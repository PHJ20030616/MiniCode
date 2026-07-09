"""/config 命令 —— 查看当前配置。"""

from __future__ import annotations

from minicode.commands.base import BaseCommand, CommandContext, CommandResult


def _mask_api_key(key: str) -> str:
    """对 API key 进行脱敏处理。

    显示前 4 位 + **** + 后 4 位。
    长度不足 8 位的 key 全部脱敏。

    Args:
        key: 原始 API key。

    Returns:
        脱敏后的字符串。
    """
    if len(key) < 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


class ConfigCommand(BaseCommand):
    """查看 MiniCode 当前配置。"""

    name: str = "config"
    description: str = "查看当前配置"
    usage: str = "/config show"

    async def execute(self, args: str, ctx: CommandContext) -> CommandResult:
        """显示当前配置信息。

        Args:
            args: 子命令。支持 "show"，空字符串等同于 "show"。
            ctx: 命令执行上下文。

        Returns:
            CommandResult，message 包含格式化后的配置文本。
        """
        subcmd = args.strip().lower()

        if subcmd not in ("", "show"):
            return CommandResult(
                success=False,
                message=f"未知的 config 子命令：{subcmd}。\n用法：/config show — 查看当前配置",
            )

        config = ctx.app_config

        lines: list[str] = []
        lines.append("当前配置：")
        lines.append("")
        lines.append(f"  默认 Provider : {config.default_provider}")
        lines.append(f"  默认 Model    : {config.default_model}")
        lines.append(f"  Max Tokens    : {config.max_tokens}")
        lines.append(f"  Agent 最大轮次 : {config.agent.max_rounds}")
        lines.append(f"  流式输出       : {'启用' if config.agent.stream else '关闭'}")
        lines.append(f"  Trust 模式     : {'启用' if config.permissions.trust_mode else '关闭'}")
        lines.append("")

        # 显示已配置的 Provider 列表
        if config.providers:
            lines.append("已配置的 Providers：")
            lines.append("")
            for name, provider in config.providers.items():
                is_default = "*" if name == config.default_provider else " "
                masked_key = _mask_api_key(provider.api_key) if provider.api_key else "（未设置）"
                models_str = "、".join(provider.models) if provider.models else "（无）"
                lines.append(f"  [{is_default}] {name}")
                lines.append(f"      Base URL: {provider.base_url}")
                lines.append(f"      API Key : {masked_key}")
                lines.append(f"      Models  : {models_str}")
                lines.append("")

        return CommandResult(message="\n".join(lines))
