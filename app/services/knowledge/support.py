"""知识库域共享纯函数：状态机、上传策略与存在性校验。

无 DI、无状态，仓库以参数传入（先例：services/document_chunker.py）。
存在性校验（require_kb/require_document）归服务层公共逻辑——HTTP 与
Celery 两个运行时经由服务方法操作文档，端点不再各自挡 404。
"""

import posixpath
from uuid import UUID

from app.core.exceptions import BusinessError
from app.exceptions import KnowledgeDocumentNotFoundError, KnowledgeNotFoundError
from app.models.domain.knowledge import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeStatus,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository

# 上传文件大小上限：当前实现全量读入内存计算 sha256，先挡住超大文件
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# 上传格式白名单：解析流水线当前仅接 PDF（MinerU 主路径）
ALLOWED_UPLOAD_SUFFIXES = frozenset({".pdf"})

# 落库的失败原因截断长度
ERROR_MESSAGE_MAX = 2000

# 状态机：启用仅允许 ready/disabled（enabled 为幂等重放），禁用仅允许 enabled（disabled 为幂等重放）；
# pending/processing/failed 必须先完成/重试处理流程，不允许直接启用或禁用。
# deleting 为删除流程独占的终态入口：仅允许 DELETE 幂等重入继续清理，其余操作一律拒绝
ENABLE_ALLOWED = {KnowledgeStatus.READY, KnowledgeStatus.DISABLED, KnowledgeStatus.ENABLED}
DISABLE_ALLOWED = {KnowledgeStatus.ENABLED, KnowledgeStatus.DISABLED}

# 重试处理：failed 可重试；pending（如 worker 未起、任务丢失）可补发
RETRY_ALLOWED = {KnowledgeStatus.FAILED, KnowledgeStatus.PENDING}


def check_status_transition(status: KnowledgeStatus, enabled: bool, error: type[BusinessError]) -> None:
    allowed = ENABLE_ALLOWED if enabled else DISABLE_ALLOWED
    if status not in allowed:
        action = "enable" if enabled else "disable"
        raise error(
            f"cannot {action} in status '{status.value}': allowed statuses are "
            f"{', '.join(sorted(s.value for s in allowed))}"
        )


def sanitize_filename(filename: str, doc_id: UUID) -> str:
    # 上传名可能携带路径成分（恶意注入或 Windows 反斜杠）：归一后取 basename，
    # 空串/./.. 兜底用 doc_id，保证对象 key 永远是合法的相对路径
    base = posixpath.basename((filename or "").replace("\\", "/")).strip()
    if base in ("", ".", ".."):
        return str(doc_id)
    return base


def page_envelope(items: list, total: int, page: int, page_size: int) -> dict:
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def require_kb(kb_repo: KnowledgeBaseRepository, kb_id: UUID) -> KnowledgeBase:
    """父资源存在性锚定：文档是知识库的子资源，404 语义才能区分 4001/4004。"""
    kb = kb_repo.get_kb(kb_id)
    if kb is None:
        raise KnowledgeNotFoundError("knowledge base not found")
    return kb


def require_document(
    document_repo: KnowledgeDocumentRepository, kb_id: UUID, doc_id: UUID
) -> KnowledgeDocument:
    doc = document_repo.get_document(kb_id, doc_id)
    if doc is None:
        raise KnowledgeDocumentNotFoundError(f"knowledge document not found: {doc_id}")
    return doc
