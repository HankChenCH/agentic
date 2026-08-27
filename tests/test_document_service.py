"""文档服务：原始文件读回（预览端点依赖）与存在性校验（4001/4004）。"""

from typing import BinaryIO, Iterator
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.core.exceptions import InfrastructureError
from app.core.logging import LoggerFactory
from app.exceptions import (
    KnowledgeDocumentNotFoundError,
    KnowledgeNotFoundError,
)
from app.infrastructures.filesystem import Filesystem
from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from app.services.domain.knowledge.document_service import KnowledgeDocumentService
from app.services.domain.knowledge.object_store import KnowledgeObjectStore

PDF_BYTES = b"%PDF-1.4 fake pdf body"


class MemoryFilesystem(Filesystem):
    """内存版 Filesystem 契约实现：读写删均在 dict 上进行。"""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def read(self, key: str) -> bytes:
        return self.objects[key]

    def put(self, key: str, data: bytes | BinaryIO) -> None:
        self.objects[key] = data if isinstance(data, bytes) else data.read()

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def exists(self, key: str) -> bool:
        return key in self.objects

    def list(self, prefix: str = "") -> Iterator[str]:
        return iter(sorted(k for k in self.objects if k.startswith(prefix)))


class StubVectorIndex:
    """向量索引桩：文件读回路径不触向量索引。"""

    def delete_segments(self, kb_id, segment_ids):  # noqa: ARG002
        raise NotImplementedError("vector index is out of scope for these tests")


def make_kb(engine) -> uuid4:
    with Session(engine) as session:
        kb = KnowledgeBase(
            name=f"kb-{uuid4()}",
            embedding_model="ollama-embedding",
            status=KnowledgeStatus.ENABLED,
        )
        session.add(kb)
        session.commit()
        session.refresh(kb)
        return kb.id


@pytest.fixture()
def filesystem():
    return MemoryFilesystem()


@pytest.fixture()
def service(engine, filesystem):
    return KnowledgeDocumentService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        document_repo=KnowledgeDocumentRepository(engine=engine),
        vector_index=StubVectorIndex(),
        object_store=KnowledgeObjectStore(
            filesystem=filesystem, logger_factory=LoggerFactory()
        ),
        logger_factory=LoggerFactory(),
    )


def test_read_document_file_roundtrip(engine, service, filesystem):
    kb_id = make_kb(engine)
    doc = service.create_document(
        kb_id, filename="年报.pdf", content_type="application/pdf", data=PDF_BYTES
    )
    # 上传即落对象存储：key 与字节与读回一致（中文文件名路径可用）
    assert filesystem.objects[doc.doc_path] == PDF_BYTES

    got_doc, data = service.read_document_file(kb_id, doc.id)
    assert got_doc.id == doc.id
    assert data == PDF_BYTES


def test_read_document_file_unknown_kb(service):
    with pytest.raises(KnowledgeNotFoundError):
        service.read_document_file(uuid4(), uuid4())


def test_read_document_file_unknown_doc(engine, service):
    kb_id = make_kb(engine)
    with pytest.raises(KnowledgeDocumentNotFoundError):
        service.read_document_file(kb_id, uuid4())


def test_read_document_file_missing_object_wrapped(engine, service, filesystem):
    kb_id = make_kb(engine)
    doc = service.create_document(
        kb_id, filename="a.pdf", content_type="application/pdf", data=PDF_BYTES
    )
    # 模拟删除流程已清理对象（行还在、对象没了）
    filesystem.delete(doc.doc_path)
    with pytest.raises(InfrastructureError):
        service.read_document_file(kb_id, doc.id)
