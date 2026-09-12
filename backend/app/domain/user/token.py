"""JWT 令牌：签发与验签纯函数。

payload 仅携带无状态身份（user_id/username/exp/type）——鉴权依赖
（api/deps.require_user）验签后即得 principal，不回库查用户；用户被删的
残留令牌由各端点的归属查询（查无此行 → 404）自然兜住。

双令牌：短命 access（鉴权用）+ 长命 refresh（临期换新一对）。
``type`` claim 区分两者，互不可用；历史单令牌无 type claim，缺省按
access 处理以平滑过渡。

``jti`` claim（uuid4 hex）是令牌的一次性身份：随 TokenBundle/TokenPayload
透出，供 refresh_token 登记表做旋转与复用检测（服务端按 jti 查状
态）；access 验签路径不消费它。历史令牌可能无 jti（decode 侧为 None）。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt


ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


@dataclass(frozen=True)
class TokenPayload:
    user_id: UUID
    username: str
    jti: str | None = None


@dataclass(frozen=True)
class TokenBundle:
    token: str
    expires_at: datetime
    jti: str


def encode_token(
    *,
    user_id: UUID,
    username: str,
    token_type: str,
    secret: str,
    algorithm: str,
    expires_minutes: int,
    now: datetime | None = None,
) -> TokenBundle:
    issued_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires_at = issued_at + timedelta(minutes=expires_minutes)
    jti = uuid4().hex
    token = jwt.encode(
        {
            "sub": str(user_id),
            "username": username,
            "type": token_type,
            "iat": issued_at,
            "exp": expires_at,
            "jti": jti,  # 同秒内重复签发（如连续旋转 refresh）不产生相同令牌
        },
        secret,
        algorithm=algorithm,
    )
    return TokenBundle(token=token, expires_at=expires_at, jti=jti)


def encode_access_token(
    *,
    user_id: UUID,
    username: str,
    secret: str,
    algorithm: str,
    expires_minutes: int,
    now: datetime | None = None,
) -> TokenBundle:
    return encode_token(
        user_id=user_id,
        username=username,
        token_type=ACCESS_TOKEN_TYPE,
        secret=secret,
        algorithm=algorithm,
        expires_minutes=expires_minutes,
        now=now,
    )


def encode_refresh_token(
    *,
    user_id: UUID,
    username: str,
    secret: str,
    algorithm: str,
    expires_minutes: int,
    now: datetime | None = None,
) -> TokenBundle:
    return encode_token(
        user_id=user_id,
        username=username,
        token_type=REFRESH_TOKEN_TYPE,
        secret=secret,
        algorithm=algorithm,
        expires_minutes=expires_minutes,
        now=now,
    )


def decode_token(
    token: str,
    *,
    expected_type: str,
    secret: str,
    algorithm: str,
) -> TokenPayload:
    """验签并校验令牌类型；过期/签名非法/类型不符/结构不符抛
    :class:`jwt.InvalidTokenError` 子类，由调用方映射为业务异常（401）。

    历史 token 无 ``type`` claim，缺省按 access 处理。
    """
    payload = jwt.decode(token, secret, algorithms=[algorithm])
    if payload.get("type", ACCESS_TOKEN_TYPE) != expected_type:
        raise jwt.InvalidTokenError(f"token type mismatch: expected {expected_type}")
    return TokenPayload(
        user_id=UUID(str(payload["sub"])),
        username=str(payload["username"]),
        jti=payload.get("jti"),
    )


def decode_access_token(token: str, *, secret: str, algorithm: str) -> TokenPayload:
    return decode_token(
        token, expected_type=ACCESS_TOKEN_TYPE, secret=secret, algorithm=algorithm
    )


def decode_refresh_token(token: str, *, secret: str, algorithm: str) -> TokenPayload:
    return decode_token(
        token, expected_type=REFRESH_TOKEN_TYPE, secret=secret, algorithm=algorithm
    )
