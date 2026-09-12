"""ChatModelGateway 适配器与 openai/ollama 供应商构建器。

纯单测：LangChain 模型构造惰性建连，哑 key 即可断言产物类型与配置透传
（deepseek 主路径已在 test_model_factory.py 覆盖，此处补齐其余两个注册
供应商与网关的按用途路由委托）。
"""

from dataclasses import dataclass

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_ollama import ChatOllama, OllamaEmbeddings

from app.core.config import LLMProviderEntry, ModelTaskType
from app.adapters.llm import ModelFactory
from app.adapters.llm.gateway import DefaultChatModelGateway
from app.adapters.llm.ollama_provider import OllamaModelBuilder
from app.adapters.llm.openai_provider import OpenAIModelBuilder


@dataclass
class _StubAppConfig:
    llm: object


def _entry(type_: str, **overrides) -> LLMProviderEntry:
    base = dict(type=type_, task_type=ModelTaskType.CHAT, model="test-model", api_key="test-key",
                api_url="https://gw.invalid/v1")
    base.update(overrides)
    return LLMProviderEntry(**base)


# ---------- DefaultChatModelGateway：按用途路由的委托 ----------


def test_gateway_delegates_to_factory_with_provider():
    """缺省走 default entry，指名即按用途路由（memory.extraction_provider 先例）。"""
    calls = []

    class RecordingFactory:
        def create(self, provider=None):
            calls.append(provider)
            return "model-instance"

    gateway = DefaultChatModelGateway(model_factory=RecordingFactory())

    assert gateway.create() == "model-instance"
    assert gateway.create("memory-extraction") == "model-instance"
    assert calls == [None, "memory-extraction"]


def test_gateway_produces_chat_model_via_real_factory():
    config = _StubAppConfig(llm=type("C", (), {"default": "main", "providers": {"main": _entry("deepseek")}})())
    gateway = DefaultChatModelGateway(model_factory=ModelFactory(app_config=config))
    assert isinstance(gateway.create(), BaseChatModel)


# ---------- OpenAI 构建器（OpenAI 兼容网关） ----------


def test_openai_build_chat():
    entry = _entry("openai", timeout=30)
    model = OpenAIModelBuilder().build_chat(entry)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "test-model"
    assert model.openai_api_base == "https://gw.invalid/v1"
    assert model.request_timeout == 30.0


def test_openai_build_embedding():
    entry = _entry("openai", task_type=ModelTaskType.EMBEDDING)
    embeddings = OpenAIModelBuilder().build_embedding(entry)
    assert isinstance(embeddings, Embeddings)
    assert isinstance(embeddings, OpenAIEmbeddings)


def test_openai_context_window_lands_on_profile():
    entry = _entry("openai", context_window=8192)
    model = OpenAIModelBuilder().build_chat(entry)
    assert model.profile["max_input_tokens"] == 8192


# ---------- Ollama 构建器（本地部署，api_key 可省略） ----------


def test_ollama_build_chat_without_api_key():
    entry = LLMProviderEntry(type="ollama", task_type=ModelTaskType.CHAT, model="bge-m3",
                             api_url="http://127.0.0.1:11434")
    model = OllamaModelBuilder().build_chat(entry)
    assert isinstance(model, ChatOllama)
    assert model.model == "bge-m3"
    assert model.base_url == "http://127.0.0.1:11434"


def test_ollama_timeout_goes_through_client_kwargs():
    """ollama 客户端超时不在模型字段上，经 client_kwargs 传给底层 httpx。"""
    entry = _entry("ollama", api_url="http://127.0.0.1:11434", timeout=15)
    model = OllamaModelBuilder().build_chat(entry)
    assert model.client_kwargs == {"timeout": 15}

    # 缺省 120s 同样透传（builder 的 None 分支是防御性写法，配置面必填）
    no_timeout = LLMProviderEntry(type="ollama", task_type=ModelTaskType.CHAT, model="m",
                                  api_url="http://127.0.0.1:11434")
    assert OllamaModelBuilder().build_chat(no_timeout).client_kwargs == {"timeout": 120}


def test_ollama_build_embedding():
    entry = _entry("ollama", task_type=ModelTaskType.EMBEDDING, api_url="http://127.0.0.1:11434")
    assert isinstance(OllamaModelBuilder().build_embedding(entry), OllamaEmbeddings)


# ---------- 工厂路由：task_type 面对全部注册供应商一致 ----------


def test_factory_routes_openai_and_ollama_entries():
    config = _StubAppConfig(llm=type("C", (), {"default": "openai-chat", "providers": {
        "openai-chat": _entry("openai"),
        "openai-embed": _entry("openai", task_type=ModelTaskType.EMBEDDING),
        "ollama-chat": _entry("ollama", api_url="http://127.0.0.1:11434"),
    }})())
    factory = ModelFactory(app_config=config)

    assert isinstance(factory.create("openai-chat"), ChatOpenAI)
    assert isinstance(factory.create("ollama-chat"), ChatOllama)
    assert isinstance(factory.create_embeddings("openai-embed"), OpenAIEmbeddings)
    with pytest.raises(ValueError, match="task_type is 'chat', expected 'embedding'"):
        factory.create_embeddings("ollama-chat")
