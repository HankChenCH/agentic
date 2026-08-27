"""知识库向量 collection 的共享定义：索引命名与显式 schema。

写入侧（services/knowledge 的摄取/删除）与检索侧（本组件）共用本模块，
保证「写入的 metadata 字段」与「collection 声明的属性」严格对齐——
单一事实源。显式 schema 取代 langchain-weaviate 的隐式默认建库：
``content`` 以 gse 分词（中文 BM25 / 混合检索的前提，tokenization 建
collection 时定死、存量不可改），metadata 属性类型不再依赖服务端
auto-schema 推断；未在此列出的新 metadata 字段仍由服务端 AUTO_SCHEMA
兜底自动补列。
"""

from uuid import UUID


def index_name(kb_id: UUID) -> str:
    # 每库一 collection：嵌入模型建库时固化（KnowledgeBase.embedding_model），
    # 换模型重嵌互不污染；名字以大写字母开头满足 Weaviate collection 命名约束
    return f"Knowledge_{kb_id.hex}"


# 与 KnowledgeVectorIndex._metadata() 写入字段一一对应；新增 metadata 字段
# 时在此补声明（或依赖服务端 AUTO_SCHEMA 自动补列，但类型推断不可控）
_ID_PROPERTIES = ("kb_id", "doc_id")
_INT_PROPERTIES = ("position", "page_start", "page_end")
_ARRAY_PROPERTIES = ("heading_path", "types", "assets")
# 溯源区域：meta["bboxes"]（对象数组）不能直接进 Weaviate，写入前由
# KnowledgeVectorIndex 展平成平行数组——coords 每 4 个数对应 pages 一个条目
_INT_ARRAY_PROPERTIES = ("bbox_pages",)
_NUMBER_ARRAY_PROPERTIES = ("bbox_coords",)


def collection_schema(name: str) -> dict:
    """Weaviate collection 显式 schema（REST 格式，create_from_dict 直接可用）。"""
    return {
        "class": name,
        "properties": [
            # 正文：gse 分词支持中文 BM25（混合检索）；纯向量检索不受分词影响
            {"name": "content", "dataType": ["text"], "tokenization": "gse"},
            # 标识/过滤字段：field 分词整体成词，精确匹配且不膨胀倒排索引
            *(
                {"name": prop, "dataType": ["text"], "tokenization": "field"}
                for prop in _ID_PROPERTIES
            ),
            *({"name": prop, "dataType": ["int"]} for prop in _INT_PROPERTIES),
            *(
                {"name": prop, "dataType": ["text[]"], "tokenization": "field"}
                for prop in _ARRAY_PROPERTIES
            ),
            *({"name": prop, "dataType": ["int[]"]} for prop in _INT_ARRAY_PROPERTIES),
            *({"name": prop, "dataType": ["number[]"]} for prop in _NUMBER_ARRAY_PROPERTIES),
        ],
    }
