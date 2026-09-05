"""ConversationTitleGenerator：裸模型调用与输出归一化的纯单测（不建真实模型/容器）。"""

from uuid import uuid4

from langchain.messages import HumanMessage, SystemMessage

from conftest import TEST_USER_ID, StubUsageService
from app.models.domain.agentic import (
    AgenticConversationMessage,
    AgenticMessageRole,
    AgenticMessageType,
)
from app.domain.conversation.title_generator import (
    ConversationTitleGenerator,
    generate_title,
)


class FakeModel:
    """model.invoke 替身：canned 回复，捕获收到的消息列表。"""

    def __init__(self, reply, usage_metadata=None):
        self.model_name = "fake-title-model"
        self.reply = reply
        self.usage_metadata = usage_metadata
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return _Reply(self.reply, self.usage_metadata)


class _Reply:
    def __init__(self, content, usage_metadata=None):
        self.content = content
        self.usage_metadata = usage_metadata


class FakeModelGateway:
    def __init__(self, model):
        self.model = model
        self.created = 0

    def create(self, name=None):
        self.created += 1
        return self.model


def one_message():
    return [make_message(sequence_num=1, message_type=AgenticMessageType.MESSAGE, text="正文")]


def test_prompt_and_transcript_shape():
    model = FakeModel("标题")
    query = "你好"
    messages = [
        make_message(sequence_num=1, message_type=AgenticMessageType.MESSAGE, text="回复一"),
        # THOUGHT（reasoning）行不进 transcript
        make_message(sequence_num=2, message_type=AgenticMessageType.THOUGHT, text="推理过程"),
        make_message(sequence_num=3, message_type=AgenticMessageType.MESSAGE, text="回复二"),
    ]

    title, usage = generate_title(model, query, messages)

    assert title == "标题"
    system, human = model.calls[0]
    assert isinstance(system, SystemMessage)
    assert "会话标题生成器" in system.content
    assert isinstance(human, HumanMessage)
    # 仅 MESSAGE 类型的文本 part 参与，按消息顺序拼接
    assert human.content == "用户：你好\n助手：回复一\n回复二"
    # 替身回复无 usage_metadata：用量为空 dict
    assert usage == {}


def make_message(sequence_num, message_type, text):
    return AgenticConversationMessage(
        thread_id=None,
        turn_id=None,
        message_id=None,
        sequence_num=sequence_num,
        role=AgenticMessageRole.ASSISTANT,
        message_type=message_type,
        content=[{"type": "text", "text": text}],
        token_usage={},
        latency_ms=0,
    )


def test_normalize_quoted_multiline():
    assert generate_title(FakeModel('「智能体设计讨论」\n多余的第二行'), "", [])[0] == "智能体设计讨论"
    assert generate_title(FakeModel('"带引号"\n其余'), "", [])[0] == "带引号"


def test_normalize_caps_length():
    long = "标" * 60
    assert generate_title(FakeModel(long), "", [])[0] == "标" * 50


def test_normalize_blank_output_is_empty():
    # 纯空白输出返回 ""（而非 IndexError），调用方按生成失败留空重试
    assert generate_title(FakeModel("  \n \n"), "", [])[0] == ""
    assert generate_title(FakeModel("\n\n 有效 \n"), "", [])[0] == "有效"


def test_generate_title_returns_raw_usage_metadata():
    model = FakeModel("标题", usage_metadata={"input_tokens": 50, "output_tokens": 8, "total_tokens": 58})

    title, usage = generate_title(model, "你好", one_message())

    assert title == "标题"
    # 原始键族原样带回（键族归一与落库归 UsageService 的 sink）
    assert usage == {"input_tokens": 50, "output_tokens": 8, "total_tokens": 58}


def test_generator_facade_routes_through_factory_default():
    factory = FakeModelGateway(FakeModel(" 工厂标题 "))
    usage = StubUsageService()
    generator = ConversationTitleGenerator(chat_model_gateway=factory, usage=usage)

    title = generator.generate("你好", one_message(), user_id=TEST_USER_ID, thread_id=uuid4())

    assert factory.created == 1
    assert factory.model is not None
    assert title == "工厂标题"
    # 无用量不产生流水行
    assert usage.records == []


def test_generator_facade_records_title_usage():
    factory = FakeModelGateway(FakeModel(
        "标题", usage_metadata={"input_tokens": 50, "output_tokens": 8, "total_tokens": 58},
    ))
    usage = StubUsageService()
    generator = ConversationTitleGenerator(chat_model_gateway=factory, usage=usage)
    thread_id = uuid4()

    generator.generate("你好", one_message(), user_id=TEST_USER_ID, thread_id=thread_id)

    assert len(usage.records) == 1
    row = usage.records[0]
    assert row.scene == "title"
    assert row.user_id == TEST_USER_ID
    assert row.thread_id == thread_id
    assert row.model == "fake-title-model"
    # sink 内完成键族归一（input_tokens → prompt_tokens）
    assert row.usage == {"prompt_tokens": 50, "completion_tokens": 8, "total_tokens": 58}
