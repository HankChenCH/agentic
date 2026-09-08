"""builtin:support（智能客服）的单元测试：复用 test_rag_agent 的 stub 体系，
不碰 Weaviate/DeepSeek。

覆盖：客服面工具装配（知识检索三件 + 记忆三件套，无浏览工具/演示工具）、
system prompt 客服人设（禁出处禁角标）、脱敏流投影（SupportToolsTransformer
把 KnowledgeSearchResult JSON 拆成 content 真实 + display 状态文案——前端
只见调用成败，LLM 侧 ToolMessage 不受影响）、未命中/非溯源结果的透传、
以及客服面端到端 ag-ui 帧契约。
"""

import json
from uuid import UUID, uuid4

from langchain_core.messages import AIMessage

from app.agents.builtin.rag.agent import RagAgent  # noqa: F401  (注册副作用对齐)
from app.agents.builtin.support.agent import SupportAgent
from tests.test_rag_agent import (
    ScriptedChatModel,
    StubDirectory,
    StubRetrieval,
    _TEST_USER_ID,
    _ctx,
    _hit,
    _toolbox,
    _KB_ID,
)

SUPPORT_TOOL_NAMES = {
    "knowledge_search", "knowledge_context",
    "timeline", "expand", "state_at",
    "a2ui_compose",
}

_SEARCH_RESULT_JSON = json.dumps({
    "sources": [{
        "index": 1, "kb_id": str(_KB_ID), "doc_id": "doc-1", "doc_name": "文档A",
        "position": 0, "score": 0.9, "content": "退货政策为七天无理由。",
        "page_start": 3, "page_end": 3, "heading_path": ["售后"], "bboxes": [[3, 0.1, 0.2, 0.3, 0.4]],
    }],
    "notes": [],
}, ensure_ascii=False)


# ---------------------------------------------------------------- 工具面
def test_support_tools_are_retrieval_subset_and_memory():
    agent = SupportAgent(model=ScriptedChatModel(), toolbox=_toolbox())
    names = {tool.name for tool in agent.build_tools()}
    assert names == SUPPORT_TOOL_NAMES
    # 不装配库内浏览工具（客服用不上）与演示工具；清单类工具
    # （knowledge_list/human_agent_list）已被 prompt 片段取代
    assert "knowledge_document_list" not in names and "get_weather" not in names
    assert "knowledge_list" not in names and "human_agent_list" not in names


def test_support_agent_registered_and_exposed():
    from app.agents.base import AGENT_REGISTRY

    assert AGENT_REGISTRY["builtin:support"] is SupportAgent


# ---------------------------------------------------------------- 人设层
def test_support_prompt_forbids_provenance_and_citations():
    """客服人设：检索结果不外露出处、不打角标；工具索引与实际装配面一致。"""
    agent = SupportAgent(model=ScriptedChatModel(answers=[AIMessage(content="好")]), toolbox=_toolbox())
    agent.invoke(_ctx("你好"))
    system = agent.model.prompts[0][0]
    assert system.type == "system"
    assert "knowledge_search" in system.content
    assert "不得" in system.content and "引用角标" in system.content


def test_support_prompt_guides_transfer_flow():
    """转人工引导：坐席来自 prompt 清单、a2ui_compose 生成卡片、诚实边界。"""
    from app.agents.builtin.support.prompts import SYSTEM_PROMPT

    assert "a2ui_compose" in SYSTEM_PROMPT
    assert "坐席清单" in SYSTEM_PROMPT
    assert "human_agent_list" not in SYSTEM_PROMPT
    assert "action_text" in SYSTEM_PROMPT
    assert "严禁假装" in SYSTEM_PROMPT


def test_support_prompt_renders_kb_and_agent_fragments():
    """{knowledge_bases} / {human_agents} 动态槽：清单预注入 system prompt
    （省去清单类工具往返），无数据整节移除。"""
    from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus

    kbs = [KnowledgeBase(
        user_id=UUID(_TEST_USER_ID), name="国标文件", is_public=True,
        embedding_model="test-embed", status=KnowledgeStatus.ENABLED, doc_num=7,
    )]
    agent = SupportAgent(
        model=ScriptedChatModel(answers=[AIMessage(content="好")]),
        toolbox=_toolbox(
            retrieval=StubRetrieval(kbs=kbs),
            directory=StubDirectory("- 大头儿子（专属客服，online）擅长：售后"),
        ),
    )
    agent.invoke(_ctx("你好"))
    system = agent.model.prompts[0][0]
    assert "<knowledge_bases>" in system.content and "国标文件" in system.content
    assert "<human_agents>" in system.content and "大头儿子" in system.content

    empty = SupportAgent(
        model=ScriptedChatModel(answers=[AIMessage(content="好")]),
        toolbox=_toolbox(),
    )
    empty.invoke(_ctx("你好"))
    empty_system = empty.model.prompts[0][0]
    assert "<knowledge_bases>" not in empty_system.content
    assert "<human_agents>" not in empty_system.content


def test_rag_prompt_keeps_citation_discipline():
    """引用纪律归属 RAG 人设层（工具 description 已中性化）。"""
    from app.agents.builtin.rag.prompts import SYSTEM_PROMPT
    from app.components.knowledge.manifest import _SPEC

    assert "[n]" in SYSTEM_PROMPT
    search_spec = next(t for t in _SPEC.tools if t.name == "knowledge_search")
    assert "[index]" not in search_spec.description
    assert "角标" not in search_spec.description


# ---------------------------------------------------------------- 脱溯源投影
def _search_stream_agent(retrieval, answers):
    model = ScriptedChatModel(answers=[
        AIMessage(content="", tool_calls=[{
            "name": "knowledge_search",
            "args": {"query": "退货政策", "kb_ids": [str(_KB_ID)]},
            "id": "call-1",
        }]),
        *answers,
    ])
    return SupportAgent(model=model, toolbox=_toolbox(retrieval=retrieval))


def _run_tools_channel(agent):
    tool_items = []
    for name, item in agent.stream(_ctx("退货政策是什么")).interleave("messages", "tools"):
        if name == "tools":
            tool_items.append(item)
    return tool_items


def test_support_transformer_dual_content_on_provenance_results():
    """双内容契约：content 恒为真实检索 JSON（落库审计），display 为纯状态
    文案（前端只看调用成败）；LLM 侧 ToolMessage 仍是完整 JSON（作答质量不受影响）。"""
    retrieval = StubRetrieval(result=([_hit(content="退货政策为七天无理由。")], []))
    agent = _search_stream_agent(retrieval, [AIMessage(content="七天无理由。")])

    tool_items = _run_tools_channel(agent)
    assert [t["event"] for t in tool_items] == ["tool-started", "tool-result", "tool-finished"]
    result = tool_items[1]
    # content：真实结果（审计副本），出处元数据原样保留
    assert "文档A" in result["content"] and "sources" in result["content"]
    # display：前端只见调用成败——命中正文与出处元数据都不出现
    assert result["display"] == "调用成功"
    for forbidden in ("退货政策为七天无理由。", "文档A", "doc-1", "heading_path", "bboxes", "score", "sources", "page_start"):
        assert forbidden not in result["display"]
    # LLM 侧不受投影影响：ToolMessage 进完整检索 JSON
    tool_messages = [m for m in agent.model.prompts[1] if m.type == "tool"]
    assert "文档A" in tool_messages[0].content


def test_support_transformer_passthrough_non_provenance_results():
    """未命中说明（人类可读文本，无 sources）原样透传，不改写（无 display 键）。"""
    retrieval = StubRetrieval(result=([], []))
    agent = _search_stream_agent(retrieval, [AIMessage(content="没找到。")])

    tool_items = _run_tools_channel(agent)
    assert tool_items[1]["content"].startswith("知识库中未检索到相关内容")
    assert "display" not in tool_items[1]


def test_support_transformer_passthrough_memory_tools():
    """memory 工具不在脱溯源名单内，结果原样透传。"""
    agent = SupportAgent(model=ScriptedChatModel(answers=[
        AIMessage(content="", tool_calls=[{"name": "timeline", "args": {"query": "退货历史"}, "id": "call-1"}]),
        AIMessage(content="好"),
    ]), toolbox=_toolbox())

    tool_items = _run_tools_channel(agent)
    assert [t["event"] for t in tool_items] == ["tool-started", "tool-result", "tool-finished"]


def test_support_transformer_digest_unit():
    """投影单元：合法溯源 JSON 映射为纯状态文案；非法/空 sources 返回 None（透传）。"""
    from app.agents.builtin.support.transformer import _frontend_display

    assert _frontend_display(_SEARCH_RESULT_JSON) == "调用成功"
    assert _frontend_display("知识库中未检索到相关内容") is None
    assert _frontend_display('{"sources": [], "notes": []}') is None
    assert _frontend_display("not json") is None
    assert _frontend_display({"a": 1}) is None


# ---------------------------------------------------------------- 端到端
def test_support_stream_agui_frames_have_no_provenance_payload():
    """客服面全链路：TOOL_CALL_RESULT 的 content 为纯状态文案——前端
    knowledge-search-tool 的 parseResult 解析不出 sources，自然降级为单行
    状态展示，命中正文/出处与 SourceCard/PDF 抽屉都不出现。"""
    from app.application.translator.agui_translator import AgUiTranslator

    retrieval = StubRetrieval(result=([_hit(content="退货政策为七天无理由。")], []))
    agent = _search_stream_agent(retrieval, [AIMessage(content="七天无理由。")])

    translator = AgUiTranslator(thread_id=uuid4(), run_id="run-1")
    frames = [translator.start()]
    for name, item in agent.stream(_ctx("退货政策是什么")).interleave("messages", "tools"):
        frames.extend(translator.translate(uuid4(), name, item))
    frames.append(translator.finish())

    events = [json.loads(f.removeprefix("data: ").strip()) for f in frames]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "RUN_STARTED" and kinds[-1] == "RUN_FINISHED"
    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert result["content"] == "调用成功"
