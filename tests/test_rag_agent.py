"""builtin:rag（ReAct 工具循环形态）的单元测试：真实组件 + stub 门面服务 +
脚本化模型，不碰 Weaviate/DeepSeek。

覆盖：rag 与 demo 的组件级工具面（rag=知识四件+记忆三件套、demo=记忆
三件套+get_weather）、rag 端到端一轮（模型发起真实 knowledge_search 调用
→ stub 检索执行 → 最终回答）、system prompt 渲染（{tools} 索引 + <memory>
空节移除）、流式契约（ToolsTransformer 标准 tools 通道事件 + AgUiTranslator
合法 ag-ui 帧）；尾部两个 StorageTranslator 用例是通用落库契约（tool-started
补齐/去重），随本文件保留。
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, List
from uuid import UUID, uuid4

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from app.agents.builtin.demo.agent import DemoAgent
from app.agents.builtin.rag.agent import RagAgent
from app.agents.context import AgentRunContext
from app.agents.toolbox import AgentToolbox
from app.components.demo import DemoComponent
from app.components.knowledge import KnowledgeComponent
from app.components.knowledge.ability.retrieval import RetrievalHit
from app.components.memory import MemoryComponent
from app.models.domain.agentic import AgenticMessageType
from app.services.orchestration.translator.storage_translator import StorageTranslator

_TEST_USER_ID = "11111111-1111-1111-1111-111111111111"
_THREAD_ID = str(uuid4())
_KB_ID = uuid4()
_NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

RAG_TOOL_NAMES = {
    "knowledge_list", "knowledge_search", "knowledge_context", "knowledge_document_list",
    "timeline", "expand", "state_at",
}
DEMO_TOOL_NAMES = {"timeline", "expand", "state_at", "get_weather"}


# ---------------------------------------------------------------- stubs
class ScriptedChatModel(BaseChatModel):
    """按次序回放脚本化 AIMessage（tool_calls 或文本）；记录收到的 prompts。"""

    answers: List[Any] = Field(default_factory=list)
    prompts: List[List[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _next(self) -> AIMessage:
        return self.answers.pop(0) if self.answers else AIMessage(content="默认回答")

    def _generate(
        self,
        messages: List[BaseMessage],
        stop=None,
        run_manager: CallbackManagerForLLMRun = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.prompts.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs: Any):
        self.prompts.append(list(messages))
        message = self._next()
        if message.tool_calls:
            call = message.tool_calls[0]
            chunk = AIMessageChunk(content="", tool_call_chunks=[{
                "name": call["name"], "args": json.dumps(call["args"]),
                "id": call["id"], "index": 0, "type": "tool_call_chunk",
            }])
            yield ChatGenerationChunk(message=chunk)
            return
        text = message.content
        mid = max(1, len(text) // 2)
        for piece in (text[:mid], text[mid:]):  # 拆两段模拟 token 流
            if piece:
                yield ChatGenerationChunk(message=AIMessageChunk(content=piece))


class StubRetrieval:
    """检索门面替身：按脚本返回（hits, notes），记录调用参数。"""

    def __init__(self, result=([], [])):
        self.result = result
        self.captured = None

    def list_visible_knowledge(self, user_id):
        return []

    def search_for_user(self, user_id, query, kb_ids=None, top_k=4):
        self.captured = {"user_id": user_id, "query": query, "kb_ids": kb_ids, "top_k": top_k}
        return self.result


class StubRecall:
    """记忆门面替身：快注固定块（空块测空节移除），深度三件套不参与断言。"""

    def __init__(self, block=""):
        self.block = block
        self.queries = []

    def build_fast_context(self, query, user_id, thread_id):
        self.queries.append((query, user_id, thread_id))
        return self.block

    def timeline(self, **kwargs):
        return ""

    def expand(self, **kwargs):
        return ""

    def state_at(self, **kwargs):
        return ""


# ---------------------------------------------------------------- helpers
def _hit(content: str = "RAG 指检索增强生成。") -> RetrievalHit:
    return RetrievalHit(
        content=content, score=0.9, kb_id=str(_KB_ID), doc_id="doc-1",
        doc_name="文档A", position=0, meta={},
    )


def _toolbox(retrieval=None, recall=None) -> AgentToolbox:
    return AgentToolbox(
        memory=MemoryComponent(recall=recall or StubRecall()),
        knowledge=KnowledgeComponent(retrieval=retrieval or StubRetrieval(), navigation=SimpleNamespace()),
        demo=DemoComponent(weather=SimpleNamespace()),
    )


def _ctx(query: str, history=()) -> AgentRunContext:
    return AgentRunContext(
        messages=[*history, HumanMessage(content=query)],
        thread_id=_THREAD_ID, run_id="run-1", user_id=_TEST_USER_ID, now=_NOW,
    )


# ---------------------------------------------------------------- 工具面
def test_rag_tools_are_knowledge_and_memory_only():
    agent = RagAgent(model=ScriptedChatModel(), toolbox=_toolbox())
    names = {tool.name for tool in agent.build_tools()}
    assert names == RAG_TOOL_NAMES
    assert "get_weather" not in names


def test_demo_tools_are_memory_and_demo_only():
    agent = DemoAgent(model=ScriptedChatModel(), toolbox=_toolbox())
    names = {tool.name for tool in agent.build_tools()}
    assert names == DEMO_TOOL_NAMES
    assert not names & {"knowledge_list", "knowledge_search", "knowledge_context", "knowledge_document_list"}


# ---------------------------------------------------------------- 端到端一轮
def test_knowledge_search_tool_call_executes_end_to_end():
    """模型发起真实 knowledge_search 调用 → stub 检索执行（身份/参数透传）
    → 工具结果进第二轮 → 最终回答。"""
    retrieval = StubRetrieval(result=([_hit()], []))
    model = ScriptedChatModel(answers=[
        AIMessage(content="", tool_calls=[{
            "name": "knowledge_search",
            "args": {"query": "什么是RAG", "kb_ids": [str(_KB_ID)]},
            "id": "call-1",
        }]),
        AIMessage(content="RAG 是检索增强生成 [1]。"),
    ])
    agent = RagAgent(model=model, toolbox=_toolbox(retrieval=retrieval))

    assert agent.invoke(_ctx("什么是RAG")) == "RAG 是检索增强生成 [1]。"
    assert retrieval.captured == {
        "user_id": UUID(_TEST_USER_ID), "query": "什么是RAG",
        "kb_ids": [_KB_ID], "top_k": 4,
    }
    # 第二轮模型调用收到了工具结果（ToolMessage 进上下文）
    tool_messages = [m for m in model.prompts[1] if m.type == "tool"]
    assert len(tool_messages) == 1 and "RAG 指检索增强生成。" in tool_messages[0].content


def test_system_prompt_renders_tools_index_and_memory_block():
    """{tools} 索引按实际装配工具生成；<memory> 有块渲染、空块整节移除。"""
    block = "## 快速记忆上下文\n1. 用户的偏好是Python"
    agent = RagAgent(
        model=ScriptedChatModel(answers=[AIMessage(content="好")]),
        toolbox=_toolbox(recall=StubRecall(block=block)),
    )
    agent.invoke(_ctx("你好"))

    system = agent.model.prompts[0][0]
    assert system.type == "system"
    assert "knowledge_search" in system.content and "timeline" in system.content
    assert "get_weather" not in system.content  # 工具索引与实际装配面一致
    assert block in system.content  # 快注块渲染进 {memory} 槽
    assert agent.recall.queries  # 每次模型调用都经 memory 片段触发快注

    plain = RagAgent(
        model=ScriptedChatModel(answers=[AIMessage(content="好")]),
        toolbox=_toolbox(recall=StubRecall(block="")),
    )
    plain.invoke(_ctx("你好"))
    assert "<memory>" not in plain.model.prompts[0][0].content  # 空节移除


# ---------------------------------------------------------------- 流式契约
def test_stream_emits_tool_events_and_agui_frames():
    """全链路契约：真实工具调用经 ToolsTransformer 出标准 tools 通道事件，
    AgUiTranslator 产出合法 ag-ui 帧（TOOL_CALL 三事件 + 单条文本消息流）。"""
    from app.services.orchestration.translator.translator import AgUiTranslator

    retrieval = StubRetrieval(result=([_hit()], ["知识库「X」未启用，已跳过"]))
    agent = RagAgent(model=ScriptedChatModel(answers=[
        AIMessage(content="", tool_calls=[{
            "name": "knowledge_search",
            "args": {"query": "什么是RAG", "kb_ids": [str(_KB_ID)]},
            "id": "call-1",
        }]),
        AIMessage(content="RAG 是检索增强生成 [1]。"),
    ]), toolbox=_toolbox(retrieval=retrieval))

    translator = AgUiTranslator(thread_id=uuid4(), run_id="run-1")
    frames = [translator.start()]
    tool_items = []
    run = agent.stream(_ctx("什么是RAG"))
    for name, item in run.interleave("messages", "tools"):
        if name == "tools":
            tool_items.append(item)
        frames.extend(translator.translate(name, item, uuid4()))
    frames.append(translator.finish())

    kinds = [json.loads(f.removeprefix("data: ").strip())["type"] for f in frames]
    # 生命周期完整；真实检索调用出 ToolCall 三事件；用户可见文本只有一条消息流
    assert kinds[0] == "RUN_STARTED" and kinds[-1] == "RUN_FINISHED"
    assert "TOOL_CALL_START" in kinds and "TOOL_CALL_RESULT" in kinds and "TOOL_CALL_END" in kinds
    assert kinds.count("TEXT_MESSAGE_START") == 1 and kinds.count("TEXT_MESSAGE_END") == 1
    text_delta = "".join(
        json.loads(f.removeprefix("data: ").strip())["delta"]
        for f, k in zip(frames, kinds) if k == "TEXT_MESSAGE_CONTENT"
    )
    assert text_delta == "RAG 是检索增强生成 [1]。"

    # tools 通道事件契约：tool-started/result/finished，结果串与工具同一 JSON
    assert [t["event"] for t in tool_items] == ["tool-started", "tool-result", "tool-finished"]
    assert tool_items[0]["tool_name"] == "knowledge_search"
    payload = json.loads(tool_items[1]["content"])
    assert payload["sources"][0]["content"] == "RAG 指检索增强生成。"
    assert payload["notes"] == ["知识库「X」未启用，已跳过"]


def test_storage_persists_node_tool_call_from_tool_started():
    st = StorageTranslator(thread_id=uuid4(), turn_id=uuid4())
    st.translate("tools", {
        "event": "tool-started", "tool_call_id": "c1",
        "tool_name": "knowledge_search", "args": {"query": "q", "top_k": 6},
    }, uuid4())
    st.translate("tools", {"event": "tool-result", "tool_call_id": "c1", "content": "结果"}, uuid4())
    rows = st.messages
    assert [r.message_type for r in rows] == [AgenticMessageType.TOOL_CALL, AgenticMessageType.TOOL_RESULT]
    assert rows[0].content[0]["name"] == "knowledge_search"
    assert rows[0].content[0]["args"] == {"query": "q", "top_k": 6}
    assert rows[1].parent_message_id == rows[0].message_id


def test_storage_dedupes_model_issued_tool_calls():
    st = StorageTranslator(thread_id=uuid4(), turn_id=uuid4())
    stream = SimpleNamespace(
        reasoning="", text="",
        tool_calls=SimpleNamespace(get=lambda: [{"id": "c1", "name": "some_tool", "args": {}}]),
        output_message=None,
    )
    st.translate("messages", stream, uuid4())
    st.translate("tools", {"event": "tool-started", "tool_call_id": "c1", "tool_name": "some_tool"}, uuid4())
    # 模型发起的调用已由 messages 路径落库，tool-started 去重不重复落行
    assert [r.message_type for r in st.messages] == [AgenticMessageType.TOOL_CALL]
