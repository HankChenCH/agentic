"""知识库检索工具（供智能体 build_tools 装配），闭包绑定检索服务与 agent 身份。

双工具设计：LLM 先经 knowledge_list 了解当前有哪些可用知识库，再按需
自选库（kb_ids）检索——比单工具「盲搜全部库」多出显式的库选择能力，
kb_ids 缺省时退化为全库检索。

knowledge_search 返回 JSON（``{"sources": [...], "notes": [...]}``）：LLM
与前端共用同一结果——LLM 依据 sources 的 content 作答并按 index 标注 [n]
引用，前端 ToolUI 解析同一 JSON 渲染溯源卡片（bboxes 为原文 0-1 归一化
位置框，用于 PDF 高亮定位）。
"""

import json
from uuid import UUID

from app.components.knowledge.service import KnowledgeRetrievalService, RetrievalHit
from app.components.knowledge.vector_index import DEFAULT_TOP_K

# 单来源 bbox 条数上限：控制工具结果体积（溯源展示取前若干块已够定位）
_MAX_SOURCE_BBOXES = 12


def build_knowledge_tools(retrieval_service: KnowledgeRetrievalService, agentic_id: str) -> list:
    def knowledge_list() -> str:
        """
        列出当前可用的知识库（名称、id、描述、文档数、状态）。
        回答资料性/事实性问题前先调用本工具了解有哪些知识库，再用 knowledge_search 检索；
        只有「已启用（enabled）」状态的知识库可被检索。
        """
        kbs = retrieval_service.list_knowledge_for_agent(agentic_id)
        if not kbs:
            return "当前没有可用的知识库"
        lines = []
        for index, kb in enumerate(kbs, start=1):
            line = f"{index}. {kb.name}（id: {kb.id}，{kb.doc_num} 篇文档，{kb.status.value}）"
            if kb.description:
                line += f" — {kb.description}"
            lines.append(line)
        return "\n".join(lines)

    def knowledge_search(
        query: str, kb_ids: list[str] | None = None, top_k: int = DEFAULT_TOP_K
    ) -> str:
        """
        在知识库中检索与问题相关的文档片段，返回带出处（文档名/页码/标题路径/原文位置框）的 JSON。
        query: 检索问题或关键词
        kb_ids: 要检索的知识库 id 列表（取 knowledge_list 结果中的 id）；缺省=检索全部可用知识库
        top_k: 返回的最大片段数，默认 4
        返回 {"sources": [{index, doc_name, page_start, page_end, heading_path, score, content, bboxes, ...}], "notes": []}；
        回答时依据 sources 的 content 作答，并以 [index] 角标注明引用出处（文档名/页码）；未检索到时如实说明。
        """
        selected = _parse_kb_ids(kb_ids)
        hits, notes = retrieval_service.search_for_agent(
            agentic_id, query, kb_ids=selected, top_k=top_k if top_k > 0 else DEFAULT_TOP_K
        )
        if not hits:
            message = "知识库中未检索到相关内容"
            return "\n".join([message, *notes]) if notes else message
        payload = {
            "sources": [_hit_source(index, hit) for index, hit in enumerate(hits, start=1)],
            "notes": notes,
        }
        return json.dumps(payload, ensure_ascii=False)

    return [knowledge_list, knowledge_search]


def _parse_kb_ids(kb_ids: list[str] | None) -> list[UUID] | None:
    # LLM 生成的 id 可能非法（幻觉/截断）：剔除无效项而非整体失败；
    # 合法但不属于本 agent 的 id 由服务端归入「未绑定已忽略」说明
    if not kb_ids:
        return None
    valid = []
    for raw in kb_ids:
        try:
            valid.append(UUID(raw))
        except (ValueError, AttributeError, TypeError):
            continue
    return valid or None


def _hit_source(index: int, hit: RetrievalHit) -> dict:
    """单条命中 → 结构化来源（LLM 引用与前端溯源卡片共用）。"""
    meta = hit.meta or {}
    source = {
        "index": index,
        "kb_id": hit.kb_id,
        "doc_id": hit.doc_id,
        "doc_name": hit.doc_name or "未知文档",
        "position": hit.position,
        "score": round(hit.score, 2),
        "content": hit.content,
        "page_start": meta.get("page_start"),
        "page_end": meta.get("page_end"),
        "heading_path": meta.get("heading_path") or [],
    }
    # bbox 紧凑元组 [page, x0, y0, x1, y1]（0-1 归一化）；存量文档无该字段则整体省略
    bboxes = _compact_bboxes(meta.get("bboxes"))
    if bboxes:
        source["bboxes"] = bboxes
    return source


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
