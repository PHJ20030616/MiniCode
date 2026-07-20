from minicode.prompts import PLANNING_SYSTEM_PROMPT


def test_planning_prompt_requires_plain_json_plan() -> None:
    assert "只输出 JSON" in PLANNING_SYSTEM_PROMPT
    assert '{"goal": "...", "steps": [{"title": "...", "description": "..."}]}' in (
        PLANNING_SYSTEM_PROMPT
    )


def test_planning_prompt_forbids_tools_and_requires_execution_oriented_steps() -> None:
    assert "不要调用工具" in PLANNING_SYSTEM_PROMPT
    assert "阅读、修改、验证" in PLANNING_SYSTEM_PROMPT


def test_planner_module_reuses_the_public_prompt_constant() -> None:
    from minicode.agent import planner

    assert planner.PLANNING_SYSTEM_PROMPT is PLANNING_SYSTEM_PROMPT
