"""知识库管理端点：只做 HTTP wiring，业务规则全部在服务层。

存在性/状态等校验由服务抛业务异常（含 4001/4004 区分），全局异常
处理器（AOP）映射为信封响应——端点不再做 None 判断，后台任务与
HTTP 拿到完全一致错误语义。
"""

import logging
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response as RawResponse
from wireup import Injected

from app.models.schema.request.knowledge import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseUpdateRequest,
    KnowledgeDocumentUpdateRequest,
)
from app.models.schema.request.pagination import PaginationRequest
from app.models.schema.response.biz_response import Response
from app.services import (
    DocumentIngestionService,
    KnowledgeBaseService,
    KnowledgeDocumentService,
)
from app.tasks.knowledge import process_document

# 顶级领域前缀（API 根为 /，各领域独立挂载）：知识库挂 /knowledge。
# 前端 REST_BASE 指向根（http://host/），service 侧相对路径 /knowledge... 直接命中。
router = APIRouter(prefix="/knowledge", tags=["Knowledge"])

# 模块级非 DI 代码走 stdlib logger（app 域由 InterceptHandler 桥接进统一 sinks）
logger = logging.getLogger(__name__)


@router.post("")
def create_knowledge_base(
    knowledge_base_service: Injected[KnowledgeBaseService],
    request: KnowledgeBaseCreateRequest,
):
    return Response.success(knowledge_base_service.create_knowledge(request)).to_dict()


@router.get("")
def list_knowledge_base(
    knowledge_base_service: Injected[KnowledgeBaseService],
    pagination: Annotated[PaginationRequest, Depends()],
):
    return Response.success(
        knowledge_base_service.list_knowledge(pagination.page, pagination.pageSize)
    ).to_dict()


@router.get("/{kb_id}")
def get_knowledge_base(
    knowledge_base_service: Injected[KnowledgeBaseService],
    kb_id: UUID,
):
    return Response.success(knowledge_base_service.describe_knowledge(kb_id)).to_dict()


@router.patch("/{kb_id}")
def update_knowledge_base(
    knowledge_base_service: Injected[KnowledgeBaseService],
    kb_id: UUID,
    request: KnowledgeBaseUpdateRequest,
):
    return Response.success(knowledge_base_service.update_knowledge(kb_id, request)).to_dict()


@router.delete("/{kb_id}")
def delete_knowledge_base(
    knowledge_base_service: Injected[KnowledgeBaseService],
    kb_id: UUID,
):
    # 返回标记为 deleting 的实体快照，调用方可确认删除已被受理
    return Response.success(knowledge_base_service.delete_knowledge(kb_id)).to_dict()


@router.post("/{kb_id}/enable")
def enable_knowledge_base(
    knowledge_base_service: Injected[KnowledgeBaseService],
    kb_id: UUID,
):
    return Response.success(knowledge_base_service.set_knowledge_enabled(kb_id, enabled=True)).to_dict()


@router.post("/{kb_id}/disable")
def disable_knowledge_base(
    knowledge_base_service: Injected[KnowledgeBaseService],
    kb_id: UUID,
):
    return Response.success(knowledge_base_service.set_knowledge_enabled(kb_id, enabled=False)).to_dict()


@router.post("/{kb_id}/document")
def create_knowledge_document(
    knowledge_document_service: Injected[KnowledgeDocumentService],
    kb_id: UUID,
    file: UploadFile = File(..., description="文档文件（当前仅支持 PDF）"),
    name: str | None = Form(default=None, min_length=1, max_length=250, description="文档名称，缺省用上传文件名"),
    description: str = Form(default="", max_length=500, description="文档描述"),
):
    # sync 端点在线程池执行：直接读 UploadFile 底层的 SpooledTemporaryFile，避免 async 切换
    doc = knowledge_document_service.create_document(
        kb_id,
        filename=file.filename or "",
        content_type=file.content_type,
        data=file.file.read(),
        name=name,
        description=description,
    )
    _dispatch_processing(kb_id, doc.id)
    return Response.success(doc).to_dict()


@router.post("/{kb_id}/document/{doc_id}/retry")
def retry_knowledge_document(
    ingestion_service: Injected[DocumentIngestionService],
    kb_id: UUID,
    doc_id: UUID,
):
    """补发处理任务：failed/pending 允许（如 worker 掉线导致的任务丢失）。"""
    doc = ingestion_service.retry_document(kb_id, doc_id)
    _dispatch_processing(kb_id, doc_id)
    # 返回受理快照：实际处理状态由后台任务推进
    return Response.success(doc).to_dict()


@router.get("/{kb_id}/document")
def list_knowledge_documents(
    knowledge_document_service: Injected[KnowledgeDocumentService],
    kb_id: UUID,
    pagination: Annotated[PaginationRequest, Depends()],
):
    return Response.success(
        knowledge_document_service.list_documents(kb_id, pagination.page, pagination.pageSize)
    ).to_dict()


@router.get("/{kb_id}/document/{doc_id}")
def get_knowledge_document(
    knowledge_document_service: Injected[KnowledgeDocumentService],
    kb_id: UUID,
    doc_id: UUID,
):
    return Response.success(knowledge_document_service.describe_document(kb_id, doc_id)).to_dict()


@router.get("/{kb_id}/document/{doc_id}/file")
def get_knowledge_document_file(
    knowledge_document_service: Injected[KnowledgeDocumentService],
    kb_id: UUID,
    doc_id: UUID,
):
    """原始文件字节流（inline，前端 PDF 预览用）——二进制响应不走信封。"""
    doc, data = knowledge_document_service.read_document_file(kb_id, doc_id)
    # RFC 5987：中文名走 filename*=UTF-8''，兜底补 .pdf 后缀
    filename = quote(doc.name if doc.name.lower().endswith(".pdf") else f"{doc.name}.pdf")
    return RawResponse(
        content=data,
        media_type=doc.mime_type or "application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{filename}"},
    )


@router.patch("/{kb_id}/document/{doc_id}")
def update_knowledge_document(
    knowledge_document_service: Injected[KnowledgeDocumentService],
    kb_id: UUID,
    doc_id: UUID,
    request: KnowledgeDocumentUpdateRequest,
):
    return Response.success(
        knowledge_document_service.update_document(kb_id, doc_id, request)
    ).to_dict()


@router.delete("/{kb_id}/document/{doc_id}")
def delete_knowledge_document(
    knowledge_document_service: Injected[KnowledgeDocumentService],
    kb_id: UUID,
    doc_id: UUID,
):
    # 返回标记为 deleting 的实体快照，调用方可确认删除已被受理
    return Response.success(knowledge_document_service.delete_document(kb_id, doc_id)).to_dict()


@router.post("/{kb_id}/document/{doc_id}/enable")
def enable_knowledge_document(
    knowledge_document_service: Injected[KnowledgeDocumentService],
    kb_id: UUID,
    doc_id: UUID,
):
    return Response.success(
        knowledge_document_service.set_document_enabled(kb_id, doc_id, enabled=True)
    ).to_dict()


@router.post("/{kb_id}/document/{doc_id}/disable")
def disable_knowledge_document(
    knowledge_document_service: Injected[KnowledgeDocumentService],
    kb_id: UUID,
    doc_id: UUID,
):
    return Response.success(
        knowledge_document_service.set_document_enabled(kb_id, doc_id, enabled=False)
    ).to_dict()


def _dispatch_processing(kb_id: UUID, doc_id: UUID) -> None:
    # 分发留在端点层：服务若 import tasks 会形成 services→tasks→services 循环
    # 分发失败不回滚上传（文档停留 pending，可用 retry 端点补发），只记录告警
    try:
        process_document.delay(str(kb_id), str(doc_id))
    except Exception:
        logger.exception("failed to dispatch process_document task for doc %s", doc_id)
