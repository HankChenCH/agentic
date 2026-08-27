"""知识库聚合管理：CRUD、启停与三段式删除。

校验（存在性/重名/状态机）全部在本层抛业务异常——HTTP 与 Celery 两个
运行时经由服务得到一致错误语义，端点只做 HTTP wiring。
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from wireup import injectable

from .vector_index import KnowledgeVectorIndex
from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.exceptions import KnowledgeNameDuplicatedError, KnowledgeStatusError
from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus
from app.models.schema.request.knowledge import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseUpdateRequest,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_binding_repository import KnowledgeBindingRepository
from .object_store import KnowledgeObjectStore
from .support import (
    check_status_transition,
    page_envelope,
    require_kb,
)


@injectable
@dataclass
class KnowledgeBaseService:
    kb_repo: KnowledgeBaseRepository
    binding_repo: KnowledgeBindingRepository
    vector_index: KnowledgeVectorIndex
    object_store: KnowledgeObjectStore
    app_config: AppConfig
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def create_knowledge(self, request: KnowledgeBaseCreateRequest) -> KnowledgeBase:
        if self.kb_repo.get_kb_by_name(request.name) is not None:
            raise KnowledgeNameDuplicatedError(f"knowledge base name already exists: {request.name}")
        kb = KnowledgeBase(
            name=request.name,
            description=request.description,
            weight=request.weight,
            # 建库即固化嵌入模型标识：后续向量化以该模型为准，换模型需全量重嵌
            embedding_model=self.app_config.vector_db.embedding,
            status=KnowledgeStatus.PENDING,
        )
        try:
            return self.kb_repo.create_kb(kb)
        except IntegrityError:
            # 重名预检与写入之间存在并发窗口，唯一索引是最终兜底
            raise KnowledgeNameDuplicatedError(
                f"knowledge base name already exists: {request.name}"
            ) from None

    def update_knowledge(self, kb_id: UUID, request: KnowledgeBaseUpdateRequest) -> KnowledgeBase:
        kb = require_kb(self.kb_repo, kb_id)
        if kb.status == KnowledgeStatus.DELETING:
            raise KnowledgeStatusError("cannot update a knowledge base being deleted")
        if request.name is not None and request.name != kb.name:
            if self.kb_repo.get_kb_by_name(request.name) is not None:
                raise KnowledgeNameDuplicatedError(f"knowledge base name already exists: {request.name}")
            kb.name = request.name
        if request.description is not None:
            kb.description = request.description
        if request.weight is not None:
            kb.weight = request.weight
        return self.kb_repo.update_kb(kb)

    def describe_knowledge(self, kb_id: UUID) -> KnowledgeBase:
        return require_kb(self.kb_repo, kb_id)

    def list_knowledge(self, page: int, page_size: int) -> dict:
        items, total = self.kb_repo.list_kbs(page, page_size)
        return page_envelope(items, total, page, page_size)

    def delete_knowledge(self, kb_id: UUID) -> KnowledgeBase:
        """三段式删除：标记 deleting（独立提交）→ 清理对象存储 → 级联删除数据行。

        标记先行落库：清理/删行中途失败时资源停留在 deleting（列表已不可见、
        其余操作已被拒绝），重试 DELETE 会跳过标记继续清理删除——幂等可重入。
        后续把后两段迁移到后台任务时，此状态位即任务的工作标记。
        """
        kb = require_kb(self.kb_repo, kb_id)
        if kb.status != KnowledgeStatus.DELETING:
            kb.status = KnowledgeStatus.DELETING
            kb = self.kb_repo.update_kb(kb)
        # 整库向量直接 drop collection（替代全量分段 id 逐批删除，快且不留空壳）；
        # 绑定行同步清理，避免悬空引用
        self.vector_index.drop_collection(kb_id)
        self.binding_repo.delete_by_kb(kb_id)
        self.object_store.delete_prefix_best_effort(self.object_store.kb_prefix(kb_id))
        self.kb_repo.delete_kb(kb_id)
        return kb

    def set_knowledge_enabled(self, kb_id: UUID, enabled: bool) -> KnowledgeBase:
        kb = require_kb(self.kb_repo, kb_id)
        check_status_transition(kb.status, enabled, KnowledgeStatusError)
        target = KnowledgeStatus.ENABLED if enabled else KnowledgeStatus.DISABLED
        if kb.status != target:
            kb.status = target
            kb = self.kb_repo.update_kb(kb)
        return kb
