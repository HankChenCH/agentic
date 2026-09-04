"""用户域端口（依赖倒置）：记忆「用户」节点的账号信息同步协议。

注册即预建记忆「用户」节点、轮次收尾自愈刷新账号信息，但领域层不得
import components——与 ``memory/ports.py`` 同款解法：协议住本层，实现
住在 ``app/components/memory/ability/user_node.py``（``UserNodeSyncService``），
经 wireup ``@injectable(as_type=...)`` 按本协议类型注入。依赖箭头
components ──► domain 合法。
"""

from typing import Protocol
from uuid import UUID

from app.models.domain.memory import MemoryEntity
from app.models.domain.user import User


class UserNodeSyncPort(Protocol):
    """把账号 profile（username/nickname）同步进该用户的记忆「用户」节点。"""

    def sync_user_node(self, user_id: UUID, username: str, nickname: str) -> MemoryEntity: ...


class UserRepositoryPort(Protocol):
    """用户聚合数据访问协议（实现住 ``app/adapters/persistence/``）。"""

    def get_by_id(self, user_id: UUID) -> User | None: ...

    def get_by_username(self, username: str) -> User | None: ...

    def create(self, username: str, password_hash: str, nickname: str) -> User: ...

    def update_profile(self, user_id: UUID, nickname: str) -> User | None: ...

    def update_password(self, user_id: UUID, password_hash: str) -> User | None: ...
