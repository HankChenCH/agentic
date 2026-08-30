"""JWT 访问令牌：签发与验签纯函数。

payload 仅携带无状态身份（user_id/username/exp）——鉴权依赖
（api/deps.require_user）验签后即得 principal，不回库查用户；用户被删的
残留令牌由各端点的归属查询（查无此行 → 404）自然兜住。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt


@dataclass(frozen=True)
class TokenPayload:
    user_id: UUID
    username: str


@dataclass(frozen=True)
class TokenBundle:
    token: str
    expires_at: datetime


def encode_access_token(
    *,
    user_id: UUID,
    username: str,
    secret: str,
    algorithm: str,
    expires_minutes: int,
    now: datetime | None = None,
) -> TokenBundle:
    issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires_at = issued_at + timedelta(minutes=expires_minutes)
    token = jwt.encode(
        {
            "sub": str(user_id),
            "username": username,
            "iat": issued_at,
            "exp": expires_at,
        },
        secret,
        algorithm=algorithm,
    )
    return TokenBundle(token=token, expires_at=expires_at)


def decode_access_token(token: str, *, secret: str, algorithm: str) -> TokenPayload:
    """验签并解析；过期/签名非法/结构不符抛 :class:`jwt.InvalidTokenError` 子类，
    由调用方映射为业务异常（401）。"""
    payload = jwt.decode(token, secret, algorithms=[algorithm])
    return TokenPayload(
        user_id=UUID(str(payload["sub"])),
        username=str(payload["username"]),
    )
