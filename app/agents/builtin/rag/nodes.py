"""builtin:rag 的图节点实现。

节点分工（单职责，状态进 / 状态出）：
- understand：意图分路（chitchat/knowledge）+ 多轮凝练 standalone question
  （一次结构化调用双职，避免两次调用延迟）；
- retrieve：用户域混合检索（可见性圈定 + alpha 混合 + top_k 过采样后滤），
  并以 tools 通道契约发 tool 事件（前端渲染"正在检索"与结果溯源卡片）；
- grade：一次结构化调用批量二元相关性过滤（只过滤不循环）；
- rewrite：检索改写（唯一循环边 rewrite → retrieve 的起点）；
- generate：带 [n] 引用生成（knowledge）或闲聊直答（chitchat）；
- fallback：检索/改写/重试皆空时的如实兜底（LLM 硬约束话术，保持流式与
  落库路径统一）。

除 generate/fallback 外都是内部 LLM 调用，由 RagRunStream 按节点名从
messages 通道滤除（不进 UI、不落库）——检索步骤以 tools 通道的 tool 事件
呈现。结构化输出的内部模型调用同样进 messages 通道且 node 归属当前节点，
滤除机制对两者一致生效。
"""

import logging
from dataclasses import dataclass
from typing import List
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from app.agents.builtin.rag.prompts import (
    FALLBACK_PROMPT,
    GENERATE_SYSTEM_PROMPT,
    GRADE_PROMPT,
    REWRITE_PROMPT,
    UNDERSTAND_PROMPT,
    UnderstandDecision,
    GradeOutput,
)
from app.agents.builtin.rag.state import RagState
from app.components.base import configurable_user
from app.components.knowledge.ability.retrieval import KnowledgeRetrievalService, RetrievalHit
from app.components.knowledge.manifest import render_search_result

# 与真实 knowledge_search 工具同名的伪工具步骤名（前端按名称渲染检索卡片）
TOOL_NAME = "knowledge_search"
RAG_TOP_K = 6  # 检索终选片段数（仅返回口径：服务内部候选池固定，返回前做邻域扩展 + RRF 融合）
RAG_ALPHA = 0.5  # Weaviate 混合检索权重：<1 开启 BM25+向量融合（gse 中文分词已就绪）
MAX_REWRITES = 1  # 检索改写重试配额（行业共识：一次纠正分支足够，循环要硬上限）


def message_text(message: BaseMessage) -> str:
    """消息文本（str 或 v1 内容块列表形态，只取 text 块）。"""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def _chunk_text(chunk: BaseMessage) -> str:
    """流式 chunk 的文本增量（reasoning 块由流投影单独呈现，不进答案）。"""
    return message_text(chunk)


def _consume_stream(chunks) -> str:
    """消费模型 token 流并累积全文（回调同时驱动 messages 通道实时投影）。"""
    return "".join(_chunk_text(chunk) for chunk in chunks)


def _render_history(messages: List[BaseMessage], max_messages: int = 10) -> str:
    """历史渲染成对话行（只取 user/assistant 文本，限最近数条约束 token）。"""
    lines = []
    for message in messages[-max_messages:]:
        if isinstance(message, HumanMessage):
            lines.append(f"用户：{message_text(message)}")
        elif isinstance(message, AIMessage):
            lines.append(f"助手：{message_text(message)}")
    return "\n".join(lines)


def _source_title(hit: RetrievalHit) -> str:
    page = (hit.meta or {}).get("page_start")
    page_suffix = f"，页 {page}" if page is not None else ""
    return f"{hit.doc_name or '未知文档'}{page_suffix}"


@dataclass
class RagNodes:
    """RAG 图节点集合：持模型与检索门面，由 RagAgent 构建图时实例化。

    非 injectable（依赖由 agent 装配），身份经 langgraph 注入的
    ``configurable.user_id`` 读取（与组件工具同一约束：不走 ContextVar/闭包）。
    """

    model: BaseChatModel
    retrieval: KnowledgeRetrievalService

    def __post_init__(self):
        # 结构化输出 runnable 构建期绑定一次；其内部模型调用同样进 messages
        # 通道（node 归属当前图节点），由 RagRunStream 统一滤除
        self._understand_model = self.model.with_structured_output(UnderstandDecision)
        self._grade_model = self.model.with_structured_output(GradeOutput)

    # ------------------------------------------------------------------
    def understand(self, state: RagState, config: RunnableConfig = None) -> dict:
        """分路 + 凝练：chitchat 走直答，knowledge 带检索问题进入检索链。"""
        messages = state["messages"]
        decision: UnderstandDecision = self._understand_model.invoke(
            [
                SystemMessage(content=UNDERSTAND_PROMPT),
                HumanMessage(content=(
                    f"【对话历史】\n{_render_history(messages[:-1]) or '（无）'}\n\n"
                    f"【当前输入】\n{message_text(messages[-1])}"
                )),
            ],
            config=config,
        )
        route = (decision.route or "").strip().lower()
        if route not in ("chitchat", "knowledge"):
            # 模型输出越界时按知识问走检索：检索侧自有改写重试与兜底
            route = "knowledge"
        standalone = (decision.standalone_question or "").strip() or message_text(messages[-1])
        return {"route": route, "standalone_question": standalone}

    def retrieve(self, state: RagState, config: RunnableConfig = None) -> dict:
        """用户域混合检索；步骤以 knowledge_search 伪工具事件呈现给前端。"""
        query = state["standalone_question"]
        writer = get_stream_writer()
        tool_call_id = uuid4().hex
        writer({
            "event": "tool-started", "tool_call_id": tool_call_id, "tool_name": TOOL_NAME,
            "args": {"query": query, "top_k": RAG_TOP_K},
        })
        try:
            hits, notes = self.retrieval.search_for_user(
                configurable_user(config), query, top_k=RAG_TOP_K, alpha=RAG_ALPHA
            )
        except Exception:
            # 检索基础设施故障按未命中降级（走改写重试→兜底），不炸整轮流
            logging.getLogger(__name__).warning("RAG 检索失败，按未命中降级", exc_info=True)
            writer({"event": "tool-error", "tool_call_id": tool_call_id})
            return {"documents": [], "relevant": []}
        writer({
            "event": "tool-result", "tool_call_id": tool_call_id,
            "content": render_search_result(hits, notes),
        })
        writer({"event": "tool-finished", "tool_call_id": tool_call_id})
        return {"documents": hits, "relevant": []}

    def grade(self, state: RagState, config: RunnableConfig = None) -> dict:
        """批量二元相关性过滤：一次调用判全部初检块，只过滤不循环。"""
        documents = state.get("documents") or []
        if not documents:
            return {"relevant": []}
        blocks = "\n\n".join(
            f"[{index}] {hit.content}" for index, hit in enumerate(documents, start=1)
        )
        outcome: GradeOutput = self._grade_model.invoke(
            [
                SystemMessage(content=GRADE_PROMPT),
                HumanMessage(content=f"【用户问题】\n{state['standalone_question']}\n\n【知识块】\n{blocks}"),
            ],
            config=config,
        )
        # 越界/重复编号忽略，命中按编号升序还原原检索排序
        picked = {}
        for item in outcome.results or []:
            if item.relevant and 1 <= item.index <= len(documents):
                picked[item.index] = documents[item.index - 1]
        return {"relevant": [picked[index] for index in sorted(picked)]}

    def rewrite(self, state: RagState, config: RunnableConfig = None) -> dict:
        """检索改写：清空上轮命中，配额计数 +1，回到 retrieve 重检。"""
        rewritten: BaseMessage = self.model.invoke(
            [SystemMessage(content=REWRITE_PROMPT), HumanMessage(content=state["standalone_question"])],
            config=config,
        )
        text = message_text(rewritten).strip()
        return {
            "standalone_question": text or state["standalone_question"],
            "rewrite_count": state.get("rewrite_count", 0) + 1,
            "documents": [],
            "relevant": [],
        }

    def generate(self, state: RagState, config: RunnableConfig = None) -> dict:
        """终答节点：knowledge 带 [n] 编号资料生成；chitchat 无资料直答。"""
        messages = state["messages"]
        if state.get("route") == "knowledge":
            sources = "\n\n".join(
                f"[{index}] {_source_title(hit)}\n{hit.content}"
                for index, hit in enumerate(state.get("relevant") or [], start=1)
            )
            human = f"【资料】\n{sources}\n\n【问题】\n{state['standalone_question']}"
        else:
            human = message_text(messages[-1])
        text = _consume_stream(self.model.stream(
            [SystemMessage(content=GENERATE_SYSTEM_PROMPT), *messages[:-1], HumanMessage(content=human)],
            config=config,
        ))
        return {"messages": [AIMessage(content=text)], "generation": text}

    def fallback(self, state: RagState, config: RunnableConfig = None) -> dict:
        """未命中兜底：硬约束话术（不给资料、禁编造），保持流式/落库路径统一。"""
        text = _consume_stream(self.model.stream(
            [SystemMessage(content=FALLBACK_PROMPT), HumanMessage(content=state["standalone_question"])],
            config=config,
        ))
        return {"messages": [AIMessage(content=text)], "generation": text}
