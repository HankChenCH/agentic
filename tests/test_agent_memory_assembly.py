"""agent 层输入装配契约：ReAct 路径 ``_input`` 不携带 system 消息（人设经
``create_agent(system_prompt=...)`` 烘图、快注经动态 prompt 中间件渲染进模板
``{memory}`` 槽，中间件契约见 test_agents_prompt_middleware.py）。图不参与
本组测试——桩子类的 build_graph 返回 None，仅验证输入装配。
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.agents.context import AgentRunContext

TEST_USER = str(uuid4())
THREAD = str(uuid4())
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)

BLOCK = "## 快速记忆上下文（共1条 · 取自 2026-08-31）\n1. 用户的偏好是Python"


class FakeRecall:
    """MemoryRecallService 替身：固定块（可抛错），记录调用。"""

    def __init__(self, block="", error=None):
        self.block = block
        self.error = error
        self.queries = []

    def build_fast_context(self, query, user_id, thread_id):
        self.queries.append((query, user_id, thread_id))
        if self.error is not None:
            raise self.error
        return self.block


def _toolbox(recall):
    return SimpleNamespace(memory=SimpleNamespace(recall=recall))


class _StubAgent(BaseAgent):
    """不编译真实图的桩智能体：本组测试只验证输入装配。"""

    agentic_id = "test:stub"

    def build_system_prompt(self):
        return "人设"

    def build_graph(self):
        return None


def _ctx(query, history=()):
    return AgentRunContext(
        messages=[*history, HumanMessage(content=query)],
        thread_id=THREAD,
        run_id="run-1",
        user_id=TEST_USER,
        now=NOW,
    )


def test_input_replays_history_without_system_message():
    """ReAct 路径 _input 不携带 system 消息：历史原样回放、末条仅注入时间
    前缀（人设烘在图上、动态槽归中间件，任一时刻模型只收一条 system）。"""
    recall = FakeRecall(block=BLOCK)
    agent = _StubAgent(model=None, toolbox=_toolbox(recall))
    history = AIMessage(content="先前回答")

    messages = agent._input(_ctx("我上周聊到什么了？", history=[history]))["messages"]

    assert all(not isinstance(m, SystemMessage) for m in messages)
    assert messages[0] is history
    assert messages[-1].content == f"[当前时间：{NOW}]\n\n我上周聊到什么了？"
    # 快注渲染发生在模型调用时（memory 片段），_input 不触发召回
    assert recall.queries == []


def test_input_ignores_recall_failure():
    """快注失败不影响输入装配——降级口径收口在中间件的片段渲染处。"""
    agent = _StubAgent(model=None, toolbox=_toolbox(FakeRecall(error=RuntimeError("db down"))))

    messages = agent._input(_ctx("hi"))["messages"]

    assert messages and not any(isinstance(m, SystemMessage) for m in messages)


# ---- 多模态（图片输入）输入装配口径 ----


def test_supports_vision_follows_model_declaration():
    """supports_vision 读模型实例的 multimodal 声明；无声明的裸模型视为不支持。"""
    recall = FakeRecall(block="")
    vision_model = SimpleNamespace(multimodal=("text", "vision"))
    plain_model = SimpleNamespace(multimodal=())
    bare_model = object()

    assert _StubAgent(model=vision_model, toolbox=_toolbox(recall)).supports_vision is True
    assert _StubAgent(model=plain_model, toolbox=_toolbox(recall)).supports_vision is False
    assert _StubAgent(model=bare_model, toolbox=_toolbox(recall)).supports_vision is False


def test_input_time_prefix_supports_block_content():
    """末条内容为块列表（多模态）时，时间前缀作为首个 text 块插入，图片块原样保留。"""
    agent = _StubAgent(model=None, toolbox=_toolbox(FakeRecall(block="")))
    image_block = {"type": "image", "base64": "aGk=", "mime_type": "image/png"}
    messages = [HumanMessage(content=[{"type": "text", "text": "这是什么？"}, image_block])]

    out = agent._input(AgentRunContext(
        messages=messages, thread_id=THREAD, run_id="run-1", user_id=TEST_USER, now=NOW,
    ))["messages"]

    assert out[-1].content == [
        {"type": "text", "text": f"[当前时间：{NOW}]\n\n"},
        {"type": "text", "text": "这是什么？"},
        image_block,
    ]


def test_fast_memory_block_extracts_text_from_block_content():
    """快注短路判别的 query 取块列表的纯文本（而非 repr），图片不进召回。"""
    recall = FakeRecall(block=BLOCK)
    agent = _StubAgent(model=None, toolbox=_toolbox(recall))
    image_block = {"type": "image", "base64": "aGk=", "mime_type": "image/png"}
    ctx = AgentRunContext(
        messages=[HumanMessage(content=[{"type": "text", "text": "这是什么？"}, image_block])],
        thread_id=THREAD, run_id="run-1", user_id=TEST_USER, now=NOW,
    )

    block = agent._fast_memory_block(ctx)

    assert block == BLOCK
    assert recall.queries[0][0] == "这是什么？"
