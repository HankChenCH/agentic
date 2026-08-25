"""agent ↔ 知识库绑定管理：列表与全量替换。

校验在本层抛业务异常：agent 未注册（4007）、知识库不存在（4001/404）。
绑定允许任意非 deleting 状态的库——检索时再按 enabled 收敛（先绑库、
后等文档处理完再启用的管理路径可用）。
"""

from dataclasses import dataclass
from typing import List
from uuid import UUID

from wireup import injectable

from app.agents.base import AGENT_REGISTRY
from app.core.logging import LoggerFactory
from app.exceptions import KnowledgeAgentInvalidError, KnowledgeStatusError
from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_binding_repository import KnowledgeBindingRepository
from app.services.knowledge.support import require_kb


@injectable
@dataclass
class KnowledgeBindingService:
    kb_repo: KnowledgeBaseRepository
    binding_repo: KnowledgeBindingRepository
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def list_bindings(self, agent_id: str) -> List[KnowledgeBase]:
        self._require_agent(agent_id)
        bindings = self.binding_repo.list_by_agent(agent_id)
        kbs = (self.kb_repo.get_kb(binding.kb_id) for binding in bindings)
        return [kb for kb in kbs if kb is not None and kb.status != KnowledgeStatus.DELETING]

    def replace_bindings(self, agent_id: str, kb_ids: List[UUID]) -> List[KnowledgeBase]:
        """全量替换该 agent 的绑定集合（空列表 = 清空绑定），返回绑定成功的 KB 列表。"""
        self._require_agent(agent_id)
        # 去重保序：入参重复 id 不产生重复绑定行
        unique_ids = list(dict.fromkeys(kb_ids))
        kbs: List[KnowledgeBase] = []
        for kb_id in unique_ids:
            kb = require_kb(self.kb_repo, kb_id)
            if kb.status == KnowledgeStatus.DELETING:
                raise KnowledgeStatusError(f"cannot bind a knowledge base being deleted: {kb_id}")
            kbs.append(kb)
        self.binding_repo.replace_by_agent(agent_id, unique_ids)
        return kbs

    @staticmethod
    def _require_agent(agent_id: str) -> None:
        # 按注册表现值校验，防止手滑绑出悬空 agent；会话侧未注册 id 回退默认
        # agent 是 AgentFactory 既有语义，与绑定校验无关
        if agent_id not in AGENT_REGISTRY:
            raise KnowledgeAgentInvalidError(
                f"agent not registered: {agent_id}, registered: {sorted(AGENT_REGISTRY)}"
            )
