from pathlib import Path

import minicode.prompts as prompts


def test_public_prompt_api_is_importable() -> None:
    expected = {
        "PLANNING_SYSTEM_PROMPT",
        "RESULT_JSON_INSTRUCTION",
        "SUMMARY_SYSTEM_PROMPT",
        "SUMMARY_WRAPPER_PREFIX",
        "ToolPromptInfo",
        "build_main_agent_prompt",
        "build_subagent_prompt",
        "build_summary_user_prompt",
    }

    assert set(prompts.__all__) == expected


def test_prompt_package_does_not_import_runtime_layers() -> None:
    prompt_root = Path(__file__).parents[2] / "src" / "minicode" / "prompts"
    forbidden = ("minicode.agent", "minicode.providers", "minicode.tools", "minicode.config")

    for path in prompt_root.glob("*.py"):
        source = path.read_text(encoding="utf-8-sig")
        assert not any(value in source for value in forbidden), path
