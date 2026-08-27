"""collection 显式 schema：与写入 metadata 字段严格对齐、gse 分词。"""

from uuid import uuid4

from app.services.domain.knowledge.collection import collection_schema, index_name


def test_index_name_format():
    kb_id = uuid4()
    name = index_name(kb_id)
    assert name == f"Knowledge_{kb_id.hex}"
    # Weaviate collection 命名约束：大写字母开头
    assert name[0].isupper()


def test_schema_declares_metadata_fields():
    props = {p["name"]: p for p in collection_schema("Knowledge_x")["properties"]}
    # 与 KnowledgeVectorIndex._metadata() 写入字段一一对应：
    # 固定三键（kb_id/doc_id/position）+ chunk meta 键（page/heading/types/assets
    # + 溯源展平的 bbox_pages/bbox_coords）+ 正文
    assert set(props) == {
        "content",
        "kb_id",
        "doc_id",
        "position",
        "page_start",
        "page_end",
        "heading_path",
        "types",
        "assets",
        "bbox_pages",
        "bbox_coords",
    }
    # 正文 gse 分词：中文 BM25 / 混合检索的前提（建库定死、存量不可改）
    assert props["content"]["tokenization"] == "gse"
    assert props["content"]["dataType"] == ["text"]
    assert props["position"]["dataType"] == ["int"]
    assert props["heading_path"]["dataType"] == ["text[]"]
    # 溯源区域展平平行数组：coords 每 4 个数对应 pages 一个条目
    assert props["bbox_pages"]["dataType"] == ["int[]"]
    assert props["bbox_coords"]["dataType"] == ["number[]"]
