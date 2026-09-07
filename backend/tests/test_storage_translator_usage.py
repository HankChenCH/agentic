"""StorageTranslator 的用量面：键名归一 + chat 场景流水收集（ReAct 多步多行）。"""

from types import SimpleNamespace
from uuid import uuid4

from conftest import TEST_USER_ID
from app.application.translator.storage_translator import StorageTranslator, UsageContext


class _Deltas:
    """假消息投影（test_agentic_service.Deltas 同款最小面）。"""

    def __init__(self, *chunks):
        self._chunks = list(chunks)

    def __iter__(self):
        return iter(self._chunks)

    def __str__(self):
        return "".join(self._chunks)

    def __bool__(self):
        return bool(self._chunks)


def _stream(text="回复", usage_metadata=None):
    """messages 投影 item 的最小替身（FakeChatModelStream 同款公开面）。"""
    return SimpleNamespace(
        reasoning=_Deltas(),
        text=_Deltas(text),
        tool_calls=SimpleNamespace(get=lambda: []),
        output_message=SimpleNamespace(usage_metadata=usage_metadata) if usage_metadata is not None else None,
    )


def _context():
    return UsageContext(user_id=TEST_USER_ID, model="fake-chat-model", agentic_id="builtin:demo")


def test_message_token_usage_keys_normalized():
    """写入侧归一：usage_metadata 的 input/output_tokens → prompt/completion 族。"""
    translator = StorageTranslator(thread_id=uuid4(), turn_id=uuid4(), usage=_context())
    translator.translate(uuid4(), "messages", _stream(
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    ))

    rows = [m for m in translator.messages if m.message_type.value == "message"]
    assert rows[0].token_usage["prompt_tokens"] == 10
    assert rows[0].token_usage["completion_tokens"] == 5
    assert rows[0].token_usage["total_tokens"] == 15
    assert "input_tokens" not in rows[0].token_usage


def test_chat_usage_rows_collected_per_llm_call():
    """每次模型调用一行 chat 流水，归属/模型/会话/轮次来自注入的上下文。"""
    thread_id, turn_id = uuid4(), uuid4()
    translator = StorageTranslator(thread_id=thread_id, turn_id=turn_id, usage=_context())

    translator.translate(uuid4(), "messages", _stream(usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}))
    translator.translate(uuid4(), "messages", _stream("无用量"))   # 无 usage：不产生零值行
    translator.translate(uuid4(), "messages", _stream(usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}))

    rows = translator.usage_records
    assert len(rows) == 2
    first = rows[0]
    assert first.user_id == TEST_USER_ID
    assert first.scene == "chat"
    assert first.model == "fake-chat-model"
    assert first.agentic_id == "builtin:demo"
    assert first.thread_id == thread_id
    assert first.turn_id == turn_id
    assert (first.input_tokens, first.output_tokens, first.total_tokens) == (10, 5, 15)
    assert rows[1].total_tokens == 2


def test_no_usage_context_no_rows():
    """未注入用量上下文（旧构造形态）：不收集流水，消息 token_usage 照常落。"""
    translator = StorageTranslator(thread_id=uuid4(), turn_id=uuid4())
    translator.translate(uuid4(), "messages", _stream(usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}))

    assert translator.usage_records == []
    rows = [m for m in translator.messages if m.message_type.value == "message"]
    assert rows[0].token_usage["prompt_tokens"] == 10
