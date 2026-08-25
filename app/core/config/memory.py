from pydantic import BaseModel, Field


class MemoryConfig(BaseModel):
    """记忆系统配置。

    ``extraction_provider`` 引用 ``LLMConfig.providers`` 的一个
    entry key——记忆抽取复用对话模型供应商，不单独建供应商表；key 不存在时
    由 ModelFactory.create 抛出明确 ValueError。
    """

    enabled: bool = Field(
        default=True,
        description="是否启用记忆写入；关闭后 remember 跳过，召回自然为空",
    )
    extraction_provider: str = Field(
        default="deepseek-flash",
        description="记忆抽取使用的模型 entry key",
    )
