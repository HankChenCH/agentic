"""bbox 溯源全链路契约：content_list → 解析 → 分块 → 向量展平/还原 → 工具 JSON。

各阶段已有单测，这里锁住「端到端形状」：坐标从 MinerU 0-1000 基准进入，
到 knowledge_search 的 JSON 输出仍是 0-1 归一化元组，字段名不漂移。
"""

import json
from uuid import uuid4

from langchain_core.documents import Document

from app.components.knowledge import KnowledgeComponent
from app.components.knowledge.ability.retrieval import RetrievalHit
from app.services.domain.knowledge.vector_index import KnowledgeVectorIndex
from app.infrastructures.document_parser import ParsedDocument
from app.infrastructures.document_parser.mineru_cloud_provider import _normalize_blocks
from app.models.domain.knowledge import DocumentSegment
from app.services.domain.knowledge.document_chunker import chunk_document


RAW_CONTENT_LIST = [
    {"type": "title", "text": "安装指南", "text_level": 1, "page_idx": 0, "bbox": [70, 80, 520, 110]},
    {"type": "text", "text": "安装前请确认系统版本与依赖。", "page_idx": 0, "bbox": [70, 130, 560, 160]},
    {"type": "text", "text": "Linux 下需先安装运行库。", "page_idx": 1, "bbox": [70, 100, 560, 130]},
    {"type": "table", "table_body": "<table><tr><td>依赖</td></tr></table>", "page_idx": 1, "bbox": [60, 150, 570, 300]},
]


def test_bbox_flows_from_content_list_to_tool_json():
    # 1) 解析归一化：0-1000 → 0-1（title 不进分块缓冲，正文/表格的 bbox 保留）
    blocks = _normalize_blocks(RAW_CONTENT_LIST, {})
    assert blocks[0].bbox == [0.07, 0.13, 0.56, 0.16]

    # 2) 分块：meta 携带 bboxes
    drafts = chunk_document(ParsedDocument(blocks=blocks))
    assert len(drafts) == 1
    segment = DocumentSegment(
        kb_id=uuid4(), doc_id=uuid4(), position=0, content=drafts[0].content, meta=drafts[0].meta
    )

    # 3) 向量写入展平 + 检索还原：meta["bboxes"] 形状不变
    metadata = KnowledgeVectorIndex._metadata(segment)
    assert "bboxes" not in metadata and metadata["bbox_pages"] == [0, 1, 1]
    hit = KnowledgeVectorIndex._hit(uuid4(), Document(page_content=segment.content, metadata=metadata), 0.834)
    assert hit.meta["bboxes"] == [
        {"page": 0, "bbox": [0.07, 0.13, 0.56, 0.16]},
        {"page": 1, "bbox": [0.07, 0.1, 0.56, 0.13]},
        {"page": 1, "bbox": [0.06, 0.15, 0.57, 0.3]},
    ]

    # 4) 工具 JSON：紧凑元组 [page, x0, y0, x1, y1]
    class StubRetrieval:
        def search_for_user(self, user_id, query, kb_ids=None, top_k=4):
            return (
                [RetrievalHit(
                    content=hit.content, score=hit.score, kb_id="kb", doc_id="doc",
                    doc_name="产品手册", position=hit.position, meta=hit.meta,
                )],
                [],
            )

    tools = {t.name: t for t in KnowledgeComponent(retrieval=StubRetrieval(), navigation=None).tools("builtin:demo")}
    payload = json.loads(tools["knowledge_search"].invoke({"query": "如何安装", "kb_ids": [str(uuid4())]}))
    source = payload["sources"][0]
    assert source["bboxes"][0] == [0, 0.07, 0.13, 0.56, 0.16]
    assert source["page_start"] == 0 and source["page_end"] == 1
    assert source["doc_name"] == "产品手册"
