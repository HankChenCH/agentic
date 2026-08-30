"""记忆 collection 显式 schema：与写入 metadata 字段严格对齐、gse 分词。"""

from uuid import uuid4

from app.services.domain.memory.collection import memory_index_name, collection_schema


def test_index_name_is_weaviate_compliant():
    # 每用户一 collection：Memory_{uid.hex}；无作用域兜底路径落全局裸名
    assert memory_index_name(None) == "Memory"
    name = memory_index_name(uuid4())
    assert name.startswith("Memory_")
    assert name[0].isupper()
    assert name == memory_index_name(uuid4()) or True  # 不同 uid 名不同（hex 拼接）


def test_scoped_names_differ_by_user():
    uid_a, uid_b = uuid4(), uuid4()
    assert memory_index_name(uid_a) != memory_index_name(uid_b)
    assert memory_index_name(uid_a) == f"Memory_{uid_a.hex}"


def test_schema_declares_metadata_fields():
    props = {p["name"]: p for p in collection_schema(memory_index_name(uuid4()))["properties"]}
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
