"""knowledge 组件清单：能力声明（spec）+ 工具构造 + LLM/前端共享契约模型。

四件工具的分工与纪律（引导全收在各工具 description，不散落 agent 系统提示词）：
可用知识库清单默认经 system prompt 片段注入（``{knowledge_bases}`` 动态槽，
ability 门面的 digest 与 knowledge_list 工具共用同一行渲染），agent 的
knowledge_search 直接从清单选 kb_ids；knowledge_list 是清单缺失/需刷新时的
备用入口。knowledge_search 缺省不检索而是返回引导话术（运行时硬闸，防跨库
盲搜退化）；定位读取 knowledge_context 以检索结果的 doc_id + position 为
句柄精确读取某一片段（无邻域泛化、无通读用法——检索结果已自带命中邻域，
泛化读取只会诱导「多拉上下文」的工具滥用）；knowledge_document_list 是
无关键词场景的库内浏览入口。可用口径是归属可见性：私有库仅属主可见、
公开库所有人可见（当前用户身份经 langgraph config 注入读取，见
``components.base.configurable_user``）。

检索与定位读取返回同形 JSON（``{"sources": [...], "notes": [...]}``），
形状由 ``KnowledgeSearchResult`` 契约模型单源定义：LLM 依据 sources 的
content 作答（引用策略属智能体人设层——是否标注 [n] 角标由各 agent 的
system prompt 决定，工具 description 保持中性，见 builtin/rag/prompts.py），
前端 ToolUI 解析同一 JSON 渲染溯源
卡片（bboxes 为原文 0-1 归一化位置框，用于 PDF 高亮定位）——模型即契约，
不再是无 schema 的口头约定；定位读取无检索相关度，score 字段整体省略。
检索管线（混合检索 + 命中邻域扩展 + RRF 排名融合）收敛在
``KnowledgeRetrievalService``，入参 top_k 只控最终返回条数，与内部检索
深度无关。
"""

import json
from dataclasses import dataclass
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from wireup import injectable

from app.components.base import (
    ComponentSpec,
    ToolSpec,
    configurable_user,
    register_component,
)
from app.components.knowledge.ability.navigation import KnowledgeNavigationService
from app.components.knowledge.ability.retrieval import (
    KnowledgeRetrievalService,
    RetrievalHit,
    render_kb_lines,
)
from app.domain.knowledge.ports import DEFAULT_TOP_K

# 单来源 bbox 条数上限：控制工具结果体积（溯源展示取前若干块已够定位）
_MAX_SOURCE_BBOXES = 12


class KnowledgeListArgs(BaseModel):
    """knowledge_list 无参数。"""


class KnowledgeSearchArgs(BaseModel):
    """knowledge_search 工具参数。"""

    query: str = Field(description="检索问题或关键词")
    kb_ids: list[str] | None = Field(
        default=None,
        description="要检索的知识库 id 列表（取 knowledge_list 结果中的 id）；缺省=检索全部可用知识库",
    )
    top_k: int = Field(default=DEFAULT_TOP_K, description="返回的最大片段数，默认 4（仅控制返回条数，与内部检索深度无关）")


class KnowledgeContextArgs(BaseModel):
    """knowledge_context 工具参数。"""

    doc_id: str = Field(description="文档 id（取 knowledge_search 结果 sources 中的 doc_id）")
    position: int = Field(description="要读取的片段位置（取 knowledge_search 结果 sources 中的 position，从 0 起）")


class KnowledgeDocumentListArgs(BaseModel):
    """knowledge_document_list 工具参数。"""

    kb_id: str = Field(description="知识库 id（取 knowledge_list 结果中的 id）")


class KnowledgeSource(BaseModel):
    """单条检索来源：LLM 引用与前端溯源卡片共用的结构（检索与定位读取同形）。"""

    index: int
    kb_id: str
    doc_id: str
    doc_name: str
    position: int
    # 混合检索相关度（0-1）；定位读取无检索分，字段整体省略（同 bboxes 口径）
    score: float | None = None
    content: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    # 紧凑元组 [page(int), x0, y0, x1, y1(float)]；存量文档无该字段则整体省略。
    # int|float 联合保持页码不被 pydantic 强转成 float（JSON 契约逐字节不变）
    bboxes: list[list[int | float]] | None = None


class KnowledgeSearchResult(BaseModel):
    """检索/定位读取共用结果契约：LLM 与前端共用同一 JSON。"""

    sources: list[KnowledgeSource] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _build_knowledge_list_tool(component: "KnowledgeComponent") -> StructuredTool:
    service = component.retrieval
    # config 的类型注解必须是裸 RunnableConfig（langchain 按类型严格相等
    # 识别注入参数，| None 会导致注入失效）；默认值只为语法/直调兜底
    def knowledge_list(config: RunnableConfig = None) -> str:
        kbs = service.list_visible_knowledge(configurable_user(config))
        return render_kb_lines(kbs) or "当前没有可用的知识库"

    return StructuredTool.from_function(
        name="knowledge_list",
        description=(
            "列出当前用户可用的知识库（名称、id、公开/私有、文档数、状态）："
            "自己的私有库与所有人可见的公开库。"
            "system prompt 已附知识库清单时无需调用本工具，直接从清单取 kb_ids 检索；"
            "仅当清单缺失或需要最新状态时调用。"
            "只有「已启用（enabled）」状态的知识库可被检索。"
        ),
        args_schema=KnowledgeListArgs,
        func=knowledge_list,
        infer_schema=False,
    )


def _build_knowledge_search_tool(component: "KnowledgeComponent") -> StructuredTool:
    service = component.retrieval

    def knowledge_search(
        query: str, kb_ids: list[str] | None = None, top_k: int = DEFAULT_TOP_K, config: RunnableConfig = None
    ) -> str:
        selected = _parse_kb_ids(kb_ids)
        if not selected:
            # 硬闸：缺省全库检索是跨库盲搜与结果稀释的根源——强制先 list 后按需选库
            return (
                "未指定检索范围（kb_ids）：请先调用 knowledge_list 获取可用知识库清单，"
                "再从结果中选择与问题最相关的知识库 id 作为 kb_ids 传入（不支持缺省全库检索）。"
            )
        hits, notes = service.search_for_user(
            configurable_user(config), query, kb_ids=selected, top_k=top_k if top_k > 0 else DEFAULT_TOP_K
        )
        return render_search_result(hits, notes)

    return StructuredTool.from_function(
        name="knowledge_search",
        description=(
            "在指定的知识库中检索与问题相关的文档片段，返回带出处（文档名/页码/标题路径/原文位置框）的 JSON。"
            "检索为混合检索（语义+关键词），命中片段会连同其前后相邻片段一起做融合排序："
            "score 为混合检索相关度，来源顺序为融合名次（可能与 score 大小略有出入，按顺序引用即可）。"
            "kb_ids 必填：从 system prompt 的知识库清单中选取与问题最相关的库 id"
            "（清单缺失时先调 knowledge_list），不支持缺省全库检索。"
            "query: 检索问题或关键词；"
            "top_k: 返回的最大片段数，默认 4（仅控制返回条数，与内部检索深度无关）。"
            "返回 {\"sources\": [{index, doc_name, page_start, page_end, heading_path, score, content, bboxes, ...}], \"notes\": []}；"
            "依据 sources 的 content 作答，未检索到时如实说明。"
            "sources 中的 doc_id + position 可调 knowledge_context 精确读取某一片段——"
            "仅在检索节选确实不足以作答时使用。"
        ),
        args_schema=KnowledgeSearchArgs,
        func=knowledge_search,
        infer_schema=False,
    )


def _build_knowledge_context_tool(component: "KnowledgeComponent") -> StructuredTool:
    navigation = component.navigation

    def knowledge_context(doc_id: str, position: int, config: RunnableConfig = None) -> str:
        parsed = _parse_uuid(doc_id)
        if parsed is None:
            return "doc_id 无效：请取 knowledge_search 结果 sources 中的 doc_id 原值"
        window = navigation.segment_at(configurable_user(config), parsed, position)
        return render_context_result(window, fallback=f"文档不可见或不存在（doc_id: {doc_id}）")

    return StructuredTool.from_function(
        name="knowledge_context",
        description=(
            "精确读取指定文档的指定位置：返回该 position 的单个片段（与 knowledge_search"
            " 同形的 sources JSON，单来源、无 score）。doc_id 与 position 取 knowledge_search"
            " 结果中的原值。仅用于定位需求——核对检索节选是否完整、读取节选旁某个确切位置、"
            "按 position 逐段核查；检索结果已附带命中片段的邻域，多数问题无需调用本工具，"
            "也没有通读全文的用法。"
        ),
        args_schema=KnowledgeContextArgs,
        func=knowledge_context,
        infer_schema=False,
    )


def _build_knowledge_document_list_tool(component: "KnowledgeComponent") -> StructuredTool:
    navigation = component.navigation

    def knowledge_document_list(kb_id: str, config: RunnableConfig = None) -> str:
        parsed = _parse_uuid(kb_id)
        if parsed is None:
            return "kb_id 无效：请取 knowledge_list 结果中的 id 原值"
        docs = navigation.list_documents(configurable_user(config), parsed)
        if docs is None:
            return "知识库不可见、不存在或未启用"
        if not docs:
            return "该知识库暂无文档"
        lines = []
        for index, doc in enumerate(docs, start=1):
            line = f"{index}. {doc.name}（doc_id: {doc.id}，{doc.seg_num} 段，{doc.status.value}）"
            if doc.description:
                line += f" — {doc.description}"
            lines.append(line)
        return "\n".join(lines)

    return StructuredTool.from_function(
        name="knowledge_document_list",
        description=(
            "列出某个知识库内的文档（名称、doc_id、分段数、状态、描述），用于浏览库中"
            "有哪些资料——不需要关键词、想看全库内容清单时使用，代替盲目多次检索，"
            "并可按需 knowledge_context 精确读取某篇文档的某段。kb_id 取 knowledge_list "
            "结果中的 id；仅已启用（enabled）的库可列出。"
        ),
        args_schema=KnowledgeDocumentListArgs,
        func=knowledge_document_list,
        infer_schema=False,
    )


def render_context_result(window, fallback: str) -> str:
    """定位读取结果 → 与 knowledge_search 同形的 sources JSON（前端复用溯源卡片）。

    window 为 None（库/文档不可见或未启用）返回 fallback；position 越界
    返回空 sources + 越界说明；命中返回分段来源——无检索相关度（score 与
    无 bbox 同款整体省略），notes 附文档规模供 agent 感知合法 position 区间。
    """
    if window is None:
        return fallback
    sources = [
        _context_source(index, segment, window)
        for index, segment in enumerate(window.segments, start=1)
    ]
    notes = list(window.notes)
    if sources:
        notes.append(f"文档「{window.doc_name}」共 {window.seg_total} 段（position 0-{window.seg_total - 1}）")
    payload = KnowledgeSearchResult(sources=sources, notes=notes).model_dump()
    for source in payload["sources"]:
        if source["score"] is None:
            del source["score"]
        if not source["bboxes"]:
            del source["bboxes"]
    return json.dumps(payload, ensure_ascii=False)


def _context_source(index: int, segment, window) -> KnowledgeSource:
    """定位读取的分段行 → 与检索同形的来源（无 score，溯源 meta 齐全）。"""
    meta = segment.meta or {}
    return KnowledgeSource(
        index=index,
        kb_id=str(window.kb_id),
        doc_id=str(segment.doc_id),
        doc_name=window.doc_name,
        position=segment.position,
        score=None,
        content=segment.content,
        page_start=meta.get("page_start"),
        page_end=meta.get("page_end"),
        heading_path=meta.get("heading_path") or [],
        bboxes=_compact_bboxes(meta.get("bboxes")) or None,
    )


def _parse_uuid(raw: str) -> UUID | None:
    # LLM 生成的 id 可能非法（幻觉/截断）：无效时返回 None 由调用方出引导话术
    try:
        return UUID(raw)
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_kb_ids(kb_ids: list[str] | None) -> list[UUID] | None:
    # LLM 生成的 id 可能非法（幻觉/截断）：剔除无效项而非整体失败；
    # 合法但当前用户不可见的 id 由服务端归入「不可见已忽略」说明
    if not kb_ids:
        return None
    valid = []
    for raw in kb_ids:
        try:
            valid.append(UUID(raw))
        except (ValueError, AttributeError, TypeError):
            continue
    return valid or None


def render_search_result(hits: list[RetrievalHit], notes: list[str]) -> str:
    """检索结果 → 检索类消费方共用的结果串（KnowledgeSearchResult JSON 契约的唯一组装点）。

    未命中返回人类可读的未检到说明（有跳过说明则附在尾部），命中返回
    ``KnowledgeSearchResult`` 的 JSON（无 bbox 的来源整体省略该字段，
    前端降级为页码跳转）。knowledge_search 工具与 knowledge_context 的
    定位读取共用同一形状，保证 LLM 与前端看到一致结构。
    """
    if not hits:
        message = "知识库中未检索到相关内容"
        return "\n".join([message, *notes]) if notes else message
    result = KnowledgeSearchResult(
        sources=[hit_source(index, hit) for index, hit in enumerate(hits, start=1)],
        notes=notes,
    )
    payload = result.model_dump()
    for source in payload["sources"]:
        # 与契约一致：无 bbox 的来源整体省略该字段（前端降级为页码跳转）
        if not source["bboxes"]:
            del source["bboxes"]
    return json.dumps(payload, ensure_ascii=False)


def hit_source(index: int, hit: RetrievalHit) -> KnowledgeSource:
    """单条命中 → 结构化来源（LLM 引用与前端溯源卡片共用）。"""
    meta = hit.meta or {}
    return KnowledgeSource(
        index=index,
        kb_id=hit.kb_id,
        doc_id=hit.doc_id,
        doc_name=hit.doc_name or "未知文档",
        position=hit.position,
        score=round(hit.score, 2),
        content=hit.content,
        page_start=meta.get("page_start"),
        page_end=meta.get("page_end"),
        heading_path=meta.get("heading_path") or [],
        bboxes=_compact_bboxes(meta.get("bboxes")) or None,
    )


def _compact_bboxes(bboxes: list | None) -> list[list]:
    """meta 的 bboxes → 紧凑元组数组并封顶；脏条目跳过不阻断。"""
    if not isinstance(bboxes, list):
        return []
    compact = []
    for item in bboxes:
        if not isinstance(item, dict):
            continue
        bbox = item.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        try:
            quad = [round(float(v), 3) for v in bbox]
            compact.append([int(item["page"]), *quad])
        except (KeyError, TypeError, ValueError):
            continue
        if len(compact) >= _MAX_SOURCE_BBOXES:
            break
    return compact


_SPEC = register_component(ComponentSpec(
    name="knowledge",
    title="知识库检索",
    description=(
        "按当前用户可见的知识库（自己的私有库 + 公开库）做 RAG 检索与定位读取："
        "先列出可用库再按需检索文档片段（结果带文档名/页码/标题路径/原文位置框等"
        "溯源信息，供引用作答与前端溯源卡片共用）；命中片段可再按 doc_id + "
        "position 邻域扩展或整篇通读，支持浏览库内文档清单。"
    ),
    tools=(
        ToolSpec(
            name="knowledge_list",
            title="查看知识库清单",
            description="列出当前用户可用的知识库（名称、id、公开/私有、文档数、状态）。",
            args_model=KnowledgeListArgs,
            build=_build_knowledge_list_tool,
        ),
        ToolSpec(
            name="knowledge_search",
            title="知识库检索",
            description="在指定（或全部可用）知识库中检索相关文档片段，返回带溯源信息的 JSON。",
            args_model=KnowledgeSearchArgs,
            build=_build_knowledge_search_tool,
        ),
        ToolSpec(
            name="knowledge_context",
            title="读取文档片段",
            description="精确读取指定文档的指定位置片段（检索结果的 doc_id + position 为句柄）。",
            args_model=KnowledgeContextArgs,
            build=_build_knowledge_context_tool,
        ),
        ToolSpec(
            name="knowledge_document_list",
            title="查看文档清单",
            description="列出知识库内的文档清单（名称、doc_id、分段数、状态），导航入口。",
            args_model=KnowledgeDocumentListArgs,
            build=_build_knowledge_document_list_tool,
        ),
    ),
))


@injectable
@dataclass
class KnowledgeComponent:
    """knowledge 组件装配器：持检索/定位读取门面服务，把 spec 声明实例化为可运行工具。

    检索范围按当前用户可见性圈定（身份经 langgraph config 注入读取），与
    agent 身份无关——``agentic_id`` 形参是与 ``MemoryComponent.tools`` 的
    统一装配接口，此处不使用。
    """

    retrieval: KnowledgeRetrievalService
    navigation: KnowledgeNavigationService

    @property
    def spec(self) -> ComponentSpec:
        return _SPEC

    def tools(self, agentic_id: str) -> list[StructuredTool]:
        return [tool.build(self) for tool in _SPEC.tools]
