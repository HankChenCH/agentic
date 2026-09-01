"""agent 层输入装配契约：ReAct 路径 ``_input`` 不再携带 system 消息（人设经
``create_agent(system_prompt=...)`` 烘图、快注经动态 prompt 中间件渲染进模板
``{memory}`` 槽，中间件契约见 test_agents_prompt_middleware.py）；RAG 因图
节点按文本渲染历史（understand 会丢弃 SystemMessage），仍在其 ``_input`` 把
快注块折进末条用户消息。图不参与本组测试——桩子类的 build_graph 返回 None，
仅验证输入装配。
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agents.base import BaseAgent
from app.agents.builtin.rag.agent import RagAgent
from app.agents.context import AgentRunContext

from fakes_memory import CannedLLM

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
    return SimpleNamespace(
        memory=SimpleNamespace(recall=recall),
        knowledge=SimpleNamespace(retrieval=SimpleNamespace()),
    )


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


def test_rag_input_folds_block_into_last_user_message():
    """RAG 不在输入加 system 消息（节点自建），块折进末条用户消息且形状与
    原编排层注入逐字一致（块 + 分隔 + 重标注 query，外层时间前缀）。"""
    recall = FakeRecall(block=BLOCK)
    agent = RagAgent(model=CannedLLM(), toolbox=_toolbox(recall))

    messages = agent._input(_ctx("查一下资料", history=[AIMessage(content="先前回答")]))["messages"]

    assert all(not isinstance(m, SystemMessage) for m in messages)
    assert messages[-1].content == f"[当前时间：{NOW}]\n\n{BLOCK}\n\n---\n\n用户提问：查一下资料"
    # 快注以原始 query 触发（寒暄短路判别），身份转为 UUID 透传
    assert recall.queries == [("查一下资料", UUID(TEST_USER), UUID(THREAD))]


def test_rag_input_without_block_keeps_plain_query():
    """RAG 无块时末条保持「时间前缀 + 原始 query」，无重标注分隔。"""
    agent = RagAgent(model=CannedLLM(), toolbox=_toolbox(FakeRecall(block="")))

    messages = agent._input(_ctx("查一下资料"))["messages"]

    assert messages[-1].content == f"[当前时间：{NOW}]\n\n查一下资料"
