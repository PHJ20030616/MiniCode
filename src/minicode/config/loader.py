"""多层配置加载器。

加载优先级（从低到高）：
  1. 代码默认值
  2. ~/.minicode/config.yaml（全局用户配置）
  3. ./.minicode/config.yaml（项目配置）
  4. 环境变量（MINICODE_*）
  5. CLI 参数覆盖

环境变量 ${VAR_NAME} 占位符在第 3 步后解析。
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml

from minicode.config.models import AppConfig
from minicode.utils.exceptions import ConfigError

# ── 环境变量映射 ──────────────────────────────────────────────────
# 键为环境变量名，值为配置字典中的嵌套路径
ENV_CONFIG_MAP: dict[str, tuple[str, ...]] = {
    "MINICODE_DEFAULT_PROVIDER": ("default_provider",),
    "MINICODE_DEFAULT_MODEL": ("default_model",),
    "MINICODE_MAX_TOKENS": ("max_tokens",),
    "MINICODE_MAX_ROUNDS": ("agent", "max_rounds"),
    "MINICODE_STREAM": ("agent", "stream"),
    "MINICODE_TRUST_MODE": ("permissions", "trust_mode"),
}

# 用于发现提供商环境变量的前后缀
PROVIDER_ENV_PREFIX = "MINICODE_"
PROVIDER_ENV_SUFFIX_API_KEY = "_API_KEY"
PROVIDER_ENV_SUFFIX_BASE_URL = "_BASE_URL"


# ── 内部工具函数 ──────────────────────────────────────────────────


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典，override 中的值覆盖 base 中的值。"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _set_nested(config: dict[str, Any], keys: tuple[str, ...], value: Any) -> None:
    """在嵌套字典中按键路径设置值。"""
    current = config
    for key in keys[:-1]:
        if key not in current or not isinstance(current.get(key), dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _load_yaml(path: Path) -> dict[str, Any]:
    """加载 YAML 文件，出错时抛出 ConfigError。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件 {path} 格式错误：{e}") from e
    except OSError as e:
        raise ConfigError(f"无法读取配置文件 {path}：{e}") from e


def _resolve_placeholders(value: str) -> str:
    """解析字符串中的 ${ENV_VAR} 占位符。

    未设置的环境变量直接抛出 ConfigError，避免未定义的占位符绕过后续校验。
    """

    def _replace(match: re.Match[str]) -> str:
        env_var = match.group(1)
        env_value = os.environ.get(env_var)
        if env_value is None:
            raise ConfigError(
                f"配置中引用了未设置的环境变量 '${{{env_var}}}'。\n"
                f"请设置环境变量 {env_var}=<值> 后再试。"
            )
        return env_value

    return re.sub(r"\$\{(\w+)\}", _replace, value)


def _resolve_placeholders_recursive(data: dict[str, Any]) -> dict[str, Any]:
    """递归解析字典所有字符串值中的 ${ENV_VAR} 占位符。"""
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = _resolve_placeholders_recursive(value)
        elif isinstance(value, str):
            result[key] = _resolve_placeholders(value)
        elif isinstance(value, list):
            result[key] = [
                _resolve_placeholders(item) if isinstance(item, str) else item
                for item in value
            ]
        else:
            result[key] = value
    return result


def _get_defaults() -> dict[str, Any]:
    """获取代码内建默认配置。"""
    return {
        "default_provider": "deepseek",
        "default_model": "deepseek-v4-flash",
        "max_tokens": 16384,
        "providers": {
            "openai": {
                "api_key": "",
                "base_url": "https://api.openai.com/v1",
                "models": ["gpt-4o", "gpt-4o-mini"],
            },
            "deepseek": {
                "api_key": "",
                "base_url": "https://api.deepseek.com",
                "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
            },
        },
        "agent": {
            "max_rounds": 20,
            "stream": True,
        },
        "permissions": {
            "trust_mode": False,
        },
    }


def _merge_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """应用环境变量覆盖。"""
    for env_name, config_path in ENV_CONFIG_MAP.items():
        value = os.environ.get(env_name)
        if value is not None:
            _set_nested(config, config_path, value)
    return config


def _merge_provider_env_vars(config: dict[str, Any]) -> dict[str, Any]:
    """应用提供商相关的环境变量覆盖（MINICODE_<名称>_API_KEY / BASE_URL）。

    扫描所有环境变量，自动发现并设置对应提供商的配置。
    """
    prefix_len = len(PROVIDER_ENV_PREFIX)
    suffix_api_len = len(PROVIDER_ENV_SUFFIX_API_KEY)
    suffix_url_len = len(PROVIDER_ENV_SUFFIX_BASE_URL)

    for env_name, env_value in os.environ.items():
        if not env_name.startswith(PROVIDER_ENV_PREFIX):
            continue

        rest = env_name[prefix_len:]

        if rest.endswith(PROVIDER_ENV_SUFFIX_API_KEY):
            provider_name = rest[: -suffix_api_len].lower()
            if provider_name:
                _set_nested(config, ("providers", provider_name, "api_key"), env_value)

        elif rest.endswith(PROVIDER_ENV_SUFFIX_BASE_URL):
            provider_name = rest[: -suffix_url_len].lower()
            if provider_name:
                _set_nested(config, ("providers", provider_name, "base_url"), env_value)

    return config


def _apply_cli_overrides(
    config: dict[str, Any], cli_overrides: dict[str, Any]
) -> dict[str, Any]:
    """应用 CLI 参数覆盖，优先级最高。"""
    if "model" in cli_overrides and cli_overrides["model"] is not None:
        config["default_model"] = cli_overrides["model"]
    if "provider" in cli_overrides and cli_overrides["provider"] is not None:
        config["default_provider"] = cli_overrides["provider"]
    return config


def _validate_api_key(config: AppConfig) -> None:
    """验证默认提供商已配置 API key。

    如果 API key 不存在则抛出清晰的 ConfigError。
    """
    provider_name = config.default_provider
    if provider_name not in config.providers:
        raise ConfigError(
            f"默认提供商 '{provider_name}' 未在配置中定义。\n"
            f"请检查配置文件中 providers 部分。\n"
            f"可用的提供商：{', '.join(config.providers.keys())}"
        )

    provider = config.providers[provider_name]
    if not provider.api_key:
        env_var_name = (
            f"{PROVIDER_ENV_PREFIX}{provider_name.upper()}{PROVIDER_ENV_SUFFIX_API_KEY}"
        )
        raise ConfigError(
            f"提供商 '{provider_name}' 未配置 API key。\n"
            f"请通过以下方式之一设置：\n"
            f"  1. 配置文件：在 ~/.minicode/config.yaml 中配置 {provider_name}.api_key\n"
            f"  2. 环境变量：设置 {env_var_name}\n"
            f"  3. 使用 --provider 参数切换到其他已配置的提供商"
        )


# ── 公开 API ─────────────────────────────────────────────────────


def load(
    *,
    config_path: Path | None = None,
    workspace: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """加载配置，按优先级合并所有配置层。

    优先级（从低到高）：
      1. 代码默认值
      2. ~/.minicode/config.yaml（全局用户配置）
      3. ./.minicode/config.yaml（项目配置）
      4. --config 指定的配置文件（如果有）
      5. 环境变量（MINICODE_*）
      6. CLI 参数覆盖

    参数：
        config_path: 通过 --config 显式指定的配置文件路径。
                     加载顺序在项目配置之后、环境变量之前。
        workspace: 项目工作目录。用于查找 ./.minicode/config.yaml。
                    默认使用当前工作目录。
        cli_overrides: CLI 参数覆盖字典，如 {"model": "gpt-4o", "provider": "openai"}。

    返回：
        验证通过的 AppConfig 实例。

    异常：
        ConfigError: 配置文件格式错误、无法读取、或缺少必要的 API key。
    """
    # 第 1 层：代码内建默认值
    config = _get_defaults()

    # 第 2 层：全局用户配置
    global_config = Path.home() / ".minicode" / "config.yaml"
    if global_config.exists():
        config = _deep_merge(config, _load_yaml(global_config))

    # 第 3 层：项目配置
    project_root = workspace or Path.cwd()
    project_config = project_root / ".minicode" / "config.yaml"
    if project_config.exists():
        config = _deep_merge(config, _load_yaml(project_config))

    # 第 4 层：--config 显式指定的配置文件
    if config_path is not None:
        if not config_path.exists():
            raise ConfigError(f"指定的配置文件不存在：{config_path}")
        config = _deep_merge(config, _load_yaml(config_path))

    # 解析 ${ENV_VAR} 占位符（在 YAML 合并之后、env/CLI 覆盖之前）
    config = _resolve_placeholders_recursive(config)

    # 第 5 层：环境变量
    config = _merge_env_vars(config)
    config = _merge_provider_env_vars(config)

    # 第 6 层：CLI 参数覆盖
    if cli_overrides:
        config = _apply_cli_overrides(config, cli_overrides)

    # 验证并返回
    app_config = AppConfig(**config)
    _validate_api_key(app_config)
    return app_config
