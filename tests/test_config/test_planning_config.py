"""规划模式配置测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from minicode.agent.planning_models import ExecutionPlan, PlanningConfig, PlanStep
from minicode.config.loader import load
from minicode.config.models import AgentConfig, AppConfig


def test_planning_config_default_values() -> None:
    """默认开启规划模式，普通任务先计划再执行。"""
    cfg = PlanningConfig()

    assert cfg.enabled is True
    assert cfg.max_steps == 8
    assert cfg.max_tokens == 2048


def test_planning_config_custom_values() -> None:
    """允许用户通过配置控制规划开关和预算。"""
    cfg = PlanningConfig(enabled=True, max_steps=5, max_tokens=1024)

    assert cfg.enabled is True
    assert cfg.max_steps == 5
    assert cfg.max_tokens == 1024


def test_planning_config_rejects_invalid_step_count() -> None:
    """步骤数量至少为 1，避免生成空计划。"""
    with pytest.raises(ValidationError, match="max_steps"):
        PlanningConfig(max_steps=0)


def test_agent_config_contains_planning_config() -> None:
    """AgentConfig 应包含规划配置。"""
    cfg = AgentConfig()

    assert isinstance(cfg.planning, PlanningConfig)
    assert cfg.planning.enabled is True


def test_app_config_accepts_custom_planning_config() -> None:
    """AppConfig 支持嵌套传入规划配置。"""
    cfg = AppConfig(agent=AgentConfig(planning=PlanningConfig(enabled=True)))

    assert cfg.agent.planning.enabled is True


def test_execution_plan_to_markdown() -> None:
    """执行计划可以稳定渲染为中文 Markdown。"""
    plan = ExecutionPlan(
        goal="修复配置加载",
        steps=[
            PlanStep(index=1, title="阅读配置模型", description="确认默认值。"),
            PlanStep(index=2, title="补充测试", description="覆盖环境变量。"),
        ],
    )

    markdown = plan.to_markdown()

    assert "### 执行计划" in markdown
    assert "目标：修复配置加载" in markdown
    assert "1. 阅读配置模型" in markdown
    assert "确认默认值。" in markdown


@pytest.mark.usefixtures("clean_minicode_env")
def test_loader_reads_planning_values_from_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """项目配置文件可以覆盖规划模式配置。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-test")
    config_dir = tmp_path / ".minicode"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "agent:\n"
        "  planning:\n"
        "    enabled: false\n"
        "    max_steps: 5\n"
        "    max_tokens: 1024\n",
        encoding="utf-8",
    )

    cfg = load(workspace=tmp_path)

    assert cfg.agent.planning.enabled is False
    assert cfg.agent.planning.max_steps == 5
    assert cfg.agent.planning.max_tokens == 1024


@pytest.mark.usefixtures("clean_minicode_env")
def test_loader_reads_planning_values_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """环境变量可以覆盖规划配置。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("MINICODE_PLANNING_ENABLED", "true")
    monkeypatch.setenv("MINICODE_PLANNING_MAX_STEPS", "6")
    monkeypatch.setenv("MINICODE_PLANNING_MAX_TOKENS", "1536")

    cfg = load(workspace=tmp_path)

    assert cfg.agent.planning.enabled is True
    assert cfg.agent.planning.max_steps == 6
    assert cfg.agent.planning.max_tokens == 1536
