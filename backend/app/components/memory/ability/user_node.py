"""账号信息 → 记忆「用户」节点联动。

注册时预建 is_user=True 节点、收尾轮次自愈刷新都走本服务：节点名恒为
规范名「用户」（consolidation.USER_ENTITY_NAME，抽取提示词约定不变），
账号的 username/nickname 只写进 attributes，不参与消歧与合并语义。
"""

from dataclasses import dataclass
from uuid import UUID

from wireup import injectable

from app.components.memory.repositories import MemoryRepository
from app.models.domain.memory import EntityType, MemoryEntity, MemoryOrigin
from app.domain.user.ports import UserNodeSyncPort

from .consolidation import USER_ENTITY_NAME

# 用户节点 attributes 中账号信息的键
ATTR_USERNAME = "username"
ATTR_NICKNAME = "nickname"


def _profile_attrs(username: str, nickname: str) -> dict:
    """账号 → attributes：nickname 为空回落 username（同 public_user 展示口径）。"""
    return {ATTR_USERNAME: username, ATTR_NICKNAME: nickname or username}


@injectable(as_type=UserNodeSyncPort)
@dataclass
class UserNodeSyncService:
    """把账号 profile 同步进当前用户的记忆「用户」节点（幂等，无变化不写）。"""

    memory_repo: MemoryRepository

    def sync_user_node(self, user_id: UUID, username: str, nickname: str) -> MemoryEntity:
        """在 user_id 作用域内查找/创建「用户」节点并回填账号 attributes。"""
        repo = self.memory_repo.for_user(user_id)
        node = repo.find_entity_by_name(USER_ENTITY_NAME)
        attrs = _profile_attrs(username, nickname)
        if node is None:
            return repo.upsert_entity(MemoryEntity(
                entity_type=EntityType.PERSON.value,
                name=USER_ENTITY_NAME,
                is_user=True,
                importance=0.9,
                origin=MemoryOrigin.EXTRACTED.value,
                attributes=attrs,
            ))
        if (node.attributes or {}).get(ATTR_USERNAME) != attrs[ATTR_USERNAME] or \
                (node.attributes or {}).get(ATTR_NICKNAME) != attrs[ATTR_NICKNAME]:
            node.attributes = {**(node.attributes or {}), **attrs}
            node = repo.upsert_entity(node)
        return node
