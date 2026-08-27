"""分块器：meta 的 bboxes 聚合（去重/封顶/跨页合并）。"""

from app.infrastructures.document_parser import ParsedBlock, ParsedBlockType, ParsedDocument
from app.services.domain.knowledge.document_chunker import _MAX_SEGMENT_BBOXES, chunk_document


def text_block(text, page=0, bbox=None):
    return ParsedBlock(type=ParsedBlockType.TEXT, text=text, page_idx=page, bbox=bbox)


def test_segment_meta_aggregates_bboxes_across_pages():
    parsed = ParsedDocument(
        blocks=[
            text_block("第一段。", page=0, bbox=[0.1, 0.1, 0.9, 0.2]),
            text_block("第二段。", page=1, bbox=[0.1, 0.3, 0.9, 0.4]),
        ]
    )
    drafts = chunk_document(parsed)
    assert len(drafts) == 1
    assert drafts[0].meta["page_start"] == 0 and drafts[0].meta["page_end"] == 1
    assert drafts[0].meta["bboxes"] == [
        {"page": 0, "bbox": [0.1, 0.1, 0.9, 0.2]},
        {"page": 1, "bbox": [0.1, 0.3, 0.9, 0.4]},
    ]


def test_split_long_block_dedupes_same_bbox():
    # 超长正文被 _split_long 切成多片：同一 block 引用重复出现，各段 bbox 只留一份
    long_text = "\n".join(f"第{i}行内容，用于撑过单段硬上限。" for i in range(200))
    parsed = ParsedDocument(blocks=[text_block(long_text, page=2, bbox=[0.1, 0.1, 0.9, 0.9])])
    drafts = chunk_document(parsed)
    assert len(drafts) > 1
    assert all(
        draft.meta.get("bboxes") == [{"page": 2, "bbox": [0.1, 0.1, 0.9, 0.9]}]
        for draft in drafts
    )


def test_bboxes_capped_and_absent_when_no_bbox():
    # 封顶：唯一 bbox 数超过上限时截断，防御超长段 meta 膨胀
    many = [
        text_block(f"段{i}。", page=i, bbox=[round(i / 1000, 3), 0.1, 0.5, 0.2])
        for i in range(_MAX_SEGMENT_BBOXES + 10)
    ]
    drafts = chunk_document(ParsedDocument(blocks=many))
    assert len(drafts) == 1  # 短块全部落进同一段
    assert len(drafts[0].meta["bboxes"]) == _MAX_SEGMENT_BBOXES

    # 无 bbox 的块：meta 不含 bboxes 键（与存量文档形状一致）
    drafts = chunk_document(ParsedDocument(blocks=[text_block("普通正文。")]))
    assert "bboxes" not in drafts[0].meta
