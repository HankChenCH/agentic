"""用户域端口（依赖倒置）：记忆「用户」节点的账号信息同步协议。

注册即预建记忆「用户」节点、轮次收尾自愈刷新账号信息，但领域层不得
import components——与 ``memory/ports.py`` 同款解法：协议住本层，实现
住在 ``app/components/memory/ability/user_node.py``（``UserNodeSyncService``），
经 wireup ``@injectable(as_type=...)`` 按本协议类型注入。依赖箭头
components ──► domain 合法。
"""

from typing import Protocol
from datetime import datetime
from uuid import UUID

from app.models.domain.memory import MemoryEntity
from app.models.domain.user import User, RefreshToken


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


class RefreshTokenStorePort(Protocol):
    """refresh token 登记表访问协议（实现住 ``app/adapters/persistence/``）。

    复用检测/旋转的状态机操作面：所有时间量都以绑定参数交给 SQL 侧
    比较（SQLite 读回 datetime 丢 tzinfo，Python 侧跨 aware/naive 比较
    不可靠），领域层不拿 datetime 做运算。
    """

    def get(self, jti: str) -> RefreshToken | None: ...

    def create(
        self, *, jti: str, user_id: UUID, family_id: str, expires_at: datetime
    ) -> RefreshToken: ...

    def claim_active(self, jti: str, *, rotated_at: datetime) -> bool:
        """条件认领旋转：active→rotated，rowcount==1 即抢占成功。"""
        ...

    def revoke_on_reuse(self, jti: str, family_id: str, *, stale_before: datetime) -> bool:
        """复用裁决：该票已 rotated 且旋转时刻早于 stale_before（宽限期外
        = 窃取警报）才吊销全族，返回是否触发连坐。"""
        ...

    def revoke_family(self, family_id: str) -> int:
        """登出语义：吊销族内全部未吊销行。"""
        ...

    def revoke_all_for_user(self, user_id: UUID) -> int: ...

    def purge_expired(self, *, now: datetime) -> int:
        """清理过期行（全局，expires_at 索引），返回删除行数。"""
        ...
