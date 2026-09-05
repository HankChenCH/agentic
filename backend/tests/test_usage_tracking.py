"""UsageTrackingChatModel：裸 invoke / with_structured_output 两条链路的用量捕获。"""

from types import SimpleNamespace
from uuid import uuid4

from app.adapters.llm.usage_tracking import (
    UsageTrackingChatModel,
    _SinkHandler,
    _usage_from_llm_result,
)
from conftest import TEST_USER_ID, StubUsageService
from app.domain.usage.extract import normalize_usage


class FakeInnerModel:
    """裸模型替身：invoke canned 回复；with_structured_output 回放假链。"""

    def __init__(self, reply="", usage_metadata=None):
        self.model_name = "fake-memory-model"
        self.reply = reply
        self.usage_metadata = usage_metadata
        self.schema = None

    def invoke(self, messages, **kwargs):
        return SimpleNamespace(content=self.reply, usage_metadata=self.usage_metadata)

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        return FakeStructuredChain()


class FakeStructuredChain:
    """with_structured_output 产物替身：捕获 invoke 收到的 config。"""

    def __init__(self):
        self.captured_config = None

    def invoke(self, user_input, config=None, **kwargs):
        self.captured_config = config
        return {"parsed": "ok"}


def _service():
    usage = StubUsageService()
    sink = usage.usage_sink(
        user_id=TEST_USER_ID, scene="memory", thread_id=uuid4(), turn_id=uuid4(),
    )
    return usage, sink


def test_bare_invoke_reports_normalized_usage():
    usage, sink = _service()
    inner = FakeInnerModel("抽取结果", usage_metadata={"input_tokens": 30, "output_tokens": 12, "total_tokens": 42})
    model = UsageTrackingChatModel(inner=inner, sink=sink)

    response = model.invoke([SimpleNamespace(content="hi")])

    assert response.content == "抽取结果"          # 透传不受影响
    rows = usage.records
    assert len(rows) == 1
    assert rows[0].scene == "memory"
    assert rows[0].model == "fake-memory-model"
    assert rows[0].usage == {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42}


def test_bare_invoke_without_usage_is_silent():
    usage, sink = _service()
    model = UsageTrackingChatModel(inner=FakeInnerModel("无用量"), sink=sink)

    model.invoke([])

    assert usage.records == []


def test_structured_chain_injects_callback_and_forwards():
    """with_structured_output 委托内部模型构建链，invoke 注入 on_llm_end 回调。"""
    usage, sink = _service()
    inner = FakeInnerModel()
    model = UsageTrackingChatModel(inner=inner, sink=sink)
    schema = object()

    chain = model.with_structured_output(schema=schema)
    assert inner.schema is schema                  # 链来自内部模型

    result = chain.invoke("输入")

    assert result == {"parsed": "ok"}
    config = chain._chain.captured_config
    handlers = [h for h in config["callbacks"] if isinstance(h, _SinkHandler)]
    assert len(handlers) == 1


def test_sink_handler_reads_usage_metadata_then_token_usage_fallback():
    usage, sink = _service()
    model = UsageTrackingChatModel(inner=FakeInnerModel(), sink=sink)

    # 形态一：generation 消息带 usage_metadata（langchain-core 新口径）
    result_msg = SimpleNamespace(generations=[[SimpleNamespace(
        message=SimpleNamespace(usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}),
    )]], llm_output=None)
    _SinkHandler(model).on_llm_end(result_msg)

    # 形态二：llm_output["token_usage"]（OpenAI 兼容族，旧口径回退）
    result_fallback = SimpleNamespace(
        generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata=None))]],
        llm_output={"token_usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}},
    )
    _SinkHandler(model).on_llm_end(result_fallback)

    assert [r.usage for r in usage.records] == [
        {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
        {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    ]


def test_sink_handler_reports_only_once():
    usage, sink = _service()
    model = UsageTrackingChatModel(inner=FakeInnerModel(), sink=sink)
    handler = _SinkHandler(model)
    result = SimpleNamespace(
        generations=[[SimpleNamespace(message=SimpleNamespace(
            usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
        ))]],
        llm_output=None,
    )

    handler.on_llm_end(result)
    handler.on_llm_end(result)  # 同一次 invoke 的多段事件只报一次

    assert len(usage.records) == 1


def test_normalize_usage_zero_payload_is_empty():
    assert normalize_usage({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}) == {}
    assert normalize_usage({"input_tokens": 1, "output_tokens": 2}) == {
        "prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3,
    }
    assert _usage_from_llm_result(SimpleNamespace(generations=[], llm_output=None)) == {}
