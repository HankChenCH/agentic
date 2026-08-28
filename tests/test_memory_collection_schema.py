"""记忆 collection 显式 schema：与写入 metadata 字段严格对齐、gse 分词。"""

from app.services.domain.memory.collection import MEMORY_INDEX_NAME, collection_schema


def test_index_name_is_weaviate_compliant():
    assert MEMORY_INDEX_NAME == "Memory"
    assert MEMORY_INDEX_NAME[0].isupper()


def test_schema_declares_metadata_fields():
    props = {p["name"]: p for p in collection_schema(MEMORY_INDEX_NAME)["properties"]}
    # 与 MemoryVectorIndex._metadata() 写入字段一一对应：kind/thread_id 文本 +
    # ref_id/occurred_at 整数 + 正文
    assert set(props) == {"content", "kind", "thread_id", "ref_id", "occurred_at"}
    # 正文 gse 分词：中文 BM25 / 混合检索的前提（建库定死、存量不可改）
    assert props["content"]["tokenization"] == "gse"
    assert props["content"]["dataType"] == ["text"]
    assert props["kind"]["tokenization"] == "field"
    assert props["thread_id"]["tokenization"] == "field"
    assert props["ref_id"]["dataType"] == ["int"]
    assert props["occurred_at"]["dataType"] == ["int"]
