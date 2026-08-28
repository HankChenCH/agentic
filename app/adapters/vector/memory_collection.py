"""记忆向量 collection 的共享定义：索引命名与显式 schema。

写入侧（services/domain/memory/vector_index 的 metadata 组装）与检索侧
共用本模块——「写入的 metadata 字段」与「collection 声明的属性」严格对齐，
单一事实源（惯例同知识域 services/domain/knowledge/collection.py）。

单一 collection ``Memory`` 承载三类对象，kind 属性区分：
- entity     实体（name+aliases 拼接）
- statement  陈述整句 summary
- episode    事件梗概 summary
``content`` 用 gse 分词（中文 BM25/混合检索前提，建库时定死存量不可改）；
标识/过滤属性 field 分词整体成词。向量对象 id 由 vector_index 以
``{kind}_{ref_id}`` 确定性生成——同 id 重写即覆盖。
"""

MEMORY_INDEX_NAME = "Memory"

_KIND_PROPERTIES = ("kind", "thread_id")
_INT_PROPERTIES = ("ref_id", "occurred_at")


def collection_schema(name: str) -> dict:
    """Weaviate collection 显式 schema（REST 格式）。"""
    return {
        "class": name,
        "properties": [
            {"name": "content", "dataType": ["text"], "tokenization": "gse"},
            *(
                {"name": prop, "dataType": ["text"], "tokenization": "field"}
                for prop in _KIND_PROPERTIES
            ),
            *({"name": prop, "dataType": ["int"]} for prop in _INT_PROPERTIES),
        ],
    }
