"""ModelFactory 实例管理：entry 解析 / 模型缓存 / task_type 校验。

纯单测：LangChain 模型构造惰性建连（构造不发网络请求），无需中间件。
"""

from dataclasses import dataclass

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.config import LLMConfig, LLMProviderEntry, ModelTaskType
from app.infrastructures.llm import ModelFactory


@dataclass
class _StubAppConfig:
    """最小 AppConfig 替身：工厂只消费 llm 分节，避免单测加载全量 yaml。"""

    llm: LLMConfig


def _entry(model: str, task_type: ModelTaskType = ModelTaskType.CHAT, type_: str = "deepseek") -> LLMProviderEntry:
    return LLMProviderEntry(type=type_, task_type=task_type, model=model, api_key="test-key")


def _factory(default: str = "main") -> ModelFactory:
    config = LLMConfig(
        default=default,
        providers={
            "main": _entry("test-chat"),
            "other": _entry("test-chat-2"),
            "embed": _entry("test-embedding", task_type=ModelTaskType.EMBEDDING, type_="openai"),
        },
    )
    return ModelFactory(app_config=_StubAppConfig(llm=config))


def test_create_returns_chat_model():
    assert isinstance(_factory().create(), BaseChatModel)


def test_model_cached_per_entry():
    # 模型内含 HTTP 连接池（重资源），同一 entry 复用同一实例
    factory = _factory()

    assert factory.create() is factory.create()
    assert factory.create("main") is factory.create("main")
    assert factory.create("main") is not factory.create("other")


def test_embeddings_cached_per_entry():
    factory = _factory()

    embeddings = factory.create_embeddings("embed")
    assert isinstance(embeddings, Embeddings)
    assert embeddings is factory.create_embeddings("embed")


def test_overrides_bypass_cache():
    # 定制参数不定型：带 overrides 的调用不读也不写缓存
    factory = _factory()

    first = factory.create(temperature=0.1)
    second = factory.create(temperature=0.2)

    assert first is not second
    assert factory.create() is not first  # 缓存未被 overrides 结果污染


def test_task_type_mismatch_raises():
    with pytest.raises(ValueError, match="task_type is 'chat', expected 'embedding'"):
        _factory().create_embeddings("main")


def test_unknown_key_raises():
    with pytest.raises(ValueError, match="unknown llm provider: nope"):
        _factory().create("nope")
