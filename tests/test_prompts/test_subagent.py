from minicode.prompts import build_subagent_prompt


def test_subagent_prompt_contains_task_role_tools_and_output_contract() -> None:
    prompt = build_subagent_prompt(
        name="researcher",
        role="researcher",
        allowed_tools=["read_file", "grep"],
        output_schema="summary_findings",
        task="检查 prompt 组织方式",
    )

    assert "子代理名称：researcher" in prompt
    assert "角色：researcher" in prompt
    assert "允许工具：read_file, grep" in prompt
    assert "委派任务：\n检查 prompt 组织方式" in prompt
    assert '"summary": "一句话总结"' in prompt
    assert "最终回答必须只输出一个 JSON 对象" in prompt


def test_subagent_prompt_renders_empty_allowed_tools_without_extra_dependency() -> None:
    prompt = build_subagent_prompt(
        name="tester",
        role="tester",
        allowed_tools=[],
        output_schema="review_findings",
        task="检查测试",
    )

    assert "允许工具：" in prompt
    assert "检查测试" in prompt


def test_legacy_subagent_adapter_delegates_without_changing_signature() -> None:
    from minicode.agent.subagents.models import SubagentRole, SubagentTask
    from minicode.agent.subagents.prompts import build_subagent_system_prompt

    task = SubagentTask(name="reviewer", task="审查代码", role=SubagentRole.REVIEWER)
    assert build_subagent_system_prompt(task, ["grep"]) == build_subagent_prompt(
        name=task.name,
        role=task.role.value,
        allowed_tools=["grep"],
        output_schema=task.output_schema,
        task=task.task,
    )
