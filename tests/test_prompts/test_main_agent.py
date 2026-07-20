from minicode.prompts import ToolPromptInfo, build_main_agent_prompt


def test_main_agent_prompt_contains_base_identity_without_tools() -> None:
    prompt = build_main_agent_prompt([])

    assert "你是 MiniCode，一个轻量级的 AI 编程助手。" in prompt
    assert "## 可用工具" not in prompt


def test_main_agent_prompt_renders_tools_in_given_order() -> None:
    prompt = build_main_agent_prompt(
        [
            ToolPromptInfo(name="grep", description="搜索内容"),
            ToolPromptInfo(name="read_file", description="读取文件"),
        ]
    )

    assert prompt.index("grep: 搜索内容") < prompt.index("read_file: 读取文件")
    assert "请根据用户的问题选择合适的工具。" in prompt


def test_main_agent_prompt_only_adds_memory_rules_when_remember_is_available() -> None:
    with_remember = build_main_agent_prompt(
        [ToolPromptInfo(name="remember", description="保存记忆")]
    )
    without_remember = build_main_agent_prompt(
        [ToolPromptInfo(name="grep", description="搜索内容")]
    )

    assert "### 记忆工具使用说明" in with_remember
    assert "不要将普通聊天" in with_remember
    assert "### 记忆工具使用说明" not in without_remember


def test_main_agent_prompt_includes_optional_sections() -> None:
    prompt = build_main_agent_prompt(
        [ToolPromptInfo(name="grep", description="搜索内容")],
        memory_content="偏好：使用中文",
        subagent_enabled=True,
    )

    assert "### 子代理委派准则" in prompt
    assert "## 用户记忆" in prompt
    assert "偏好：使用中文" in prompt
    assert "可能不完整或过期" in prompt


def test_main_agent_prompt_omits_memory_when_disabled() -> None:
    prompt = build_main_agent_prompt(
        [ToolPromptInfo(name="remember", description="保存记忆")],
        memory_content="不应注入",
        memory_enabled=False,
    )

    assert "remember: 保存记忆" not in prompt
    assert "记忆工具使用说明" not in prompt
    assert "不应注入" not in prompt
