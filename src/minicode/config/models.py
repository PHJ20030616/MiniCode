"""配置系统的 Pydantic 数据模型。"""

from pydantic import BaseModel, Field


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


class PermissionsConfig(BaseModel):
    """权限控制配置。"""

    trust_mode: bool = False
    """是否跳过 caution/dangerous 工具的确认提示。"""


class AppConfig(BaseModel):
    """MiniCode 应用顶层配置。"""

    providers: dict[str, ProviderConfig] = Field(
        default_factory=lambda: {
            "openai": ProviderConfig(
                api_key="",
                base_url="https://api.openai.com/v1",
                models=["gpt-4o", "gpt-4o-mini"],
            ),
        }
    )
    """所有已配置的 AI 提供商，键为提供商名称。"""
    default_provider: str = "openai"
    """默认使用的提供商名称。"""
    default_model: str = "gpt-4o-mini"
    """默认使用的模型名称。"""
    max_tokens: int = 4096
    """模型响应的最大 token 数。"""
    agent: AgentConfig = Field(default_factory=AgentConfig)
    """Agent 循环相关配置。"""
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    """权限控制相关配置。"""
