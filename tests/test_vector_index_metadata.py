"""向量 metadata 的溯源展平/还原 round-trip（Weaviate 不存对象数组）。"""

from uuid import uuid4

from langchain_core.documents import Document

from app.components.knowledge.vector_index import KnowledgeVectorIndex
from app.models.domain.knowledge import DocumentSegment


def make_segment(meta):
    return DocumentSegment(
        kb_id=uuid4(),
        doc_id=uuid4(),
        position=3,
        content="片段内容",
        meta=meta,
    )


def test_metadata_flattens_bboxes_into_parallel_arrays():
    segment = make_segment(
        {
            "page_start": 2,
            "page_end": 3,
            "heading_path": ["安装"],
            "types": ["text"],
            "assets": [],
            "bboxes": [
                {"page": 2, "bbox": [0.1, 0.2, 0.8, 0.3]},
                {"page": 3, "bbox": [0.05, 0.4, 0.9, 0.5]},
            ],
        }
    )
    metadata = KnowledgeVectorIndex._metadata(segment)

    assert "bboxes" not in metadata  # 对象数组不进 Weaviate
    assert metadata["bbox_pages"] == [2, 3]
    assert metadata["bbox_coords"] == [0.1, 0.2, 0.8, 0.3, 0.05, 0.4, 0.9, 0.5]
    assert metadata["page_start"] == 2  # 其余 meta 原样透传


def test_metadata_without_bboxes_keeps_legacy_shape():
    metadata = KnowledgeVectorIndex._metadata(make_segment({"page_start": 1, "page_end": 1}))
    assert "bboxes" not in metadata
    assert "bbox_pages" not in metadata and "bbox_coords" not in metadata


def test_hit_rehydrates_bboxes_round_trip():
    segment = make_segment(
        {
            "page_start": 2,
            "page_end": 3,
            "heading_path": ["安装"],
            "bboxes": [
                {"page": 2, "bbox": [0.1, 0.2, 0.8, 0.3]},
                {"page": 3, "bbox": [0.05, 0.4, 0.9, 0.5]},
            ],
        }
    )
    metadata = KnowledgeVectorIndex._metadata(segment)
    hit = KnowledgeVectorIndex._hit(uuid4(), Document(page_content="片段", metadata=metadata), 0.9)

    assert hit.meta["bboxes"] == [
        {"page": 2, "bbox": [0.1, 0.2, 0.8, 0.3]},
        {"page": 3, "bbox": [0.05, 0.4, 0.9, 0.5]},
    ]
    assert "bbox_pages" not in hit.meta and "bbox_coords" not in hit.meta
    assert hit.position == 3 and hit.meta["heading_path"] == ["安装"]


def test_hit_degrades_on_mismatched_flattened_arrays():
    # 长度不符的脏数据：还原放弃，不产出畸形 bboxes
    doc = Document(
        page_content="片段",
        metadata={"kb_id": "k", "doc_id": "d", "position": 0, "bbox_pages": [1, 2], "bbox_coords": [0.1, 0.2]},
    )
    hit = KnowledgeVectorIndex._hit(uuid4(), doc, 0.5)
    assert "bboxes" not in hit.meta
    assert "bbox_pages" not in hit.meta and "bbox_coords" not in hit.meta
