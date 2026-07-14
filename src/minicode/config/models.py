"""配置系统的 Pydantic 数据模型。"""

from pydantic import BaseModel, Field

from minicode.agent.context_models import ContextConfig
from minicode.agent.planning_models import PlanningConfig


class ProviderConfig(BaseModel):
    """AI 提供商连接配置。"""

    api_key: str = ""
    """"""
    base_url: str = ""
    """API 请求的基础地址。"""
    models: list[str] = []
    """该提供商支持的模型列表。"""


class AgentConfig(BaseModel):
    """Agent 循环行为配置。"""

    max_rounds: int = 20
    """Agent Loop 最大迭代轮次。"""
    stream: bool = True
    """是否启用流式输出。"""
    context: ContextConfig = Field(default_factory=ContextConfig)
    """上下文窗口管理配置。"""
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    """任务规划配置。"""


class PermissionsConfig(BaseModel):
    """权限控制配置。"""

    trust_mode: bool = False
    """是否跳过 caution/dangerous 工具的确认提示。"""


class MemoryConfig(BaseModel):
    """记忆系统配置。"""

    enabled: bool = True
    """是否启用记忆系统。"""
    max_chars: int = 8000
    """注入 Agent 系统提示词时记忆内容的最大字符数。"""


class AppConfig(BaseModel):
    """MiniCode 应用顶层配置。"""

    providers: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {
            "openai": ProviderConfig(
                api_key="",
                base_url="https://api.openai.com/v1",
                models=["gpt-4o", "gpt-4o-mini"],
            ),
            "deepseek": ProviderConfig(
                api_key="",
                base_url="https://api.deepseek.com",
                models=["deepseek-v4-flash","deepseek-v4-pro"],
            ),
        }
    )
    """所有已配置的 AI 提供商，键为提供商名称。"""
    default_provider: str = "deepseek"
    """默认使用的提供商名称。"""
    default_model: str = "deepseek-v4-flash"
    """默认使用的模型名称。"""
    max_tokens: int = 16384
    """模型响应的最大 token 数。"""
    agent: AgentConfig = Field(default_factory=AgentConfig)
    """Agent 循环相关配置。"""
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    """权限控制相关配置。"""
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    """记忆系统相关配置。"""
