"""记忆向量 collection 的共享定义：索引命名与显式 schema。

写入侧（services/domain/memory/vector_index 的 metadata 组装）与检索侧
共用本模块——「写入的 metadata 字段」与「collection 声明的属性」严格对齐，
单一事实源（惯例同知识域 services/domain/knowledge/collection.py）。

**每用户一个 collection** ``Memory_{user_id.hex}`` 承载该用户的三类对象，
kind 属性区分（镜像知识域 ``Knowledge_{kb_id.hex}`` 的每资源一库先例；
VectorStore 接口不暴露属性过滤，按用户分库是检索隔离的落点）：
- entity     实体（name+aliases 拼接）
- statement  陈述整句 summary
- episode    事件梗概 summary
``content`` 用 gse 分词（中文 BM25/混合检索前提，建库时定死存量不可改）；
标识/过滤属性 field 分词整体成词。向量对象 id 由 vector_index 以
``{kind}_{ref_id}`` 确定性生成——同 id 重写即覆盖；int 主键在用户间会撞，
用户维度由 collection 切分兜住，不再进入 id 空间。
"""

from uuid import UUID

_MEMORY_INDEX_PREFIX = "Memory"

_KIND_PROPERTIES = ("kind", "thread_id")
_INT_PROPERTIES = ("ref_id", "occurred_at")


def memory_index_name(user_id: UUID | None = None) -> str:
    """collection 名：作用域内按用户切分；无作用域（历史/CLI 兜底）用裸名。

    全局裸名 ``Memory`` 仅保留给未携带用户上下文的旧数据兜底路径——用户侧行
    程一律经 ``MemoryVectorIndex.for_user`` 得到 ``Memory_{uid.hex}``。
    """
    if user_id is None:
        return _MEMORY_INDEX_PREFIX
    return f"{_MEMORY_INDEX_PREFIX}_{user_id.hex}"


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
