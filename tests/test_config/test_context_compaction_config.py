"""上下文压缩配置与报告模型测试。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from minicode.agent.context_models import (
    DEFAULT_CLEANUP_TOOLS,
    CompactionConfig,
    CompactionReport,
    CompactionTrigger,
)
from minicode.config.loader import load


def test_compaction_config_default_values() -> None:
    """压缩配置应提供稳定且彼此独立的默认值。"""
    config = CompactionConfig()

    assert config.auto_enabled is True
    assert config.trigger_ratio == 0.90
    assert config.target_ratio == 0.60
    assert config.summary_max_tokens == 2048
    assert config.cleanup_tools == ["read_file", "grep", "glob", "shell"]
    assert config.cleanup_tools == DEFAULT_CLEANUP_TOOLS
    assert config.cleanup_tools is not DEFAULT_CLEANUP_TOOLS


def test_compaction_config_normalizes_cleanup_tools() -> None:
    """工具名称应去除空白，并按首次出现顺序去重。"""
    config = CompactionConfig(cleanup_tools=[" read_file ", "grep", "read_file"])

    assert config.cleanup_tools == ["read_file", "grep"]


@pytest.mark.parametrize(
    ("target_ratio", "trigger_ratio"),
    [
        (0.0, 0.9),
        (0.6, 0.6),
        (0.9, 0.6),
        (0.6, 1.0),
    ],
)
def test_compaction_config_rejects_invalid_ratios(
    target_ratio: float,
    trigger_ratio: float,
) -> None:
    """目标占用率必须有效且严格低于触发占用率。"""
    with pytest.raises(ValidationError):
        CompactionConfig(
            target_ratio=target_ratio,
            trigger_ratio=trigger_ratio,
        )


def test_compaction_report_serializes_trigger_and_utc_time_to_json() -> None:
    """压缩报告 JSON 应保留枚举值和 UTC 时间格式。"""
    report = CompactionReport(
        trigger=CompactionTrigger.MANUAL,
        created_at=datetime(2026, 7, 16, tzinfo=UTC),
        before_tokens=1000,
        after_tokens=500,
        before_message_count=20,
        after_message_count=8,
        summarized_message_count=12,
        cleared_tool_result_count=3,
        unconsumed_tool_result_count=1,
        retry_used=False,
        target_reached=True,
        focus_provided=False,
    )

    payload = json.loads(report.model_dump_json())

    assert payload["trigger"] == "manual"
    assert payload["created_at"] == "2026-07-16T00:00:00Z"


@pytest.mark.usefixtures("clean_minicode_env")
def test_loader_reads_compaction_values_from_project_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """项目 YAML 应完整加载嵌套的上下文压缩配置。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("MINICODE_DEEPSEEK_API_KEY", "sk-test")
    config_dir = tmp_path / ".minicode"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "agent:\n"
        "  context:\n"
        "    compaction:\n"
        "      auto_enabled: false\n"
        "      trigger_ratio: 0.85\n"
        "      target_ratio: 0.55\n"
        "      summary_max_tokens: 1024\n"
        "      cleanup_tools:\n"
        "        - read_file\n"
        "        - shell\n",
        encoding="utf-8",
    )

    config = load(workspace=tmp_path)

    assert config.agent.context.compaction.auto_enabled is False
    assert config.agent.context.compaction.trigger_ratio == 0.85
    assert config.agent.context.compaction.target_ratio == 0.55
    assert config.agent.context.compaction.summary_max_tokens == 1024
    assert config.agent.context.compaction.cleanup_tools == ["read_file", "shell"]
