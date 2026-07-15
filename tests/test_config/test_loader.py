"""配置加载器测试。

覆盖以下场景：
- 默认配置（无配置文件、无环境变量）
- 全局配置（~/.minicode/config.yaml）
- 项目配置覆盖全局配置
- 显式 --config 配置文件
- 环境变量覆盖
- CLI 参数覆盖（最高优先级）
- ${ENV_VAR} 占位符解析（含缺失变量报错）
- 提供商 API key 通过环境变量设置
- 缺少 API key 时抛出清晰错误
- 完整优先级链验证
"""

from pathlib import Path

import pytest
import yaml

from minicode.config.loader import ConfigError, load
from minicode.config.models import AppConfig

# ── 辅助函数 ─────────────────────────────────────────────────────


def _write_yaml(path: Path, data: dict) -> None:
    """将字典写入 YAML 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True)


# ── 配置目录的 monkeypatch fixture ──────────────────────────────


@pytest.fixture
def fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """将 Path.home() 指向临时目录、隔离 workspace，避免影响真实配置。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    # 同时隔离工作目录，避免读取当前项目的 .minicode/config.yaml
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    return home


@pytest.fixture
def fake_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """将工作目录指向临时目录并 chdir。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.chdir(ws)
    return ws


# ── 测试用例 ─────────────────────────────────────────────────────


@pytest.mark.usefixtures("clean_minicode_env")
class TestDefaultConfig:
    """无配置文件、无环境变量时的默认配置。"""

    def test_loads_without_files_or_env(self, fake_home: Path) -> None:
        """验证无配置文件和环境变量时默认配置结构正确。"""
        config = AppConfig()
        assert config.default_provider == "deepseek"
        assert config.default_model == "deepseek-v4-flash"
        assert config.max_tokens == 16384
        assert config.agent.max_rounds == 20
        assert config.agent.stream is True
        assert config.permissions.trust_mode is False

    def test_default_provider_has_openai(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证默认提供了 openai 和 deepseek 两个提供商配置。"""
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-default-key")
        config = load()
        assert "openai" in config.providers
        assert config.providers["openai"].base_url == "https://api.openai.com/v1"
        assert "gpt-4o" in config.providers["openai"].models
        assert "deepseek" in config.providers

    def test_default_missing_api_key_error(self, fake_home: Path) -> None:
        """验证默认 deepseek 没有 API key 时抛出 ConfigError。"""
        with pytest.raises(ConfigError, match="未配置 API key"):
            load()


@pytest.mark.usefixtures("clean_minicode_env")
class TestGlobalConfig:
    """~/.minicode/config.yaml 全局配置加载。"""

    def test_global_config_overrides_default(self, fake_home: Path) -> None:
        """验证全局配置可以覆盖默认值。"""
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "default_provider": "openai",
                "default_model": "gpt-4o",
                "max_tokens": 8192,
                "providers": {
                    "openai": {
                        "api_key": "sk-global-key",
                        "base_url": "https://api.openai.com/v1",
                        "models": ["gpt-4o", "gpt-4o-mini"],
                    },
                },
            },
        )
        config = load()
        assert config.default_provider == "openai"
        assert config.default_model == "gpt-4o"
        assert config.max_tokens == 8192
        assert config.providers["openai"].api_key == "sk-global-key"

    def test_global_config_adds_new_provider(self, fake_home: Path) -> None:
        """验证全局配置可以添加新的提供商。"""
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "default_provider": "deepseek",
                "providers": {
                    "openai": {
                        "api_key": "sk-openai",
                        "base_url": "https://api.openai.com/v1",
                        "models": ["gpt-4o"],
                    },
                    "deepseek": {
                        "api_key": "sk-deepseek",
                        "base_url": "https://api.deepseek.com/v1",
                        "models": ["deepseek-chat"],
                    },
                },
            },
        )
        config = load()
        assert config.default_provider == "deepseek"
        assert config.providers["deepseek"].api_key == "sk-deepseek"
        assert config.providers["openai"].api_key == "sk-openai"

    def test_global_config_missing_file(self, fake_home: Path) -> None:
        """验证全局配置文件不存在时不会报错。"""
        with pytest.raises(ConfigError, match="未配置 API key"):
            load()


@pytest.mark.usefixtures("clean_minicode_env")
class TestProjectConfig:
    """./.minicode/config.yaml 项目配置加载。"""

    def test_project_overrides_global(
        self, fake_home: Path, fake_workspace: Path
    ) -> None:
        """验证项目配置可以覆盖全局配置。"""
        # 全局配置
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "default_provider": "openai",
                "default_model": "gpt-4o",
                "providers": {
                    "openai": {
                        "api_key": "sk-global",
                        "base_url": "https://api.openai.com/v1",
                        "models": ["gpt-4o"],
                    },
                },
            },
        )
        # 项目配置（应覆盖全局）
        _write_yaml(
            fake_workspace / ".minicode" / "config.yaml",
            {
                "default_model": "gpt-4o-mini",
                "providers": {
                    "openai": {
                        "api_key": "sk-project",
                    },
                },
            },
        )
        config = load(workspace=fake_workspace)
        # 项目配置的 model 应覆盖全局配置
        assert config.default_model == "gpt-4o-mini"
        # 项目配置的 api_key 应覆盖全局配置
        assert config.providers["openai"].api_key == "sk-project"
        # 全局配置中被项目配置未覆盖的字段应保留
        assert config.providers["openai"].base_url == "https://api.openai.com/v1"

    def test_project_config_only(self, fake_home: Path, fake_workspace: Path) -> None:
        """验证仅有项目配置文件时正常工作。"""
        _write_yaml(
            fake_workspace / ".minicode" / "config.yaml",
            {
                "default_provider": "custom",
                "providers": {
                    "custom": {
                        "api_key": "sk-custom",
                        "base_url": "https://custom.api.com/v1",
                        "models": ["custom-model"],
                    },
                },
            },
        )
        config = load(workspace=fake_workspace)
        assert config.default_provider == "custom"
        assert config.providers["custom"].api_key == "sk-custom"

    def test_project_config_cwd(self, fake_home: Path, fake_workspace: Path) -> None:
        """验证不传 workspace 时使用当前工作目录查找项目配置。"""
        _write_yaml(
            fake_workspace / ".minicode" / "config.yaml",
            {
                "default_provider": "openai",
                "providers": {
                    "openai": {
                        "api_key": "sk-cwd",
                    },
                },
            },
        )
        # 不传 workspace，应使用当前目录（已被 monkeypatch 指向 fake_workspace）
        config = load()
        assert config.providers["openai"].api_key == "sk-cwd"


@pytest.mark.usefixtures("clean_minicode_env")
class TestExplicitConfig:
    """通过 --config 显式指定的配置文件。"""

    def test_explicit_overrides_project(
        self, fake_home: Path, fake_workspace: Path
    ) -> None:
        """验证显式配置文件覆盖项目配置。"""
        # 全局配置
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "default_provider": "openai",
                "providers": {
                    "openai": {
                        "api_key": "sk-global",
                        "base_url": "https://api.openai.com/v1",
                        "models": ["gpt-4o"],
                    },
                },
            },
        )
        # 项目配置（设定 model）
        _write_yaml(
            fake_workspace / ".minicode" / "config.yaml",
            {
                "default_model": "gpt-4o",
            },
        )
        # 显式配置文件（应覆盖项目配置的 model）
        explicit = fake_workspace / "my-config.yaml"
        _write_yaml(
            explicit,
            {
                "default_model": "gpt-4o-from-explicit",
            },
        )
        config = load(workspace=fake_workspace, config_path=explicit)
        assert config.default_model == "gpt-4o-from-explicit"
        assert config.providers["openai"].api_key == "sk-global"

    def test_explicit_not_found_error(self, fake_home: Path) -> None:
        """验证显式指定的配置文件不存在时抛出 ConfigError。"""
        nonexistent = Path("/nonexistent/config.yaml")
        with pytest.raises(ConfigError, match="不存在"):
            load(config_path=nonexistent)

    def test_explicit_overrides_default_provider(
        self, fake_home: Path
    ) -> None:
        """验证显式配置文件可以替换默认提供商。"""
        explicit = fake_home / "custom-config.yaml"
        _write_yaml(
            explicit,
            {
                "default_provider": "deepseek",
                "providers": {
                    "deepseek": {
                        "api_key": "sk-deepseek",
                        "base_url": "https://api.deepseek.com/v1",
                        "models": ["deepseek-chat"],
                    },
                },
            },
        )
        config = load(config_path=explicit)
        assert config.default_provider == "deepseek"


@pytest.mark.usefixtures("clean_minicode_env")
class TestEnvVarOverride:
    """环境变量覆盖配置。"""

    def test_env_overrides_yaml(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证环境变量可以覆盖 YAML 配置。"""
        # 全局配置
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "default_provider": "openai",
                "default_model": "gpt-4o",
                "providers": {
                    "openai": {
                        "api_key": "sk-from-yaml",
                    },
                },
            },
        )
        # 环境变量覆盖
        monkeypatch.setenv("MINICODE_DEFAULT_MODEL", "gpt-4o-mini")
        config = load()
        assert config.default_model == "gpt-4o-mini"
        assert config.providers["openai"].api_key == "sk-from-yaml"

    def test_env_provider_api_key(self, fake_home: Path, monkeypatch) -> None:
        """验证通过环境变量设置提供商 API key。"""
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-deepseek")
        config = load()
        assert config.providers["deepseek"].api_key == "sk-deepseek"

    def test_env_provider_base_url(self, fake_home: Path, monkeypatch) -> None:
        """验证通过环境变量设置提供商 base URL。"""
        monkeypatch.setenv("MINICODE_DEEPSEEK_BASE_URL", "https://custom.deepseek.com/v1")
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-key")
        config = load()
        assert config.providers["deepseek"].base_url == "https://custom.deepseek.com/v1"

    def test_env_discover_new_provider(self, fake_home: Path, monkeypatch) -> None:
        """验证环境变量可以动态发现新的提供商。"""
        monkeypatch.setenv("MINICODE_DEFAULT_PROVIDER", "deepseek")
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-deepseek")
        monkeypatch.setenv("MINICODE_DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        config = load()
        assert config.default_provider == "deepseek"
        assert "deepseek" in config.providers
        assert config.providers["deepseek"].api_key == "sk-deepseek"
        assert config.providers["deepseek"].base_url == "https://api.deepseek.com/v1"

    def test_env_agent_settings(self, fake_home: Path, monkeypatch) -> None:
        """验证环境变量可以覆盖 agent 配置。"""
        monkeypatch.setenv("MINICODE_MAX_ROUNDS", "10")
        monkeypatch.setenv("MINICODE_STREAM", "false")
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-key")
        config = load()
        assert config.agent.max_rounds == 10
        assert config.agent.stream is False

    def test_env_subagent_settings(self, fake_home: Path, monkeypatch) -> None:
        """验证环境变量可以覆盖 subagent 配置。"""
        monkeypatch.setenv("MINICODE_SUBAGENTS_ENABLED", "true")
        monkeypatch.setenv("MINICODE_SUBAGENTS_MAX_AGENTS", "4")
        monkeypatch.setenv("MINICODE_SUBAGENTS_CONCURRENCY", "2")
        monkeypatch.setenv("MINICODE_SUBAGENTS_MAX_ROUNDS", "6")
        monkeypatch.setenv("MINICODE_SUBAGENTS_MAX_CONTEXT_TOKENS", "9000")
        monkeypatch.setenv("MINICODE_SUBAGENTS_MAX_RESULT_CHARS", "3000")
        monkeypatch.setenv("MINICODE_SUBAGENTS_ALLOW_WRITE_TOOLS", "true")
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-key")

        config = load()

        assert config.agent.subagents.enabled is True
        assert config.agent.subagents.max_agents == 4
        assert config.agent.subagents.concurrency == 2
        assert config.agent.subagents.max_rounds == 6
        assert config.agent.subagents.max_context_tokens == 9000
        assert config.agent.subagents.max_result_chars == 3000
        assert config.agent.subagents.allow_write_tools is True

    def test_env_context_max_input_tokens(self, fake_home: Path, monkeypatch) -> None:
        """验证 MINICODE_CONTEXT_MAX_INPUT_TOKENS 被正确解析为 int。"""
        monkeypatch.setenv("MINICODE_CONTEXT_MAX_INPUT_TOKENS", "32000")
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-key")
        config = load()
        assert config.agent.context.max_input_tokens == 32000

    def test_env_context_recent_messages(self, fake_home: Path, monkeypatch) -> None:
        """验证 MINICODE_CONTEXT_RECENT_MESSAGES 被正确解析为 int。"""
        monkeypatch.setenv("MINICODE_CONTEXT_RECENT_MESSAGES", "8")
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-key")
        config = load()
        assert config.agent.context.recent_messages == 8

    def test_env_context_max_tool_output_chars(self, fake_home: Path, monkeypatch) -> None:
        """验证 MINICODE_CONTEXT_MAX_TOOL_OUTPUT_CHARS 被正确解析为 int。"""
        monkeypatch.setenv("MINICODE_CONTEXT_MAX_TOOL_OUTPUT_CHARS", "5000")
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-key")
        config = load()
        assert config.agent.context.max_tool_output_chars == 5000

    def test_env_context_max_tool_output_chars_zero_raises(
        self, fake_home: Path, monkeypatch
    ) -> None:
        """MINICODE_CONTEXT_MAX_TOOL_OUTPUT_CHARS=0 时 load() 抛出 ValidationError。"""
        from pydantic import ValidationError

        monkeypatch.setenv("MINICODE_CONTEXT_MAX_TOOL_OUTPUT_CHARS", "0")
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-key")
        with pytest.raises(ValidationError):
            load()


@pytest.mark.usefixtures("clean_minicode_env")
class TestPlaceholderResolution:
    """${ENV_VAR} 占位符解析。"""

    def test_env_placeholder_in_yaml(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证 YAML 中的 ${ENV_VAR} 占位符被正确解析。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-resolved")
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "providers": {
                    "deepseek": {
                        "api_key": "${DEEPSEEK_API_KEY}",
                        "base_url": "https://api.deepseek.com",
                        "models": ["deepseek-v4-flash"],
                    },
                },
            },
        )
        config = load()
        assert config.providers["deepseek"].api_key == "sk-resolved"

    def test_env_placeholder_missing_var(
        self, fake_home: Path
    ) -> None:
        """验证 ${ENV_VAR} 未设置时抛出 ConfigError，包含缺失的变量名。"""
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "providers": {
                    "deepseek": {
                        "api_key": "${UNDEFINED_VAR}",
                        "base_url": "https://api.deepseek.com",
                        "models": ["deepseek-v4-flash"],
                    },
                },
            },
        )
        with pytest.raises(ConfigError) as exc_info:
            load()
        msg = str(exc_info.value)
        assert "UNDEFINED_VAR" in msg
        assert "未设置" in msg


@pytest.mark.usefixtures("clean_minicode_env")
class TestCliOverride:
    """CLI 参数覆盖（最高优先级）。"""

    def test_cli_overrides_env(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证 CLI 参数覆盖环境变量。"""
        monkeypatch.setenv("MINICODE_DEFAULT_MODEL", "gpt-4o-from-env")
        monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-key")
        config = load(cli_overrides={"model": "gpt-4o-mini-from-cli"})
        assert config.default_model == "gpt-4o-mini-from-cli"

    def test_cli_overrides_yaml(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证 CLI 参数覆盖 YAML 配置。"""
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "default_provider": "openai",
                "providers": {
                    "openai": {
                        "api_key": "sk-key",
                    },
                },
            },
        )
        config = load(cli_overrides={"provider": "openai", "model": "cli-model"})
        assert config.default_provider == "openai"
        assert config.default_model == "cli-model"

    def test_cli_override_provider(self, fake_home: Path) -> None:
        """验证 CLI 可以切换默认提供商。"""
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "default_provider": "provider_a",
                "providers": {
                    "provider_a": {
                        "api_key": "sk-a",
                        "base_url": "https://a.com/v1",
                        "models": ["model-a"],
                    },
                    "provider_b": {
                        "api_key": "sk-b",
                        "base_url": "https://b.com/v1",
                        "models": ["model-b"],
                    },
                },
            },
        )
        config = load(cli_overrides={"provider": "provider_b"})
        assert config.default_provider == "provider_b"


@pytest.mark.usefixtures("clean_minicode_env")
class TestValidation:
    """配置验证。"""

    def test_missing_api_key_error_message(self, fake_home: Path) -> None:
        """验证缺少 API key 时错误信息包含提供建议。"""
        with pytest.raises(ConfigError) as exc_info:
            load()
        msg = str(exc_info.value)
        assert "未配置 API key" in msg
        assert "~/.minicode/config.yaml" in msg
        assert "./.minicode/config.yaml" in msg
        assert "--config" in msg
        assert "环境变量" in msg
        assert "DEEPSEEK_API_KEY" in msg

    def test_missing_provider_error(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证默认提供商未在 providers 中定义时抛出错误。"""
        monkeypatch.setenv("MINICODE_DEFAULT_PROVIDER", "nonexistent")
        with pytest.raises(ConfigError, match="未在配置中定义"):
            load()

    def test_invalid_yaml_error(self, fake_home: Path) -> None:
        """验证 YAML 格式错误时抛出 ConfigError。"""
        config_dir = fake_home / ".minicode"
        config_dir.mkdir(parents=True, exist_ok=True)
        with open(config_dir / "config.yaml", "w", encoding="utf-8") as f:
            f.write("invalid: yaml: : : broken")
        with pytest.raises(ConfigError, match="格式错误"):
            load()


@pytest.mark.usefixtures("clean_minicode_env")
class TestFullPriorityChain:
    """完整优先级链验证。"""

    def test_full_chain(
        self,
        fake_home: Path,
        fake_workspace: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """验证完整链条：默认 < 全局 < 项目 < 显式配置 < 环境变量 < CLI。"""
        # 第 1 层：默认值 -> default_model = "deepseek-v4-flash"

        # 第 2 层：全局配置
        _write_yaml(
            fake_home / ".minicode" / "config.yaml",
            {
                "default_provider": "openai",
                "default_model": "gpt-4o",
                "max_tokens": 4096,
                "providers": {
                    "openai": {
                        "api_key": "sk-global",
                        "base_url": "https://api.openai.com/v1",
                        "models": ["gpt-4o", "gpt-4o-mini"],
                    },
                },
            },
        )
        # 第 3 层：项目配置
        _write_yaml(
            fake_workspace / ".minicode" / "config.yaml",
            {
                "max_tokens": 8192,
            },
        )
        # 第 4 层：显式配置文件
        explicit = fake_workspace / "override.yaml"
        _write_yaml(
            explicit,
            {
                "default_model": "gpt-4o-from-explicit",
            },
        )
        # 第 5 层：环境变量
        monkeypatch.setenv("MINICODE_DEFAULT_MODEL", "gpt-4o-from-env")
        # 第 6 层：CLI 参数
        config = load(
            workspace=fake_workspace,
            config_path=explicit,
            cli_overrides={"model": "cli-model"},
        )

        assert config.default_provider == "openai"  # 全局配置覆盖
        assert config.default_model == "cli-model"  # CLI 最高优先级
        assert config.max_tokens == 8192  # 项目配置覆盖全局
        assert config.providers["openai"].api_key == "sk-global"  # 全局配置保留
        assert config.providers["openai"].base_url == "https://api.openai.com/v1"
