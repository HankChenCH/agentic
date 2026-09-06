from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.config import LLMProviderEntry


class ModelProvider(str, Enum):
    """模型供应商标识，与 ``LLMProviderEntry.type`` 对应。"""

    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    OLLAMA = "ollama"


class ModelBuilder(ABC):
    """模型构建器：每个供应商一个子类，把 provider entry 翻译成具体模型实例。

    子类声明 ``provider``，并按 ``LLMProviderEntry.task_type`` 实现对应的
    build 方法（chat 为必备能力，embedding 等按供应商能力选配，不支持时
    保留基类默认抛错即可）；entry 已携带全部直连参数
    （api_url / api_key / model / task_type），构建器自身无需再读全局配置。
    """

    provider: ClassVar[ModelProvider]

    @abstractmethod
    def build_chat(self, entry: LLMProviderEntry, **overrides: Any) -> BaseChatModel:
        """按 entry 创建 chat 模型实例。"""

    def build_embedding(self, entry: LLMProviderEntry, **overrides: Any) -> Embeddings:
        """按 entry 创建 embedding 模型实例，供应商不提供该能力时抛错。"""
        raise NotImplementedError(f"provider '{self.provider.value}' 不提供 embedding 模型")

    @staticmethod
    def apply_context_window(entry: LLMProviderEntry, overrides: dict[str, Any]) -> None:
        """把 entry 的上下文窗口声明合入 chat 构建参数（显式传入优先）。

        窗口以模型 profile 的 ``max_input_tokens`` 表达——langchain 机制
        （如 agent 的 SummarizationMiddleware fraction 触发阈值）经
        ``model.profile`` 消费。缺省不动：沿用模型类内置 profile 表
        （deepseek/openai 自带供应商数据；ollama 无内置则 profile 为空）。
        """
        if entry.context_window is not None:
            overrides.setdefault("profile", {"max_input_tokens": entry.context_window})


# 供应商注册表：@register 自动登记，新增供应商不改工厂
MODEL_BUILDERS: dict[ModelProvider, type[ModelBuilder]] = {}


def register(builder_cls: type[ModelBuilder]) -> type[ModelBuilder]:
    MODEL_BUILDERS[builder_cls.provider] = builder_cls
    return builder_cls
