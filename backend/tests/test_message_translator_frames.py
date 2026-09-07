"""翻译器纯函数内核的直接断言（统一表达：每条边 = 模块级转换函数）。

ag2ui 的 ``message_frame_events`` / ``tool_frame_events`` 与 ag2store 的
``fold_messages_frame`` / ``fold_tools_frame`` 是两条流式边的纯转换内核；
类（AgUiTranslator / StorageTranslator）只是薄驱动器（配置 + 显式状态 +
逐帧分派）。此处直接断言函数产物（事件对象 / 落库行），与批量边
（store2ui/store2ag）的函数级测试口径一致；经驱动器的端到端行为另见
test_rag_agent.py / test_storage_translator_usage.py / test_tool_result_dual_content.py。
"""

from types import SimpleNamespace
from uuid import uuid4

from ag_ui.core import (
    CustomEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from app.application.translator.agui_translator import (
    message_frame_events,
    tool_frame_events,
)
from app.application.translator.matrix import a2ui_messages
from app.application.translator.storage_translator import (
    StorageContext,
    StorageState,
    UsageContext,
    fold_messages_frame,
    fold_tools_frame,
)
from app.models.domain.agentic import AgenticMessageRole, AgenticMessageType

_MSG_ID = uuid4()


def _delta_stream(reasoning: list, text: list) -> SimpleNamespace:
    """ag2ui 视角的流替身：reasoning/text 投影是逐 delta 可迭代。"""
    return SimpleNamespace(reasoning=reasoning, text=text)


def _row_stream(reasoning="", text="", tool_calls=(), usage_metadata=None) -> SimpleNamespace:
    """ag2store 视角的流替身：message-finish 后非阻塞读的终值形态。"""
    return SimpleNamespace(
        reasoning=reasoning,
        text=text,
        tool_calls=SimpleNamespace(get=lambda: list(tool_calls)),
        output_message=None if usage_metadata is None else SimpleNamespace(usage_metadata=usage_metadata),
    )


def _ctx(usage=None) -> StorageContext:
    return StorageContext(thread_id=uuid4(), turn_id=uuid4(), usage=usage)


# ---------------------------------------------------------------- ag2ui 内核
def test_message_frame_events_frames_reasoning_then_text():
    """reasoning 先、text 后，各自 Start/delta*/End 成对；事件归属注入 id。"""
    events = list(message_frame_events(_MSG_ID, _delta_stream(["想", "法"], ["答"])))
    assert [type(e) for e in events] == [
        ReasoningMessageStartEvent,
        ReasoningMessageContentEvent,
        ReasoningMessageContentEvent,
        ReasoningMessageEndEvent,
        TextMessageStartEvent,
        TextMessageContentEvent,
        TextMessageEndEvent,
    ]
    assert [e.delta for e in events if isinstance(e, ReasoningMessageContentEvent)] == ["想", "法"]
    assert [e.delta for e in events if isinstance(e, TextMessageContentEvent)] == ["答"]
    assert all(e.message_id == _MSG_ID.hex for e in events)


def test_message_frame_events_skips_empty_projections():
    """投影为空（无对应内容）则 Start/End 也不发——流式形态与落库拆条对齐。"""
    assert list(message_frame_events(_MSG_ID, _delta_stream([], []))) == []


def test_tool_frame_events_dual_content_and_a2ui_custom():
    """双内容契约：流式只发展示版 display，缺省回退 content；携带 ui.a2ui 时
    CUSTOM 事件紧跟 TOOL_CALL_RESULT 之后。"""
    payload = {
        "event": "tool-result", "tool_call_id": "c1",
        "content": "真实", "display": "展示", "ui": {"a2ui": [{"v": 1}]},
    }
    events = list(tool_frame_events(_MSG_ID, payload))
    assert [type(e) for e in events] == [ToolCallResultEvent, CustomEvent]
    assert events[0].message_id == _MSG_ID.hex and events[0].tool_call_id == "c1"
    assert events[0].content == "展示"
    assert events[1].name == "a2ui" and events[1].value == [{"v": 1}]

    plain = list(tool_frame_events(_MSG_ID, {"event": "tool-result", "tool_call_id": "c1", "content": "真实"}))
    assert [type(e) for e in plain] == [ToolCallResultEvent]
    assert plain[0].content == "真实"


def test_tool_frame_events_start_finish_error_and_unknown():
    """started → ToolCallStart（parent = 注入 id）；finished/error 均只收尾
    tool-call；未知事件类型不产事件。"""
    started = list(tool_frame_events(_MSG_ID, {"event": "tool-started", "tool_call_id": "c1", "tool_name": "get_weather"}))
    assert [type(e) for e in started] == [ToolCallStartEvent]
    assert started[0].parent_message_id == _MSG_ID.hex and started[0].tool_call_name == "get_weather"

    finished = list(tool_frame_events(_MSG_ID, {"event": "tool-finished", "tool_call_id": "c1"}))
    errored = list(tool_frame_events(_MSG_ID, {"event": "tool-error", "tool_call_id": "c1"}))
    assert [type(e) for e in finished + errored] == [ToolCallEndEvent, ToolCallEndEvent]
    assert all(e.tool_call_id == "c1" for e in finished + errored)

    assert list(tool_frame_events(_MSG_ID, {"event": "other"})) == []


def test_a2ui_messages_shared_rule():
    """ag2ui/ag2store 共享的识别规则：空数组/形态不符/缺 ui 视同未携带。"""
    assert a2ui_messages({"ui": {"a2ui": [{"k": 1}]}}) == [{"k": 1}]
    assert a2ui_messages({"ui": {"a2ui": []}}) is None
    assert a2ui_messages({"ui": "bad"}) is None
    assert a2ui_messages({}) is None


# ------------------------------------------------------------ ag2store 内核
def test_fold_messages_frame_splits_thought_message_tool_call():
    """拆条规则：THOUGHT/MESSAGE/TOOL_CALL 各一行，parent 链 thought→message→
    tool_call；注入 id 归 text 行，thought 持独立 uuid；sequence_num 严格递增。"""
    state, ctx = StorageState(), _ctx()
    fold_messages_frame(state, ctx, _MSG_ID, _row_stream(
        reasoning="想", text="答",
        tool_calls=[{"id": "c1", "name": "get_weather", "args": {"city": "中山"}}],
    ))

    assert [m.message_type for m in state.messages] == [
        AgenticMessageType.THOUGHT, AgenticMessageType.MESSAGE, AgenticMessageType.TOOL_CALL,
    ]
    thought, message, tool_call = state.messages
    assert thought.message_id != _MSG_ID and message.message_id == _MSG_ID
    assert thought.parent_message_id is None
    assert message.parent_message_id == thought.message_id
    assert tool_call.parent_message_id == message.message_id
    assert [m.sequence_num for m in state.messages] == [1, 2, 3]
    assert state.tool_call_msg_ids == {"c1": tool_call.message_id}
    assert tool_call.content == [{"type": "tool_call", "tool_call_id": "c1", "name": "get_weather", "args": {"city": "中山"}}]

    # 模型发起的 tool_call：tools 通道 started 到达时按 id 去重（messages 路径已落库）
    fold_tools_frame(state, ctx, {"event": "tool-started", "tool_call_id": "c1", "tool_name": "get_weather"})
    assert len(state.messages) == 3
    # 结果配对父关联到 messages 路径的 TOOL_CALL 行
    fold_tools_frame(state, ctx, {"event": "tool-result", "tool_call_id": "c1", "content": "晴"})
    assert state.messages[3].parent_message_id == tool_call.message_id


def test_fold_messages_frame_reasoning_only_holds_injected_id():
    """无 text 的消息（纯 reasoning/中断）：THOUGHT 行兜底持有注入 id，与
    ag-ui 只发 ReasoningMessage 事件的形态对齐。"""
    state = StorageState()
    fold_messages_frame(state, _ctx(), _MSG_ID, _row_stream(reasoning="只想"))
    assert len(state.messages) == 1
    assert state.messages[0].message_type == AgenticMessageType.THOUGHT
    assert state.messages[0].message_id == _MSG_ID


def test_fold_messages_frame_collects_usage_per_call():
    """usage 键归一（input_tokens→prompt_tokens 族）随 MESSAGE 行落库；chat
    场景每次模型调用一行；无用量上下文（ctx.usage 为 None）不产行。"""
    usage_ctx = UsageContext(user_id=uuid4(), model="test-model")
    state = StorageState()
    stream = _row_stream(text="答", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    fold_messages_frame(state, _ctx(usage=usage_ctx), _MSG_ID, stream)

    assert state.messages[0].token_usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert len(state.usage_records) == 1
    record = state.usage_records[0]
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (10, 5, 15)

    plain_state = StorageState()
    fold_messages_frame(plain_state, _ctx(), _MSG_ID, stream)
    assert plain_state.usage_records == []


def test_fold_tools_frame_self_made_call_pairing_and_custom_row():
    """自造工具步骤（tool-started 补齐 TOOL_CALL，按 id 去重）→ TOOL_RESULT
    配对父关联 + display_content 同行落库 → CUSTOM 行紧跟；孤儿 result 无父；
    finished/error 不落库。"""
    state, ctx = StorageState(), _ctx()
    fold_tools_frame(state, ctx, {"event": "tool-started", "tool_call_id": "s1", "tool_name": "自造", "args": {}})
    assert len(state.messages) == 1
    call_row = state.messages[0]
    assert call_row.message_type == AgenticMessageType.TOOL_CALL and call_row.parent_message_id is None

    fold_tools_frame(state, ctx, {"event": "tool-started", "tool_call_id": "s1", "tool_name": "自造"})
    assert len(state.messages) == 1  # 重复 started 按 tool_call_id 去重

    fold_tools_frame(state, ctx, {
        "event": "tool-result", "tool_call_id": "s1",
        "content": "真实", "display": "展示", "ui": {"a2ui": [{"k": 1}]},
    })
    assert len(state.messages) == 3
    result, custom = state.messages[1], state.messages[2]
    assert result.message_type == AgenticMessageType.TOOL_RESULT and result.role == AgenticMessageRole.TOOL
    assert result.parent_message_id == call_row.message_id
    assert result.content == [{"type": "tool_result", "tool_call_id": "s1", "content": "真实", "display_content": "展示"}]
    assert custom.message_type == AgenticMessageType.CUSTOM and custom.parent_message_id == call_row.message_id
    assert custom.content == [{"type": "custom", "name": "a2ui", "value": [{"k": 1}]}]

    # 孤儿 result（无对应 TOOL_CALL）仍落行但无父
    fold_tools_frame(state, ctx, {"event": "tool-result", "tool_call_id": "ghost", "content": "x"})
    assert state.messages[3].parent_message_id is None

    fold_tools_frame(state, ctx, {"event": "tool-finished", "tool_call_id": "s1"})
    fold_tools_frame(state, ctx, {"event": "tool-error", "tool_call_id": "s1"})
    assert len(state.messages) == 4
