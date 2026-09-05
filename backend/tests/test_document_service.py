"""文档服务：原始文件读回（预览端点依赖）、存在性校验（4001/4004）与流式上传。"""

import hashlib
import io
from typing import BinaryIO, Iterator
from uuid import uuid4

import pytest
from obstore.store import MemoryStore
from sqlmodel import Session

import app.domain.knowledge.document_service as document_service_module
from app.core.exceptions import InfrastructureError
from app.core.logging import LoggerFactory
from app.exceptions import (
    KnowledgeDocumentInvalidError,
    KnowledgeDocumentNotFoundError,
    KnowledgeNotFoundError,
)
from app.adapters.filesystem import Filesystem
from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus
from app.adapters.persistence.knowledge_base_repository import KnowledgeBaseRepository
from app.adapters.persistence.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from app.domain.knowledge.document_service import KnowledgeDocumentService
from app.domain.knowledge.object_store import KnowledgeObjectStore
from tests.conftest import TEST_USER_ID

PDF_BYTES = b"%PDF-1.4 fake pdf body"


class UnseekableStream(io.BytesIO):
    """不可 seek 的流：逼出转存途中的计数/摘要路径（探长返回 None）。"""

    def seekable(self) -> bool:
        return False

    def seek(self, *args):
        raise OSError("unseekable stream")


class MemoryFilesystem(Filesystem):
    """内存版 Filesystem 契约实现：读写删均在 dict 上进行。

    put 对流式输入按块循环读取——真实实现（copyfileobj / obstore）都循
    环拉取，读穿透包装的 read() 单次只回一块，无参 read 不能只调一次。
    """

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def read(self, key: str) -> bytes:
        return self.objects[key]

    def put(self, key: str, data: bytes | BinaryIO) -> None:
        if isinstance(data, bytes):
            self.objects[key] = data
            return
        chunks = []
        while chunk := data.read(64 * 1024):
            chunks.append(chunk)
        self.objects[key] = b"".join(chunks)

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
            user_id=TEST_USER_ID,
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
        kb_id, TEST_USER_ID, filename="年报.pdf", content_type="application/pdf", stream=io.BytesIO(PDF_BYTES)
    )
    # 上传即落对象存储：key 与字节与读回一致（中文文件名路径可用）；
    # 流式路径产出的大小/摘要与整读一致
    assert filesystem.objects[doc.doc_path] == PDF_BYTES
    assert doc.file_size == len(PDF_BYTES)
    assert doc.checksum == hashlib.sha256(PDF_BYTES).hexdigest()

    got_doc, data = service.read_document_file(kb_id, TEST_USER_ID, doc.id)
    assert got_doc.id == doc.id
    assert data == PDF_BYTES


def test_read_document_file_unknown_kb(service):
    with pytest.raises(KnowledgeNotFoundError):
        service.read_document_file(uuid4(), TEST_USER_ID, uuid4())


def test_read_document_file_unknown_doc(engine, service):
    kb_id = make_kb(engine)
    with pytest.raises(KnowledgeDocumentNotFoundError):
        service.read_document_file(kb_id, TEST_USER_ID, uuid4())


def test_read_document_file_missing_object_wrapped(engine, service, filesystem):
    kb_id = make_kb(engine)
    doc = service.create_document(
        kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=io.BytesIO(PDF_BYTES)
    )
    # 模拟删除流程已清理对象（行还在、对象没了）
    filesystem.delete(doc.doc_path)
    with pytest.raises(InfrastructureError):
        service.read_document_file(kb_id, TEST_USER_ID, doc.id)


# ---------- 流式上传 ----------


def test_create_document_unseekable_stream_roundtrip(engine, service, filesystem):
    """不可 seek 的流走纯转存路径：无预检探长，大小/摘要全靠读穿透包装累计。"""
    kb_id = make_kb(engine)
    doc = service.create_document(
        kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=UnseekableStream(PDF_BYTES)
    )
    assert filesystem.objects[doc.doc_path] == PDF_BYTES
    assert doc.file_size == len(PDF_BYTES)
    assert doc.checksum == hashlib.sha256(PDF_BYTES).hexdigest()


def test_create_document_rejects_empty_stream(engine, service, filesystem):
    kb_id = make_kb(engine)
    with pytest.raises(KnowledgeDocumentInvalidError, match="empty"):
        service.create_document(
            kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=io.BytesIO(b"")
        )
    assert not filesystem.objects


def test_create_document_rejects_oversize_by_probe(engine, service, filesystem, monkeypatch):
    """可 seek 的流超限在预检即拒绝：不触发任何对象写入。"""
    monkeypatch.setattr(document_service_module, "MAX_UPLOAD_BYTES", 8)
    kb_id = make_kb(engine)
    with pytest.raises(KnowledgeDocumentInvalidError, match="exceeds limit"):
        service.create_document(
            kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=io.BytesIO(b"x" * 9)
        )
    assert not filesystem.objects


def test_create_document_rejects_oversize_midcopy(engine, service, filesystem, monkeypatch):
    """不可 seek 的流超限在转存途中中断：残缺对象被清理。"""
    monkeypatch.setattr(document_service_module, "MAX_UPLOAD_BYTES", 8)
    kb_id = make_kb(engine)
    with pytest.raises(KnowledgeDocumentInvalidError, match="exceeds limit"):
        service.create_document(
            kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=UnseekableStream(b"x" * 9)
        )
    assert not filesystem.objects


def test_create_document_oversize_unseekable_empty_stream(engine, service, filesystem):
    """不可 seek 的空流：转存后判空拒绝，空对象被清理。"""
    kb_id = make_kb(engine)
    with pytest.raises(KnowledgeDocumentInvalidError, match="empty"):
        service.create_document(
            kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=UnseekableStream(b"")
        )
    assert not filesystem.objects


@pytest.mark.parametrize("label, payload", [("small", PDF_BYTES), ("large-multipart", b"\xde\xad\xbe\xef" * (2 * 1024 * 1024))])
def test_counting_reader_accepted_by_obstore_put(label, payload):
    """回归：obstore ``put`` 的输入校验要求 file-like 具备 seek/tell（探测
    流长并决定是否 multipart），只实现 read 会被拒——正是上传报
    "Unexpected input for PutInput" 的根因。MemoryStore 纯内存不触网，
    直接走真实 obstore 路径验证：字节一致且探测性 seek 不污染计数/摘要。"""
    store = MemoryStore()
    reader = document_service_module._CountingDigestReader(
        io.BytesIO(payload), document_service_module.MAX_UPLOAD_BYTES
    )
    store.put(f"{label}.pdf", reader)
    assert bytes(store.get(f"{label}.pdf").bytes()) == payload
    assert reader.size == len(payload)
    assert reader.hexdigest() == hashlib.sha256(payload).hexdigest()
