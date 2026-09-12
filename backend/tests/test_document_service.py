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
from app.api.deps import UserPrincipal
from app.api.v1.endpoints.knowledge import get_knowledge_document_file
from app.application.knowledge_app_service import KnowledgeAppService
from app.adapters.filesystem import Filesystem
from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus
from app.adapters.persistence.knowledge_base_repository import KnowledgeBaseRepository
from app.adapters.persistence.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from app.domain.knowledge.document_service import KnowledgeDocumentService
from app.domain.knowledge.object_store import PRESIGN_TTL_SECONDS, KnowledgeObjectStore
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


# ---------- 上传白名单 ----------


@pytest.mark.parametrize("filename", ["报表.xlsx", "方案.DOCX", "doc.pdf"])
def test_create_document_accepts_allowlisted_suffixes(engine, service, filesystem, filename):
    """白名单内后缀（大小写不敏感）受理；解析路由由摄取期完成。"""
    kb_id = make_kb(engine)
    doc = service.create_document(
        kb_id, TEST_USER_ID, filename=filename, content_type="application/octet-stream", stream=io.BytesIO(PDF_BYTES)
    )
    assert filesystem.objects[doc.doc_path] == PDF_BYTES


def test_create_document_rejects_unrouted_suffix(engine, service, filesystem):
    kb_id = make_kb(engine)
    with pytest.raises(KnowledgeDocumentInvalidError, match="unsupported file type"):
        service.create_document(
            kb_id, TEST_USER_ID, filename="malware.exe", content_type=None, stream=io.BytesIO(PDF_BYTES)
        )
    assert not filesystem.objects


# ---------- 预签名下载决策（/file 端点 302/降级） ----------


class PresignFilesystem(MemoryFilesystem):
    """带签名能力的内存实现：记录签发入参，返回固定签名地址。"""

    def __init__(self):
        super().__init__()
        self.signed: list[tuple[str, int]] = []

    def presign_get(self, key: str, expires_in: int) -> str | None:
        self.signed.append((key, expires_in))
        return f"http://rustfs.test/signed/{key}"


@pytest.fixture()
def presign_filesystem():
    return PresignFilesystem()


@pytest.fixture()
def presign_service(engine, presign_filesystem):
    return KnowledgeDocumentService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        document_repo=KnowledgeDocumentRepository(engine=engine),
        vector_index=StubVectorIndex(),
        object_store=KnowledgeObjectStore(
            filesystem=presign_filesystem, logger_factory=LoggerFactory()
        ),
        logger_factory=LoggerFactory(),
    )


def test_resolve_document_file_presigns_document_key(engine, presign_service, presign_filesystem):
    kb_id = make_kb(engine)
    doc = presign_service.create_document(
        kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=io.BytesIO(PDF_BYTES)
    )

    got_doc, presigned = presign_service.resolve_document_file(kb_id, TEST_USER_ID, doc.id)

    assert got_doc.id == doc.id
    assert presigned == f"http://rustfs.test/signed/{doc.doc_path}"
    # 默认 TTL 即附件域同口径的 600s
    assert presign_filesystem.signed == [(doc.doc_path, PRESIGN_TTL_SECONDS)]


def test_resolve_document_file_without_signing_capability(engine, service):
    """本地磁盘后端（presign 恒 None）→ 决策载荷置空，调用方降级回源。"""
    kb_id = make_kb(engine)
    doc = service.create_document(
        kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=io.BytesIO(PDF_BYTES)
    )

    got_doc, presigned = service.resolve_document_file(kb_id, TEST_USER_ID, doc.id)
    assert got_doc.id == doc.id
    assert presigned is None


def test_resolve_document_file_not_visible_to_others(presign_service):
    """他人私有库不可见：404 不泄露存在性（与读路径同口径）。"""
    other_user = uuid4()
    with pytest.raises(KnowledgeNotFoundError):
        presign_service.resolve_document_file(uuid4(), other_user, uuid4())


# ---------- /file 端点分支：预签名 302 优先、本地盘降级流式回源 ----------


def _principal():
    return UserPrincipal(user_id=TEST_USER_ID, username="tester")


def _file_app_service(doc_service) -> KnowledgeAppService:
    """端点直测装配：真实文档服务撑门面（库/分段/派发面不参与）。"""
    return KnowledgeAppService(
        knowledge_base_service=None,
        knowledge_document_service=doc_service,
        segment_service=None,
        ingestion_service=None,
        dispatcher=None,
    )


def test_file_endpoint_302s_to_presigned_url(engine, presign_service, presign_filesystem):
    kb_id = make_kb(engine)
    doc = presign_service.create_document(
        kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=io.BytesIO(PDF_BYTES)
    )

    resp = get_knowledge_document_file(
        app_service=_file_app_service(presign_service), principal=_principal(), kb_id=kb_id, doc_id=doc.id,
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == f"http://rustfs.test/signed/{doc.doc_path}"
    # 禁缓存：签名短命，防过期地址被浏览器存住
    assert resp.headers["cache-control"] == "no-store"
    assert presign_filesystem.signed == [(doc.doc_path, PRESIGN_TTL_SECONDS)]


def test_file_endpoint_streams_when_presign_unavailable(engine, service):
    """本地磁盘后端：字节流直出（inline），mime 缺失按后缀兜底。"""
    kb_id = make_kb(engine)
    doc = service.create_document(
        kb_id, TEST_USER_ID, filename="a.pdf", content_type="application/pdf", stream=io.BytesIO(PDF_BYTES)
    )

    resp = get_knowledge_document_file(
        app_service=_file_app_service(service), principal=_principal(), kb_id=kb_id, doc_id=doc.id,
    )

    assert resp.body == PDF_BYTES
    assert resp.media_type == "application/pdf"
    assert "inline" in resp.headers["content-disposition"]
