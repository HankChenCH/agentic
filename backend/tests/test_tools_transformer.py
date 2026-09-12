"""ToolsTransformer 基类：tools stream mode 投影与形态归一化。

纯单测：langgraph 的 ``StreamChannel.push`` 无论是否订阅都会触发
``_wire_fn``，测试借它捕获归一化产物（不经 mux，避免绑定/订阅仪式）。
子类 SupportToolsTransformer 的双内容覆写见 test_support_agent.py，消费者侧
（translator 映射）见 test_tool_result_dual_content.py——本文件只锁基类。
"""

from types import SimpleNamespace

from app.agents.tools_transformer import ToolsTransformer


def _tools_event(data: dict) -> dict:
    return {"method": "tools", "params": {"data": data}}


def _collect(transformer: ToolsTransformer) -> list[dict]:
    items: list[dict] = []
    transformer.channel._wire(items.append)
    return items


def test_required_stream_modes_and_init_registers_tools_channel():
    transformer = ToolsTransformer()
    assert transformer.required_stream_modes == ("tools",)
    channels = transformer.init()
    assert set(channels) == {"tools"}
    assert channels["tools"] is transformer.channel


def test_process_returns_true_for_any_event():
    transformer = ToolsTransformer()
    assert transformer.process({"method": "messages", "params": {}}) is True
    assert transformer.process(_tools_event({"event": "tool-started", "tool_call_id": "c", "tool_name": "t"})) is True


def test_non_tools_event_produces_nothing():
    transformer = ToolsTransformer()
    transformer.process({"method": "messages", "params": {"data": "whatever"}})
    assert _collect(transformer) == []


def test_tool_started_normalized():
    transformer = ToolsTransformer()
    items = _collect(transformer)
    transformer.process(_tools_event({"event": "tool-started", "tool_call_id": "c1", "tool_name": "knowledge_search"}))
    assert items == [{"event": "tool-started", "tool_call_id": "c1", "tool_name": "knowledge_search"}]


def test_tool_error_normalized():
    transformer = ToolsTransformer()
    items = _collect(transformer)
    transformer.process(_tools_event({"event": "tool-error", "tool_call_id": "c2"}))
    assert items == [{"event": "tool-error", "tool_call_id": "c2"}]


def test_tool_finished_splits_into_result_and_finished():
    """「带输出」与「收尾」拆两个事件：translator 分别映射
    ToolCallResult / ToolCallEnd；无关字段不泄漏进 result。"""
    transformer = ToolsTransformer()
    items = _collect(transformer)
    output = SimpleNamespace(content="检索到 2 条", artifact=None)
    transformer.process(_tools_event({"event": "tool-finished", "tool_call_id": "c3", "output": output}))

    assert items == [
        {"event": "tool-result", "tool_call_id": "c3", "content": "检索到 2 条"},
        {"event": "tool-finished", "tool_call_id": "c3"},
    ]


def test_tool_finished_plain_string_output():
    transformer = ToolsTransformer()
    items = _collect(transformer)
    transformer.process(_tools_event({"event": "tool-finished", "tool_call_id": "c4", "output": "ok"}))
    assert items[0]["content"] == "ok"


def test_tool_finished_artifact_rides_ui_channel():
    """content_and_artifact 形态：artifact（LLM 不可见的伴随载荷，如 A2UI
    消息数组）以 ui 键透传；无 artifact 则不带 ui 键。"""
    transformer = ToolsTransformer()
    items = _collect(transformer)
    artifact = {"a2ui": [{"surfaceId": "s1"}]}
    output = SimpleNamespace(content="ok", artifact=artifact)
    transformer.process(_tools_event({"event": "tool-finished", "tool_call_id": "c5", "output": output}))
    assert items[0]["ui"] == artifact

    # 空 artifact（含空 dict）不产生 ui 键
    transformer.process(_tools_event({"event": "tool-finished", "tool_call_id": "c6",
                                      "output": SimpleNamespace(content="ok", artifact={})}))
    assert "ui" not in items[2]


def test_unknown_tools_event_ignored():
    transformer = ToolsTransformer()
    items = _collect(transformer)
    transformer.process(_tools_event({"event": "tool-something-new", "tool_call_id": "c7"}))
    assert items == []
