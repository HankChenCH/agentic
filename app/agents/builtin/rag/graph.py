"""builtin:rag 的图装配：线性主干 + 一条改写纠正分支。

拓扑（行业共识的生产粒度：condense → retrieve → grade → generate 主干，
未命中改写重试一次，仍空如实兜底）::

    START → understand ─┬─ chitchat ────────────────→ generate → END
                        └─ knowledge → retrieve → grade ─┬─ 有相关块 → generate
                                                         ├─ 重试配额未尽 → rewrite → retrieve
                                                         └─ 配额用尽 → fallback → END

唯一循环边是 rewrite → retrieve，终止条件由 ``_route_after_grade`` 保证
（relevant 非空 / 配额用尽 / 兜底），配合 RagAgent._config 的 recursion_limit
双保险。无 checkpointer（多轮上下文由输入携带）。
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.builtin.rag.nodes import MAX_REWRITES, RagNodes
from app.agents.builtin.rag.state import RagState
from langchain_core.language_models import BaseChatModel
from app.components.knowledge.ability.retrieval import KnowledgeRetrievalService


def build_rag_graph(model: BaseChatModel, retrieval: KnowledgeRetrievalService) -> CompiledStateGraph:
    """编译 RAG 图（构建期一次，stream/invoke 复用）。"""
    nodes = RagNodes(model=model, retrieval=retrieval)
    graph = StateGraph(RagState)
    graph.add_node("understand", nodes.understand)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("grade", nodes.grade)
    graph.add_node("rewrite", nodes.rewrite)
    graph.add_node("generate", nodes.generate)
    graph.add_node("fallback", nodes.fallback)

    graph.add_edge(START, "understand")
    graph.add_conditional_edges(
        "understand",
        _route_after_understand,
        {"retrieve": "retrieve", "generate": "generate"},
    )
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        _route_after_grade,
        {"generate": "generate", "rewrite": "rewrite", "fallback": "fallback"},
    )
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("generate", END)
    graph.add_edge("fallback", END)
    return graph.compile()


def _route_after_understand(state: RagState) -> str:
    """understand 之后：chitchat 直答，knowledge 进检索链。"""
    return "retrieve" if state.get("route") == "knowledge" else "generate"


def _route_after_grade(state: RagState) -> str:
    """grade 之后的三路路由：有相关块生成 / 未命中改写重试 / 配额用尽兜底。"""
    if state.get("relevant"):
        return "generate"
    if state.get("rewrite_count", 0) < MAX_REWRITES:
        return "rewrite"
    return "fallback"
