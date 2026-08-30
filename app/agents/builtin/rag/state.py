"""builtin:rag 的图状态定义。

显式 TypedDict（非 messages-only）：RAG 流程的中间产物（分路、凝练问题、
检索命中、过滤结果、重试计数）都进状态通道，节点只做单职责转换，路由函数
读状态定分支。无 checkpointer（与全库惯例一致），状态在 ``_input`` 处整体
初始化、单轮生命周期内演进。
"""

from typing import List, Literal, TypedDict

from langchain_core.messages import BaseMessage

from app.components.knowledge.ability.retrieval import RetrievalHit

Route = Literal["chitchat", "knowledge"]


class RagState(TypedDict):
    """RAG 图状态通道（默认 reducer = 覆写）。"""

    messages: List[BaseMessage]  # 输入：回放历史（已滤工具痕迹）+ 本轮用户消息
    route: Route  # understand 的分路判定
    standalone_question: str  # 脱离历史可理解的检索问题（understand/rewrite 产出）
    documents: List[RetrievalHit]  # 当轮检索初检命中（rewrite 后清空重检）
    relevant: List[RetrievalHit]  # grade 过滤后的相关命中（generate 的引用依据）
    rewrite_count: int  # 已执行的检索改写次数（重试配额）
    generation: str  # 最终回答文本（generate/fallback 产出，兼供 invoke 读取）
