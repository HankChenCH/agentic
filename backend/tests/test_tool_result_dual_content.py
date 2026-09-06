"""tools 通道双内容契约（审计 / 展示）的消费侧单元测试。

契约：tool-result 事件 ``content`` 恒为真实结果（LLM 所见），可选 ``display``
为前端展示版。消费规则对 agent 无感——
- AgUiTranslator：流式 SSE 取 ``display ?? content``（真实版不出前端）；
- StorageTranslator：TOOL_RESULT 行同行落 ``content`` + ``display_content``。

生产者侧（SupportToolsTransformer 的双键 push）见 test_support_agent.py，
历史出口替换见 test_conversation_service.py。
"""

import json
from uuid import uuid4

from app.application.translator.storage_translator import StorageTranslator
from app.application.translator.translator import AgUiTranslator


def _events(frames):
    return [json.loads(f.removeprefix("data: ").strip()) for f in frames]


def _tool_payload(display=None):
    payload = {"event": "tool-result", "tool_call_id": "call-1", "content": "真实结果"}
    if display is not None:
        payload["display"] = display
    return payload


# ---------------------------------------------------------------- AgUiTranslator
def test_agui_tool_result_prefers_display():
    translator = AgUiTranslator(thread_id=uuid4(), run_id="run-1")
    events = _events(translator.translate("tools", _tool_payload(display="展示摘要")))

    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert result["content"] == "展示摘要"
    assert "真实结果" not in result["content"]


def test_agui_tool_result_falls_back_to_content():
    """无 display（其他 agent / 未脱敏工具）回退 content，行为与既往一致。"""
    translator = AgUiTranslator(thread_id=uuid4(), run_id="run-1")
    events = _events(translator.translate("tools", _tool_payload()))

    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert result["content"] == "真实结果"


# ---------------------------------------------------------------- StorageTranslator
def _storage_with_result(display=None):
    translator = StorageTranslator(thread_id=uuid4(), turn_id=uuid4())
    translator.translate("tools", {"event": "tool-started", "tool_call_id": "call-1", "tool_name": "knowledge_search"}, uuid4())
    translator.translate("tools", _tool_payload(display), uuid4())
    return translator


def test_storage_tool_result_persists_dual_content():
    rows = _storage_with_result(display="展示摘要").messages
    result_rows = [m for m in rows if m.message_type.value == "tool_result"]
    assert len(result_rows) == 1
    part = result_rows[0].content[0]
    # 同行双键：content 真实（审计），display_content 展示版
    assert part["content"] == "真实结果"
    assert part["display_content"] == "展示摘要"
    assert part["tool_call_id"] == "call-1"


def test_storage_tool_result_without_display_keeps_legacy_shape():
    rows = _storage_with_result().messages
    result_rows = [m for m in rows if m.message_type.value == "tool_result"]
    part = result_rows[0].content[0]
    assert part["content"] == "真实结果"
    assert "display_content" not in part
