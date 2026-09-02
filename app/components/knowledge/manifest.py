"""knowledge 组件清单：能力声明（spec）+ 工具构造 + LLM/前端共享契约模型。

双工具设计：LLM 先经 knowledge_list 了解当前有哪些可用知识库，再按需
自选库（kb_ids）检索——比单工具「盲搜全部库」多出显式的库选择能力，
kb_ids 缺省时退化为全库检索。可用口径是归属可见性：私有库仅属主可见、
公开库所有人可见（当前用户身份经 langgraph config 注入读取，见
``components.base.configurable_user``）。

knowledge_search 返回 JSON（``{"sources": [...], "notes": [...]}``），
形状由 ``KnowledgeSearchResult`` 契约模型单源定义：LLM 依据 sources 的
content 作答并按 index 标注 [n] 引用，前端 ToolUI 解析同一 JSON 渲染溯源
卡片（bboxes 为原文 0-1 归一化位置框，用于 PDF 高亮定位）——模型即契约，
不再是无 schema 的口头约定。检索管线（混合检索 + 命中邻域扩展 + RRF 排名
融合）收敛在 ``KnowledgeRetrievalService``，入参 top_k 只控最终返回条数，
与内部检索深度无关。
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
from app.components.knowledge.ability.retrieval import KnowledgeRetrievalService, RetrievalHit
from app.services.domain.knowledge.vector_index import DEFAULT_TOP_K

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
    position: int = Field(description="片段位置（取 knowledge_search 结果 sources 中的 position，从 0 起）")
    before: int = Field(default=1, description="向前取的相邻片段数（0-3，默认 1）")
    after: int = Field(default=1, description="向后取的相邻片段数（0-3，默认 1）")


class KnowledgeDocumentReadArgs(BaseModel):
    """knowledge_document_read 工具参数。"""

    doc_id: str = Field(description="文档 id（取 knowledge_search 或 knowledge_document_list 结果中的 doc_id）")
    start: int = Field(default=0, description="起始 position（从 0 起；续读时取上次返回 end+1）")
    end: int | None = Field(default=None, description="结束 position（含）；缺省=读到文档末尾（受单次预算截断）")


class KnowledgeDocumentListArgs(BaseModel):
    """knowledge_document_list 工具参数。"""

    kb_id: str = Field(description="知识库 id（取 knowledge_list 结果中的 id）")


class KnowledgeSource(BaseModel):
    """单条检索来源：LLM 引用与前端溯源卡片共用的结构。"""

    index: int
    kb_id: str
    doc_id: str
    doc_name: str
    position: int
    score: float
    content: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    # 紧凑元组 [page(int), x0, y0, x1, y1(float)]；存量文档无该字段则整体省略。
    # int|float 联合保持页码不被 pydantic 强转成 float（JSON 契约逐字节不变）
    bboxes: list[list[int | float]] | None = None


class KnowledgeSearchResult(BaseModel):
    """knowledge_search 工具结果契约：LLM 与前端共用同一 JSON。"""

    sources: list[KnowledgeSource] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class KnowledgeSegment(BaseModel):
    """单个有序片段：定位读取结果的最小单元（阅读用，不带 score/bboxes 控制体积）。"""

    doc_id: str
    doc_name: str
    position: int
    content: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)


class KnowledgeSegmentWindow(BaseModel):
    """定位读取（knowledge_context / knowledge_document_read）共用结果契约。

    ``start``/``end`` 为实际返回的 position 闭区间、``seg_total`` 为文档总段数
    ——agent 由此感知文档规模与读取进度，按需续读。
    """

    kb_id: str
    doc_id: str
    doc_name: str
    seg_total: int
    start: int
    end: int
    segments: list[KnowledgeSegment] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _build_knowledge_list_tool(component: "KnowledgeComponent") -> StructuredTool:
    service = component.retrieval
    # config 的类型注解必须是裸 RunnableConfig（langchain 按类型严格相等
    # 识别注入参数，| None 会导致注入失效）；默认值只为语法/直调兜底
    def knowledge_list(config: RunnableConfig = None) -> str:
        kbs = service.list_visible_knowledge(configurable_user(config))
        if not kbs:
            return "当前没有可用的知识库"
        lines = []
        for index, kb in enumerate(kbs, start=1):
            visibility = "公开" if kb.is_public else "私有"
            line = f"{index}. {kb.name}（id: {kb.id}，{visibility}，{kb.doc_num} 篇文档，{kb.status.value}）"
            if kb.description:
                line += f" — {kb.description}"
            lines.append(line)
        return "\n".join(lines)

    return StructuredTool.from_function(
        name="knowledge_list",
        description=(
            "列出当前用户可用的知识库（名称、id、公开/私有、文档数、状态）："
            "自己的私有库与所有人可见的公开库。"
            "回答资料性/事实性问题前先调用本工具了解有哪些知识库，再用 knowledge_search 检索；"
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
        hits, notes = service.search_for_user(
            configurable_user(config), query, kb_ids=selected, top_k=top_k if top_k > 0 else DEFAULT_TOP_K
        )
        return render_search_result(hits, notes)

    return StructuredTool.from_function(
        name="knowledge_search",
        description=(
            "在知识库中检索与问题相关的文档片段，返回带出处（文档名/页码/标题路径/原文位置框）的 JSON。"
            "检索为混合检索（语义+关键词），命中片段会连同其前后相邻片段一起做融合排序："
            "score 为混合检索相关度，来源顺序为融合名次（可能与 score 大小略有出入，按顺序引用即可）。"
            "query: 检索问题或关键词；"
            "kb_ids: 要检索的知识库 id 列表（取 knowledge_list 结果中的 id）；缺省=检索全部可用知识库；"
            "top_k: 返回的最大片段数，默认 4（仅控制返回条数，与内部检索深度无关）。"
            "返回 {\"sources\": [{index, doc_name, page_start, page_end, heading_path, score, content, bboxes, ...}], \"notes\": []}；"
            "回答时依据 sources 的 content 作答，并以 [index] 角标注明引用出处（文档名/页码）；未检索到时如实说明。"
            "命中片段是按相似度挑出的节选，可能只是某段论述/表格的中间部分："
            "需要前后文时，用该条的 doc_id + position 调 knowledge_context 看邻域；"
            "需要完整上下文时，用 knowledge_document_read 从头通读。"
        ),
        args_schema=KnowledgeSearchArgs,
        func=knowledge_search,
        infer_schema=False,
    )


def _build_knowledge_context_tool(component: "KnowledgeComponent") -> StructuredTool:
    navigation = component.navigation

    def knowledge_context(
        doc_id: str, position: int, before: int = 1, after: int = 1, config: RunnableConfig = None
    ) -> str:
        parsed = _parse_uuid(doc_id)
        if parsed is None:
            return "doc_id 无效：请取 knowledge_search 结果 sources 中的 doc_id 原值"
        window = navigation.segment_context(configurable_user(config), parsed, position, before=before, after=after)
        return render_segment_window(window, fallback=f"文档不可见或不存在（doc_id: {doc_id}）")

    return StructuredTool.from_function(
        name="knowledge_context",
        description=(
            "查看某个已检索片段的前后相邻片段，补齐命中片段缺失的上下文（分段约为 800 字符，"
            "命中常是论述/表格的中间部分）。doc_id 与 position 取 knowledge_search 结果对应"
            "来源中的原值；before/after 各控制向前/向后取几段（0-3，默认 1）。"
            "返回按 position 有序的片段窗口 JSON（含文档总段数 seg_total 与实际返回区间 start/end）；"
            "看过窗口仍不够时，可加大 before/after 或改用 knowledge_document_read 通读。"
        ),
        args_schema=KnowledgeContextArgs,
        func=knowledge_context,
        infer_schema=False,
    )


def _build_knowledge_document_read_tool(component: "KnowledgeComponent") -> StructuredTool:
    navigation = component.navigation

    def knowledge_document_read(
        doc_id: str, start: int = 0, end: int | None = None, config: RunnableConfig = None
    ) -> str:
        parsed = _parse_uuid(doc_id)
        if parsed is None:
            return "doc_id 无效：请取 knowledge_search 或 knowledge_document_list 结果中的 doc_id 原值"
        window = navigation.read_document(configurable_user(config), parsed, start=start, end=end)
        return render_segment_window(window, fallback=f"文档不可见或不存在（doc_id: {doc_id}）")

    return StructuredTool.from_function(
        name="knowledge_document_read",
        description=(
            "按 position 顺序读取文档片段，用于通读或续读：knowledge_search 只返回节选，"
            "需要完整上下文（长表格全文、连续章节、整篇短文档）时使用。start 为起始 "
            "position（缺省 0），end 为结束 position（含，缺省读到末尾）。单次返回有段数与"
            "字符预算，超预算会被截断并在 notes 中给出续读 position——按 notes 指引以 "
            "start 续读即可顺序读完；返回 JSON 含文档总段数 seg_total 与实际返回区间。"
        ),
        args_schema=KnowledgeDocumentReadArgs,
        func=knowledge_document_read,
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
            "有哪些资料再按需 knowledge_document_read 通读——不需要关键词、想看全库"
            "内容清单时使用，代替盲目多次检索。kb_id 取 knowledge_list 结果中的 id；"
            "仅已启用（enabled）的库可列出。"
        ),
        args_schema=KnowledgeDocumentListArgs,
        func=knowledge_document_list,
        infer_schema=False,
    )


def render_segment_window(window, fallback: str) -> str:
    """定位读取结果 → 工具结果串（knowledge_context / knowledge_document_read 共用组装点）。

    未命中（库/文档不可见或未启用）返回 fallback 说明；命中返回
    ``KnowledgeSegmentWindow`` 的 JSON，notes（越界/预算截断指引）附在尾部。
    """
    if window is None:
        return fallback
    payload = KnowledgeSegmentWindow(
        kb_id=str(window.kb_id),
        doc_id=str(window.doc_id),
        doc_name=window.doc_name,
        seg_total=window.seg_total,
        start=window.start,
        end=window.end,
        segments=[_segment_source(segment, window.doc_name) for segment in window.segments],
        notes=window.notes,
    ).model_dump()
    return json.dumps(payload, ensure_ascii=False)


def _segment_source(segment, doc_name: str) -> KnowledgeSegment:
    meta = segment.meta or {}
    return KnowledgeSegment(
        doc_id=str(segment.doc_id),
        doc_name=doc_name,
        position=segment.position,
        content=segment.content,
        page_start=meta.get("page_start"),
        page_end=meta.get("page_end"),
        heading_path=meta.get("heading_path") or [],
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
    """检索结果 → 工具/图节点共用的结果串（KnowledgeSearchResult JSON 契约的唯一组装点）。

    未命中返回人类可读的未检到说明（有跳过说明则附在尾部），命中返回
    ``KnowledgeSearchResult`` 的 JSON（无 bbox 的来源整体省略该字段，
    前端降级为页码跳转）。knowledge_search 工具与 builtin:rag 的检索节点
    共用本函数，保证 LLM 与前端看到同一形状。
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
            description="查看已检索片段的前后相邻片段（邻域窗口），补齐命中片段缺失的上下文。",
            args_model=KnowledgeContextArgs,
            build=_build_knowledge_context_tool,
        ),
        ToolSpec(
            name="knowledge_document_read",
            description="按 position 顺序读取文档片段（通读/续读），单次有预算截断并附续读指引。",
            args_model=KnowledgeDocumentReadArgs,
            build=_build_knowledge_document_read_tool,
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
