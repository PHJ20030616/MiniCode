"""MiniCode 的统一 prompt 构建入口。"""

from minicode.prompts.compaction import (
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_WRAPPER_PREFIX,
    build_summary_user_prompt,
)
from minicode.prompts.main_agent import build_main_agent_prompt
from minicode.prompts.models import ToolPromptInfo
from minicode.prompts.planning import PLANNING_SYSTEM_PROMPT
from minicode.prompts.subagent import RESULT_JSON_INSTRUCTION, build_subagent_prompt

__all__ = [
    "PLANNING_SYSTEM_PROMPT",
    "RESULT_JSON_INSTRUCTION",
    "SUMMARY_SYSTEM_PROMPT",
    "SUMMARY_WRAPPER_PREFIX",
    "ToolPromptInfo",
    "build_main_agent_prompt",
    "build_subagent_prompt",
    "build_summary_user_prompt",
]
