"""知识库聚合管理：CRUD、启停与三段式删除。

校验（存在性/归属/重名/状态机）全部在本层抛业务异常——HTTP 与 Celery
两个运行时经由服务得到一致错误语义，端点只做 HTTP wiring。

归属模型：知识库属主由端点层的 UserPrincipal 注入——读路径（详情/列表）
属主或公开库可见，写路径（更新/删除/启停）仅属主可操作；他人资源一律
404 不泄露存在性（与会话归属同一口径）。
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
    require_owned_kb,
    require_visible_kb,
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

    def create_knowledge(self, request: KnowledgeBaseCreateRequest, user_id: UUID) -> KnowledgeBase:
        if self.kb_repo.get_kb_by_name(request.name, user_id) is not None:
            raise KnowledgeNameDuplicatedError(f"knowledge base name already exists: {request.name}")
        kb = KnowledgeBase(
            user_id=user_id,
            name=request.name,
            description=request.description,
            is_public=request.isPublic,
            weight=request.weight,
            # 建库即固化嵌入模型标识：后续向量化以该模型为准，换模型需全量重嵌
            embedding_model=self.app_config.vector_db.embedding,
            status=KnowledgeStatus.PENDING,
        )
        try:
            return self.kb_repo.create_kb(kb)
        except IntegrityError:
            # 重名预检与写入之间存在并发窗口，每用户唯一索引是最终兜底
            raise KnowledgeNameDuplicatedError(
                f"knowledge base name already exists: {request.name}"
            ) from None

    def update_knowledge(
        self, kb_id: UUID, request: KnowledgeBaseUpdateRequest, user_id: UUID
    ) -> KnowledgeBase:
        kb = require_owned_kb(self.kb_repo, kb_id, user_id)
        if kb.status == KnowledgeStatus.DELETING:
            raise KnowledgeStatusError("cannot update a knowledge base being deleted")
        if request.name is not None and request.name != kb.name:
            if self.kb_repo.get_kb_by_name(request.name, kb.user_id) is not None:
                raise KnowledgeNameDuplicatedError(f"knowledge base name already exists: {request.name}")
            kb.name = request.name
        if request.description is not None:
            kb.description = request.description
        if request.isPublic is not None:
            kb.is_public = request.isPublic
        if request.weight is not None:
            kb.weight = request.weight
        return self.kb_repo.update_kb(kb)

    def describe_knowledge(self, kb_id: UUID, user_id: UUID) -> KnowledgeBase:
        return require_visible_kb(self.kb_repo, kb_id, user_id)

    def list_knowledge(self, page: int, page_size: int, user_id: UUID) -> dict:
        items, total = self.kb_repo.list_kbs(page, page_size, user_id)
        return page_envelope(items, total, page, page_size)

    def delete_knowledge(self, kb_id: UUID, user_id: UUID) -> KnowledgeBase:
        """三段式删除：标记 deleting（独立提交）→ 清理对象存储 → 级联删除数据行。

        标记先行落库：清理/删行中途失败时资源停留在 deleting（列表已不可见、
        其余操作已被拒绝），重试 DELETE 会跳过标记继续清理删除——幂等可重入。
        后续把后两段迁移到后台任务时，此状态位即任务的工作标记。
        """
        kb = require_owned_kb(self.kb_repo, kb_id, user_id)
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

    def set_knowledge_enabled(self, kb_id: UUID, enabled: bool, user_id: UUID) -> KnowledgeBase:
        kb = require_owned_kb(self.kb_repo, kb_id, user_id)
        check_status_transition(kb.status, enabled, KnowledgeStatusError)
        target = KnowledgeStatus.ENABLED if enabled else KnowledgeStatus.DISABLED
        if kb.status != target:
            kb.status = target
            kb = self.kb_repo.update_kb(kb)
        return kb
