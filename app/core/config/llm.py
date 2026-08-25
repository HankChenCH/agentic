from enum import Enum

from pydantic import BaseModel, SecretStr, Field, model_validator

_DEEPSEEK_DEFAULT_API_URL = "https://api.deepseek.com"


class ModelTaskType(str, Enum):
    """模型任务类型（类似 huggingface 的 task 分类）：entry 声明自身用途，
    工厂据此校验调用方式并路由到对应的构建方法。
    """

    CHAT = "chat"
    EMBEDDING = "embedding"


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
