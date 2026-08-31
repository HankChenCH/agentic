"""builtin:rag 图的单元测试：stub 聊天模型 + stub 检索门面，不碰 Weaviate/DeepSeek。

覆盖：意图分路（chitchat 不检索 / 越界 route 归 knowledge）、检索参数
（alpha/top_k/user_id 透传）、改写重试配额（一次后兜底，不无限循环）、
grade 过滤与越界编号忽略、生成资料块组装、_input 历史工具痕迹过滤、
stream() 流式契约（内部调用滤除 + tools 通道事件 + token 流）、
StorageTranslator 对图节点自造工具步骤的落库。
"""

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any, List, Optional
from uuid import UUID, uuid4

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from app.agents.builtin.rag.agent import RagAgent
from app.agents.builtin.rag.nodes import message_text
from app.agents.builtin.rag.prompts import GradeItem, GradeOutput, UnderstandDecision
from app.agents.context import AgentRunContext
from app.components.knowledge.ability.retrieval import RetrievalHit
from app.models.domain.agentic import AgenticMessageType
from app.services.orchestration.translator.storage_translator import StorageTranslator

_TEST_USER_ID = "11111111-1111-1111-1111-111111111111"


# ---------------------------------------------------------------- stubs
class StubChatModel(BaseChatModel):
    """脚本化 stub：结构化输出与自由生成各走一条队列。

    与真实实现同形的关键点：结构化输出内部也发起一次模型调用（经
    ``invoke`` 走回调 → messages 通道出现 node=当前图节点的项，供滤除
    逻辑测试），但不消费自由生成的答案队列（internal_mode 标记）。
    """

    structured_answers: List[Any] = Field(default_factory=list)
    free_answers: List[str] = Field(default_factory=list)
    prompts: List[List[BaseMessage]] = Field(default_factory=list)
    internal_mode: bool = False

    @property
    def _llm_type(self) -> str:
        return "stub"

    def _next_free_text(self) -> str:
        return self.free_answers.pop(0) if self.free_answers else "默认回答"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.prompts.append(list(messages))
        text = "内部结构化调用" if self.internal_mode else self._next_free_text()
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ):
        self.prompts.append(list(messages))
        text = "内部结构化调用" if self.internal_mode else self._next_free_text()
        # 拆两段模拟 token 流（流式投影按 delta 推进）
        for piece in (text[:2], text[2:]):
            if piece:
                yield ChatGenerationChunk(message=AIMessageChunk(content=piece))

    def with_structured_output(self, schema: Any = None, **kwargs: Any) -> Any:
        model = self

        def _run(messages: Any) -> Any:
            model.internal_mode = True
            try:
                model.invoke(messages)
            finally:
                model.internal_mode = False
            return model.structured_answers.pop(0)

        return RunnableLambda(_run)


class StubRetrieval:
    """检索门面替身：按脚本出队（hits, notes），记录调用参数。"""

    def __init__(self, script: List[tuple]):
        self.script = list(script)
        self.calls: List[dict] = []

    def search_for_user(self, user_id, query, kb_ids=None, top_k=4, alpha=1.0):
        self.calls.append({"user_id": user_id, "query": query, "top_k": top_k, "alpha": alpha, "kb_ids": kb_ids})
        return self.script.pop(0)


# ---------------------------------------------------------------- helpers
def _hit(content: str = "知识块内容", doc_name: str = "文档A", position: int = 0) -> RetrievalHit:
    return RetrievalHit(
        content=content, score=0.9, kb_id="kb-1", doc_id="doc-1", doc_name=doc_name,
        position=position, meta={},
    )


def _decision(route: str, standalone: str = "凝练后的问题") -> UnderstandDecision:
    return UnderstandDecision(route=route, standalone_question=standalone)


def _make_agent(structured: List[Any], free: List[str], retrieval_script: List[tuple]):
    model = StubChatModel(structured_answers=structured, free_answers=free)
    retrieval = StubRetrieval(retrieval_script)
    # memory.recall 为永不命中的空快注替身（本文件只测 RAG 图行为本身）
    toolbox = SimpleNamespace(
        knowledge=SimpleNamespace(retrieval=retrieval),
        memory=SimpleNamespace(recall=SimpleNamespace(build_fast_context=lambda **kwargs: "")),
    )
    return RagAgent(model=model, toolbox=toolbox), model, retrieval


def _ctx(messages: List[BaseMessage]) -> AgentRunContext:
    return AgentRunContext(
        messages=list(messages), thread_id="thread-1", run_id="run-1",
        user_id=_TEST_USER_ID, now=datetime(2026, 8, 30, 12, 0, 0),
    )


def _prompts_containing(model: StubChatModel, needle: str) -> List[List[BaseMessage]]:
    return [p for p in model.prompts if any(needle in message_text(m) for m in p)]


# ---------------------------------------------------------------- 路由与检索
def test_chitchat_routes_around_retrieval():
    agent, model, retrieval = _make_agent(
        structured=[_decision("chitchat")],
        free=["你好！很高兴见到你。"],
        retrieval_script=[],
    )
    out = agent.invoke(_ctx([HumanMessage(content="你好")]))
    assert out == "你好！很高兴见到你。"
    assert retrieval.calls == []


def test_knowledge_single_pass_generates_with_citations():
    agent, model, retrieval = _make_agent(
        structured=[
            _decision("knowledge", "什么是RAG"),
            GradeOutput(results=[GradeItem(index=1, relevant=True)]),
        ],
        free=["RAG 是检索增强生成 [1]。"],
        retrieval_script=[([_hit(content="RAG 指检索增强生成。")], [])],
    )
    out = agent.invoke(_ctx([HumanMessage(content="什么是RAG")]))
    assert out == "RAG 是检索增强生成 [1]。"
    assert len(retrieval.calls) == 1
    call = retrieval.calls[0]
    # 混合检索参数与身份透传（可见性圈定在门面内完成）
    assert call["top_k"] == 6
    assert call["alpha"] == 0.5
    assert call["user_id"] == UUID(_TEST_USER_ID)
    assert call["query"] == "什么是RAG"


def test_miss_rewrites_once_then_falls_back():
    agent, model, retrieval = _make_agent(
        structured=[
            _decision("knowledge", "量子计算 阅读顺序"),
            GradeOutput(results=[]),
            GradeOutput(results=[]),
        ],
        free=["量子计算入门资料推荐", "知识库中暂未找到相关内容，建议换一种问法。"],
        retrieval_script=[([], []), ([], [])],
    )
    out = agent.invoke(_ctx([HumanMessage(content="量子计算怎么入门")]))
    # 恰好重试一次：两次检索，第二次用改写问题；最终走兜底话术
    assert len(retrieval.calls) == 2
    assert retrieval.calls[1]["query"] == "量子计算入门资料推荐"
    assert out == "知识库中暂未找到相关内容，建议换一种问法。"


def test_rewrite_retry_can_recover():
    agent, model, retrieval = _make_agent(
        structured=[
            _decision("knowledge", "K8s 网络"),
            GradeOutput(results=[]),
            GradeOutput(results=[GradeItem(index=1, relevant=True)]),
        ],
        free=["kubernetes 网络原理", "答案引用 [1]。"],
        retrieval_script=[([], []), ([_hit(content="命中内容")], [])],
    )
    out = agent.invoke(_ctx([HumanMessage(content="K8s网络")]))
    assert len(retrieval.calls) == 2
    assert out == "答案引用 [1]。"


def test_out_of_range_route_falls_to_knowledge():
    agent, model, retrieval = _make_agent(
        structured=[
            UnderstandDecision(route="weather", standalone_question=""),
            GradeOutput(results=[]),
            GradeOutput(results=[]),
        ],
        free=["改写词", "未找到相关内容。"],
        retrieval_script=[([], []), ([], [])],
    )
    out = agent.invoke(_ctx([HumanMessage(content="北京天气如何")]))
    # 越界 route 归 knowledge：走了检索链；standalone 缺省回退原始问题
    assert len(retrieval.calls) == 2
    assert out == "未找到相关内容。"


def test_grade_filters_and_ignores_out_of_range_indices():
    agent, model, retrieval = _make_agent(
        structured=[
            _decision("knowledge", "问题"),
            GradeOutput(results=[
                GradeItem(index=1, relevant=True),
                GradeItem(index=9, relevant=True),  # 越界：忽略
                GradeItem(index=2, relevant=False),
            ]),
        ],
        free=["最终回答"],
        retrieval_script=[([
            _hit(content="块一内容", doc_name="文档A", position=0),
            _hit(content="块二内容", doc_name="文档B", position=1),
        ], [])],
    )
    agent.invoke(_ctx([HumanMessage(content="问题")]))
    generate_prompts = _prompts_containing(model, "【资料】")
    assert len(generate_prompts) == 1
    human = [m for m in generate_prompts[0] if isinstance(m, HumanMessage)][0]
    assert "[1] 文档A" in human.content and "块一内容" in human.content
    assert "[2]" not in human.content and "块二内容" not in human.content


# ---------------------------------------------------------------- 输入与流式
def test_input_filters_tool_traces_and_initializes_state():
    agent, _, _ = _make_agent(structured=[], free=[], retrieval_script=[])
    history = [
        HumanMessage(content="第一个问题"),
        AIMessage(content="", tool_calls=[{"name": "knowledge_search", "args": {"query": "x"}, "id": "c1"}]),
        ToolMessage(content="工具结果", tool_call_id="c1"),
        AIMessage(content="第一个回答"),
        HumanMessage(content="第二个问题"),
    ]
    state = agent._input(_ctx(history))
    texts = [message_text(m) for m in state["messages"]]
    assert "工具结果" not in " ".join(texts)
    assert all(not getattr(m, "tool_calls", None) for m in state["messages"])
    assert "第一个回答" in texts and any("第二个问题" in t for t in texts)
    assert state["messages"][-1].content.startswith("[当前时间：")
    # 状态通道整体初始化（图节点/路由函数可安全读取）
    assert state["route"] == "" and state["rewrite_count"] == 0
    assert state["documents"] == [] and state["relevant"] == [] and state["generation"] == ""


def test_stream_suppresses_internal_calls_and_emits_tool_events():
    agent, model, retrieval = _make_agent(
        structured=[
            _decision("knowledge", "什么是RAG"),
            GradeOutput(results=[GradeItem(index=1, relevant=True)]),
        ],
        free=["最终流式回答。"],
        retrieval_script=[([_hit(content="RAG 检索增强")], ["知识库「X」未启用，已跳过"])],
    )
    run = agent.stream(_ctx([HumanMessage(content="什么是RAG")]))
    items = list(run.interleave("messages", "tools"))

    tool_items = [item for name, item in items if name == "tools"]
    assert [t["event"] for t in tool_items] == ["tool-started", "tool-result", "tool-finished"]
    assert tool_items[0]["tool_name"] == "knowledge_search"
    payload = json.loads(tool_items[1]["content"])
    assert payload["sources"][0]["content"] == "RAG 检索增强"
    assert payload["notes"] == ["知识库「X」未启用，已跳过"]

    # understand/grade 的内部 LLM 调用（含结构化输出的内部调用）被滤除
    msg_nodes = [getattr(item, "node", None) for name, item in items if name == "messages"]
    assert "understand" not in msg_nodes and "grade" not in msg_nodes
    texts = ["".join(item.text) for name, item in items if name == "messages"]
    assert "".join(texts) == "最终流式回答。"


# ---------------------------------------------------------------- ag-ui 契约
def test_stream_translates_to_agui_frames():
    """全链路契约：agent 流经 AgUiTranslator 产出合法 ag-ui SSE 帧。"""
    from app.services.orchestration.translator.translator import AgUiTranslator

    agent, _, _ = _make_agent(
        structured=[
            _decision("knowledge", "什么是RAG"),
            GradeOutput(results=[GradeItem(index=1, relevant=True)]),
        ],
        free=["最终流式回答。"],
        retrieval_script=[([_hit(content="RAG 检索增强")], [])],
    )
    thread_id, run_id = uuid4(), "run-1"
    translator = AgUiTranslator(thread_id=thread_id, run_id=run_id)
    frames = [translator.start()]
    run = agent.stream(_ctx([HumanMessage(content="什么是RAG")]))
    for name, item in run.interleave("messages", "tools"):
        frames.extend(translator.translate(name, item, uuid4()))
    frames.append(translator.finish())

    kinds = [json.loads(f.removeprefix("data: ").strip())["type"] for f in frames]
    # 生命周期完整；检索步骤出 ToolCall 三事件；用户可见文本只有一条消息流
    assert kinds[0] == "RUN_STARTED" and kinds[-1] == "RUN_FINISHED"
    assert "TOOL_CALL_START" in kinds and "TOOL_CALL_RESULT" in kinds and "TOOL_CALL_END" in kinds
    assert kinds.count("TEXT_MESSAGE_START") == 1 and kinds.count("TEXT_MESSAGE_END") == 1
    # 内部调用未泄漏成 Reasoning/Text 消息
    assert kinds.count("REASONING_MESSAGE_START") == 0
    text_delta = "".join(
        json.loads(f.removeprefix("data: ").strip())["delta"]
        for f, k in zip(frames, kinds) if k == "TEXT_MESSAGE_CONTENT"
    )
    assert text_delta == "最终流式回答。"


# ---------------------------------------------------------------- 落库
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
