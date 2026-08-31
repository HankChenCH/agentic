from enum import Enum
from typing import Literal

from pydantic import BaseModel, SecretStr, Field, model_validator

_DEEPSEEK_DEFAULT_API_URL = "https://api.deepseek.com"


class ModelTaskType(str, Enum):
    """模型任务类型（类似 huggingface 的 task 分类）：entry 声明自身用途，
    工厂据此校验调用方式并路由到对应的构建方法。

    ``rerank`` 仅为配置预留（声明条目用途、使校验放行）；工厂尚未支持
    rerank 构建，消费方出现前不参与装配。
    """

    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class LLMCapabilities(BaseModel):
    """模型/部署的能力声明（描述事实，不携带调用策略）。

    ``thinkable``：模型支持思考模式；``features``：该模型支持的
    ``with_structured_output`` method 白名单（function_calling / json_schema /
    json_mode，按声明顺序表达自发现优先级）。消费规则归属供应商模型类：
    ``ThinkingAwareChatDeepSeek`` 据此自发现 method、对显式 method 做声明
    校验（fail-fast），并对与思考互斥的 method（强制 tool_choice 通道）自动
    以关思考副本执行。``features`` 为 Literal 白名单，未知值配置加载期
    fail-fast；openai/ollama builder 共享本 schema，暂不消费这些声明。
    """

    thinkable: bool = Field(default=False, description="模型是否支持思考模式")
    features: list[Literal["function_calling", "json_schema", "json_mode"]] = Field(
        default_factory=list,
        description="支持的 with_structured_output method（声明顺序即自发现优先级）",
    )


class LLMProviderEntry(BaseModel):
    """一个具名的模型供应商实例。

    ``type`` 是供应商标识（决定使用哪个 ModelBuilder），``task_type`` 声明
    模型用途（chat / embedding），其余字段为直连参数；一个供应商可配置
    多个 entry（如 flash / pro 两档模型）。数据来源为
    ``app/configs/llm.yaml``，密钥通过 ``$VAR`` 插值取自环境。
    """
    type: str = Field(default="deepseek", description="供应商标识，决定使用哪个模型构建器")
    task_type: ModelTaskType = Field(
        default=ModelTaskType.CHAT,
        description="模型任务类型：chat / embedding，决定可被工厂的哪个方法创建",
    )
    api_url: str = Field(
        default=_DEEPSEEK_DEFAULT_API_URL,
        description="API base URL，eg: https://api.deepseek.com",
    )
    api_key: SecretStr = Field(
        default=SecretStr(""),
        description="API key；本地部署的供应商（如 ollama）可省略",
    )
    model: str = Field(description="模型名，eg: deepseek-v4-flash")
    timeout: float = Field(
        default=120.0,
        description="单次请求超时（秒），防止上游模型/网关无响应时调用方无限挂起",
    )
    capabilities: LLMCapabilities = Field(
        default_factory=LLMCapabilities,
        description="模型/部署能力声明，由供应商模型类消费（如 ThinkingAwareChatDeepSeek）",
    )


class LLMConfig(BaseModel):
    """模型供应商配置：default 引用 providers 里的一个 entry key。"""
    default: str = Field(
        default="deepseek-flash",
        description="默认 entry key，未显式指定模型时使用",
    )
    providers: dict[str, LLMProviderEntry] = Field(description="具名模型供应商实例表")

    @model_validator(mode="after")
    def _default_must_exist(self):
        if self.default not in self.providers:
            raise ValueError(f"default provider '{self.default}' not in providers: {list(self.providers)}")
        return self
