"""refresh token 登记仓储：实现 :class:`RefreshTokenStorePort`。

旋转/复用检测的状态机操作全部收口为条件 UPDATE（先例
``KnowledgeDocumentRepository.claim_document`` 的读后乐观锁口径），
rowcount 即裁决结果；时间量一律绑定参数交 SQL 侧比较。
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, delete, update
from wireup import injectable

from app.domain.ports import RepositoryConflictError
from app.domain.user.ports import RefreshTokenStorePort
from app.models.domain.user import RefreshToken
from app.models.domain.user.refresh_token import (
    STATUS_ACTIVE,
    STATUS_REVOKED,
    STATUS_ROTATED,
)


@injectable(as_type=RefreshTokenStorePort)
@dataclass
class RefreshTokenRepository:
    engine: Engine

    def get(self, jti: str) -> RefreshToken | None:
        with Session(self.engine, expire_on_commit=False) as session:
            record = session.get(RefreshToken, jti)
            session.commit()
        return record

    def create(
        self, *, jti: str, user_id: UUID, family_id: str, expires_at: datetime
    ) -> RefreshToken:
        # jti 主键冲突在 uuid4 下近乎不可能，翻译为契约级冲突信号仅为
        # 遵守「驱动异常不出适配器」约定
        with Session(self.engine, expire_on_commit=False) as session:
            record = RefreshToken(
                jti=jti, user_id=user_id, family_id=family_id, expires_at=expires_at
            )
            session.add(record)
            try:
                session.commit()
            except IntegrityError:
                raise RepositoryConflictError("refresh token jti already exists") from None
            session.refresh(record)
        return record

    def claim_active(self, jti: str, *, rotated_at: datetime) -> bool:
        # 条件认领旋转：仅当行仍为 active 时置 rotated。并发双刷同一张
        # 票时后到者 rowcount=0 判负（领域层按宽限期口径给 401，不连坐）
        with Session(self.engine, expire_on_commit=False) as session:
            result = session.exec(
                update(RefreshToken)
                .where(col(RefreshToken.jti) == jti)
                .where(col(RefreshToken.status) == STATUS_ACTIVE)
                .values(status=STATUS_ROTATED, rotated_at=rotated_at)
            )
            claimed = int(result.rowcount) == 1
            session.commit()
        return claimed

    def revoke_on_reuse(self, jti: str, family_id: str, *, stale_before: datetime) -> bool:
        # 复用裁决：同一事务两步——先验该票是否「宽限期外的 rotated」
        # （窃取警报），命中才连坐吊销全族；宽限期内 rowcount=0 即放过
        with Session(self.engine, expire_on_commit=False) as session:
            stale_hit = session.exec(
                update(RefreshToken)
                .where(col(RefreshToken.jti) == jti)
                .where(col(RefreshToken.status) == STATUS_ROTATED)
                .where(col(RefreshToken.rotated_at) < stale_before)
                .values(status=STATUS_REVOKED)
            )
            if int(stale_hit.rowcount) == 0:
                session.commit()
                return False
            session.exec(
                update(RefreshToken)
                .where(col(RefreshToken.family_id) == family_id)
                .where(col(RefreshToken.status) != STATUS_REVOKED)
                .values(status=STATUS_REVOKED)
            )
            session.commit()
        return True

    def revoke_family(self, family_id: str) -> int:
        with Session(self.engine, expire_on_commit=False) as session:
            result = session.exec(
                update(RefreshToken)
                .where(col(RefreshToken.family_id) == family_id)
                .where(col(RefreshToken.status) != STATUS_REVOKED)
                .values(status=STATUS_REVOKED)
            )
            count = int(result.rowcount)
            session.commit()
        return count

    def revoke_all_for_user(self, user_id: UUID) -> int:
        with Session(self.engine, expire_on_commit=False) as session:
            result = session.exec(
                update(RefreshToken)
                .where(col(RefreshToken.user_id) == user_id)
                .where(col(RefreshToken.status) != STATUS_REVOKED)
                .values(status=STATUS_REVOKED)
            )
            count = int(result.rowcount)
            session.commit()
        return count

    def purge_expired(self, *, now: datetime) -> int:
        with Session(self.engine, expire_on_commit=False) as session:
            result = session.exec(
                delete(RefreshToken).where(col(RefreshToken.expires_at) < now)
            )
            count = int(result.rowcount)
            session.commit()
        return count
