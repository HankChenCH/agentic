"""ModelFactory 实例管理：entry 解析 / 模型缓存 / task_type 校验。

纯单测：LangChain 模型构造惰性建连（构造不发网络请求），无需中间件。
"""

from dataclasses import dataclass

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from app.core.config import LLMConfig, LLMProviderEntry, ModelTaskType
from app.infrastructures.llm import ModelFactory
from app.infrastructures.llm.deepseek_provider import ThinkingAwareChatDeepSeek


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


def _capabilities_factory(thinkable: bool, features: list[str] | None = None) -> ModelFactory:
    capabilities = {"thinkable": thinkable, "features": features or ["function_calling"]}
    entry = LLMProviderEntry(
        type="deepseek", task_type=ModelTaskType.CHAT,
        model="test-chat", api_key="test-key", capabilities=capabilities,
    )
    config = LLMConfig(default="main", providers={"main": entry})
    return ModelFactory(app_config=_StubAppConfig(llm=config))


def _undeclared_factory() -> ModelFactory:
    entry = LLMProviderEntry(type="deepseek", task_type=ModelTaskType.CHAT, model="test-chat", api_key="test-key")
    config = LLMConfig(default="main", providers={"main": entry})
    return ModelFactory(app_config=_StubAppConfig(llm=config))


class _Payload(BaseModel):
    """with_structured_output 离线断言用的最小 wire schema。"""

    answer: str


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


def test_unknown_capability_feature_rejected():
    # features 白名单（Literal）：未知值在配置加载期 fail-fast，声明不腐化
    with pytest.raises(ValidationError):
        LLMProviderEntry(
            type="deepseek", task_type=ModelTaskType.CHAT,
            model="test-chat", api_key="test-key",
            capabilities={"thinkable": True, "features": ["function_caling"]},
        )


def test_capabilities_wired_into_model():
    # 能力声明落位到模型实例；未声明的 entry 缺省不可思考
    model = _capabilities_factory(True).create()
    assert isinstance(model, ThinkingAwareChatDeepSeek)
    assert model.thinkable is True
    plain = LLMProviderEntry(type="deepseek", task_type=ModelTaskType.CHAT, model="test-chat", api_key="test-key")
    config = LLMConfig(default="main", providers={"main": plain})
    assert ModelFactory(app_config=_StubAppConfig(llm=config)).create().thinkable is False


def test_structured_output_disables_thinking_on_thinkable_model():
    # 兼容规则（离线断言，不联网）：绑定里是关思考副本 + 强制 tool_choice
    structured = _capabilities_factory(True).create().with_structured_output(_Payload)
    binding = structured.steps[0]
    assert binding.bound.extra_body == {"thinking": {"type": "disabled"}}
    assert binding.bound.thinkable is True
    choice = binding.kwargs["tool_choice"]
    assert isinstance(choice, dict) and choice["type"] == "function"


def test_structured_output_preserves_existing_extra_body():
    # 副本合并既有 extra_body，不覆盖调用方经 overrides 注入的其他供应商参数
    model = _capabilities_factory(True).create(extra_body={"ttl": 1})
    structured = model.with_structured_output(_Payload)
    assert structured.steps[0].bound.extra_body == {"ttl": 1, "thinking": {"type": "disabled"}}


def test_structured_output_untouched_without_thinkable():
    # 非 thinkable：无互斥，不注入任何 extra_body
    structured = _capabilities_factory(False).create().with_structured_output(_Payload)
    binding = structured.steps[0]
    assert binding.bound.extra_body is None
    assert binding.bound.thinkable is False


def test_method_self_discovery_prefers_thinking_compatible():
    # 自发现：thinkable 时按声明顺序跳过思考互斥项，选中 json_mode
    # （response_format 通道，思考保持开启，无需关思考副本）
    model = _capabilities_factory(True, features=["json_mode", "function_calling"]).create()
    structured = model.with_structured_output(_Payload)
    binding = structured.steps[0]
    assert binding.kwargs.get("response_format") == {"type": "json_object"}
    assert binding.kwargs.get("tool_choice") is None
    assert binding.bound.extra_body is None


def test_method_self_discovery_falls_back_and_disables_thinking():
    # 声明的 method 全部与思考互斥：回退首个，并以关思考副本执行
    model_a = _capabilities_factory(True, features=["json_schema", "function_calling"]).create()
    model_b = _capabilities_factory(True, features=["json_schema"]).create()
    for model in (model_a, model_b):
        binding = model.with_structured_output(_Payload).steps[0]
        assert binding.kwargs.get("tool_choice") is not None  # 强制 tool_choice 通道
        assert binding.bound.extra_body == {"thinking": {"type": "disabled"}}


def test_undeclared_method_fails_fast():
    # 显式 method 未声明：fail-fast 而非静默降级（声明与调用不一致属配置错误）
    model = _capabilities_factory(True, features=["function_calling"]).create()
    with pytest.raises(ValueError, match="not declared"):
        model.with_structured_output(_Payload, method="json_mode")


def test_json_schema_alias_normalizes_to_function_calling():
    # langchain-deepseek 把 json_schema 重映射为 function_calling：
    # 声明 function_calling 即覆盖 json_schema 调用，thinkable 下同样关思考
    model = _capabilities_factory(True, features=["function_calling"]).create()
    structured = model.with_structured_output(_Payload, method="json_schema")
    binding = structured.steps[0]
    assert binding.kwargs.get("tool_choice") is not None
    assert binding.bound.extra_body == {"thinking": {"type": "disabled"}}


def test_no_declaration_keeps_parent_behavior():
    # 未声明 features：不发现不校验（值合法性由 langchain 自身把关），沿用父类默认
    model = _undeclared_factory().create()
    default = model.with_structured_output(_Payload).steps[0]
    assert default.kwargs.get("tool_choice") is not None  # 父类默认 function_calling
    json_mode = model.with_structured_output(_Payload, method="json_mode").steps[0]
    assert json_mode.kwargs.get("response_format") == {"type": "json_object"}
    with pytest.raises(ValueError, match="Unrecognized method"):
        model.with_structured_output(_Payload, method="nope")  # langchain 自身校验 method 值
