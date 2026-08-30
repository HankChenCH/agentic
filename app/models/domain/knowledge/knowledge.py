from enum import Enum
from typing import List, Union
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import SQLModel, Field, Relationship

from app.models.domain.mixin import TimeFieldMixin

class KnowledgeStatus(Enum):
    PENDING = 'pending'
    PROCESSING = 'processing'
    READY = 'ready'
    ENABLED = 'enabled'
    DISABLED = 'disabled'
    DELETING = 'deleting'
    FAILED = 'failed'

class KnowledgeBase(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "knowledge_base" # type: ignore
    # 名称唯一性是每用户的（同一属主内不重名，不同属主可同名）；
    # 全局唯一约束随用户归属引入移除（迁移中替换为复合唯一）
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_kb_user_name"),
    )

    id: UUID | None = Field(
        title="主键id",
        description="主键id",
        default_factory=uuid4,
        primary_key=True
    )

    user_id: UUID = Field(
        title="属主用户id",
        description="知识库属主用户id（FK→users.id）；私有库仅属主可见",
        foreign_key="users.id",
        index=True,
    )

    name: str = Field(
        title="知识库名称",
        description="知识库名称，最大支持25个字符（同一属主内唯一）",
        min_length=1,
        max_length=25,
    )

    is_public: bool = Field(
        title="是否公开",
        description="公开或私有标识：true 公开（全员可见、可检索），false 私有（仅属主可见）",
        default=False,
    )

    description: str = Field(
        title="知识库详情描述",
        description="知识库详情描述，最大支持500个字符",
        min_length=0,
        max_length=500,
        default="",
    )

    embedding_model: str = Field(
        title="嵌入模型标识",
        description="建库时使用的嵌入模型（llm.yaml 的 embedding 条目名），存量向量均由该模型生成；变更模型需全量重嵌",
    )

    weight: int = Field(
        title="知识库权重",
        description="知识库权重，用于管理侧排序",
        default=0
    )

    status: KnowledgeStatus = Field(
        title="知识库状态",
        description="知识库状态: pending, processing, ready, enabled, disabled, deleting, failed",
        default=KnowledgeStatus.PENDING,
        index=True,
    )

    doc_num: int = Field(
        title="文档数",
        description="知识库下的文档数（冗余计数，由 repository 层维护）",
        default=0,
    )

    documents: List["KnowledgeDocument"] = Relationship(
        back_populates="kb",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

class KnowledgeDocument(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "knowledge_base_document" # type: ignore

    id: UUID | None = Field(
        title="主键id",
        description="主键id",
        default_factory=uuid4,
        primary_key=True,
    )

    kb_id: UUID = Field(
        title="知识库id",
        description="知识库id",
        foreign_key="knowledge_base.id",
        index=True
    )

    doc_path: str = Field(
        title="文档对象存储地址",
        description="原始文件在对象存储（rustfs）中的 key，eg: knowledge/{kb_id}/{doc_id}/example.pdf"
    )

    name: str = Field(
        title="文档名称",
        description="文档名称",
        min_length=1,
        max_length=250,
    )

    description: str = Field(
        title="文档详情描述",
        description="文档详情描述，最大支持500个字符",
        min_length=0,
        max_length=500,
        default="",
    )

    mime_type: Union[str, None] = Field(
        default=None,
        title="文档MIME类型",
        description="文档MIME类型，eg: application/pdf"
    )

    file_size: Union[int, None] = Field(
        default=None,
        title="文档大小",
        description="原始文件大小（字节）"
    )

    checksum: Union[str, None] = Field(
        default=None,
        title="文档内容校验和",
        description="原始文件内容的 sha256 十六进制值，用于去重/秒传判断"
    )

    weight: int = Field(
        title="文档权重",
        description="文档权重，用于管理侧排序",
        default=0
    )

    status: KnowledgeStatus = Field(
        title="文档状态",
        description="文档状态: pending, processing, ready, enabled, disabled, deleting, failed",
        default=KnowledgeStatus.PENDING,
        index=True,
    )

    error_message: Union[str, None] = Field(
        default=None,
        title="处理失败原因",
        description="解析/分段/嵌入流水线失败时记录的错误信息，重试成功后置空"
    )

    seg_num: int = Field(
        title="文档分段数",
        description="文档分段数（冗余计数，由 repository 层维护）",
        default=0
    )

    kb: "KnowledgeBase" = Relationship(back_populates="documents")
    segments: List["DocumentSegment"] = Relationship(
        back_populates="doc",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

class DocumentSegment(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "knowledge_base_document_segment" # type: ignore
    __table_args__ = (
        UniqueConstraint("doc_id", "position", name="uq_segment_doc_position"),
    )

    id: UUID | None = Field(
        title="主键id",
        description="主键id；同时作为向量库对象UUID——写入 Weaviate 时显式传 ids=[segment.id]，保证行与向量一一对应",
        default_factory=uuid4,
        primary_key=True,
    )

    kb_id: UUID = Field(
        title="知识库id",
        description="知识库id（冗余自所属文档以省一次join，删除文档时需一并清理）",
        foreign_key="knowledge_base.id",
        index=True
    )

    doc_id: UUID = Field(
        title="文档id",
        description="文档id",
        foreign_key="knowledge_base_document.id",
        index=True
    )

    position: int = Field(
        title="段落位置",
        description="段落位置，用于标识段落在文档内的先后顺序，从0开始且文档内唯一",
        default=0
    )

    content: str = Field(
        title="段落内容",
        description="段落内容（TEXT 不限长度；分块大小由入库时的分块配置决定）"
    )

    meta: Union[dict, None] = Field(
        default=None,
        title="段落元数据",
        description="解析/分块产生的元数据（JSON）：page_start/page_end、heading_path 标题面包屑、"
        "块类型、资产 key 等；随向量写入 Weaviate 作为 metadata，供检索过滤与溯源展示",
        sa_column=Column(JSON),
    )

    word_count: int = Field(
        title="段落字数",
        description="段落内容字数（按字符计）",
        default=0
    )

    status: KnowledgeStatus = Field(
        title="段落状态",
        description="段落状态: pending, processing, ready, enabled, disabled, deleting, failed",
        default=KnowledgeStatus.PENDING,
        index=True,
    )

    doc: "KnowledgeDocument" = Relationship(back_populates="segments")
