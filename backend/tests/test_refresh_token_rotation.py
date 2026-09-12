"""refresh 旋转与复用检测：登记表状态机 + 族语义。

纯单测：临时 SQLite（conftest 的 engine fixture）+ 真实 UserService/
RefreshTokenRepository。覆盖：族登记（一次登录一个族、多设备独立）/
旋转（条件认领继承族）/ 宽限期内重放不连坐 / 宽限期外复用族连坐 /
吊销票、未知票、历史无 jti 票同口径拒绝 / 登出按族 / 改密全吊销 /
过期行清理。
"""

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from sqlmodel import Session, col, update

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-0123456789abcdef-0123456789abcdef")

from app.adapters.persistence.refresh_token_repository import RefreshTokenRepository
from app.exceptions import InvalidCredentialsError
from app.models.domain.user.refresh_token import (
    STATUS_ACTIVE,
    STATUS_REVOKED,
    STATUS_ROTATED,
    RefreshToken,
)

from test_user_service import make_service

SECRET = "test-secret-0123456789abcdef-0123456789abcdef"


def repo(engine):
    return RefreshTokenRepository(engine=engine)


def craft_refresh_jwt(*, jti=None, user_id=None, expires_minutes=60):
    """绕过登记的测试票：可控 jti/expiry（legacy 无 jti、未知 jti 用）。"""
    claims = {
        "sub": str(user_id or uuid4()),
        "username": "alice",
        "type": "refresh",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=expires_minutes),
    }
    if jti is not None:
        claims["jti"] = jti
    return jwt.encode(claims, SECRET, algorithm="HS256")


def age_rotated_at(engine, jti: str, *, minutes_ago: float) -> None:
    """把该票的 rotated_at 拨回过去（宽限期外重放的前提）。"""
    with Session(engine, expire_on_commit=False) as session:
        session.exec(
            update(RefreshToken)
            .where(col(RefreshToken.jti) == jti)
            .values(rotated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
        )
        session.commit()


# ---------------- 族登记 ----------------

def test_register_and_login_create_independent_families(engine):
    svc = make_service(engine)
    first = svc.register("alice", "password123")
    second = svc.login("alice", "password123")

    row_a = repo(engine).get(first.refresh_token.jti)
    row_b = repo(engine).get(second.refresh_token.jti)
    # 一次登录一个族：族根 = 本次 refresh 的 jti；两次登录互不隶属
    assert row_a.status == row_b.status == STATUS_ACTIVE
    assert row_a.family_id == first.refresh_token.jti
    assert row_b.family_id == second.refresh_token.jti
    assert row_a.family_id != row_b.family_id


# ---------------- 旋转 ----------------

def test_refresh_rotates_claim_and_extends_family(engine):
    svc = make_service(engine)
    session = svc.register("alice", "password123")
    old_jti = session.refresh_token.jti

    rotated = svc.refresh(session.refresh_token.token)
    new_row = repo(engine).get(rotated.refresh_token.jti)
    old_row = repo(engine).get(old_jti)

    assert old_row.status == STATUS_ROTATED and old_row.rotated_at is not None
    assert new_row.status == STATUS_ACTIVE
    assert new_row.family_id == old_row.family_id       # 旋转继承族
    assert new_row.jti != old_jti


def test_refresh_replay_within_grace_rejected_but_family_alive(engine):
    """宽限期内重放（多标签页竞态）：只拒绝不连坐，新票仍可刷。"""
    svc = make_service(engine)
    session = svc.register("alice", "password123")
    rotated = svc.refresh(session.refresh_token.token)

    with pytest.raises(InvalidCredentialsError):
        svc.refresh(session.refresh_token.token)        # 立即重放已旋转票

    again = svc.refresh(rotated.refresh_token.token)    # 族未被连坐：新票照常
    assert again.refresh_token.jti != rotated.refresh_token.jti


def test_refresh_replay_beyond_grace_revokes_family(engine):
    """宽限期外重放 = 窃取警报：整族连坐，包括后续合法新票。"""
    svc = make_service(engine)
    session = svc.register("alice", "password123")
    rotated = svc.refresh(session.refresh_token.token)
    age_rotated_at(engine, session.refresh_token.jti, minutes_ago=5)  # 超出 60s 宽限

    with pytest.raises(InvalidCredentialsError):
        svc.refresh(session.refresh_token.token)

    assert repo(engine).get(rotated.refresh_token.jti).status == STATUS_REVOKED
    with pytest.raises(InvalidCredentialsError):
        svc.refresh(rotated.refresh_token.token)        # 连坐后的合法新票也失效


# ---------------- 拒绝口径 ----------------

def test_refresh_rejects_revoked_unknown_legacy_and_expired(engine):
    svc = make_service(engine)
    session = svc.register("alice", "password123")

    svc.logout_family(session.refresh_token.token)      # 票已吊销
    with pytest.raises(InvalidCredentialsError):
        svc.refresh(session.refresh_token.token)
    with pytest.raises(InvalidCredentialsError):
        svc.refresh(craft_refresh_jwt(jti=uuid4().hex))  # 验签通过但登记无此票
    with pytest.raises(InvalidCredentialsError):
        svc.refresh(craft_refresh_jwt())                 # 历史无 jti：无从查登记
    expired = craft_refresh_jwt(jti=uuid4().hex, expires_minutes=-1)
    with pytest.raises(InvalidCredentialsError):
        svc.refresh(expired)                             # JWT exp 先行兜住


# ---------------- 登出 ----------------

def test_logout_revokes_only_own_family(engine):
    svc = make_service(engine)
    svc.register("alice", "password123")
    here = svc.login("alice", "password123")            # 设备 A
    other = svc.login("alice", "password123")           # 设备 B（另一族）

    svc.logout_family(here.refresh_token.token)

    with pytest.raises(InvalidCredentialsError):
        svc.refresh(here.refresh_token.token)
    still = svc.refresh(other.refresh_token.token)      # 他族（设备 B）无恙
    assert still.refresh_token.jti != other.refresh_token.jti


def test_logout_is_idempotent_on_invalid_tokens(engine):
    svc = make_service(engine)
    svc.logout_family(None)
    svc.logout_family("")
    svc.logout_family("not-a-jwt")
    svc.logout_family(craft_refresh_jwt())              # 无 jti 同样静默
    svc.logout_family(craft_refresh_jwt(jti=uuid4().hex))  # 登记无此票


# ---------------- 改密全吊销 ----------------

def test_change_password_revokes_all_families(engine):
    svc = make_service(engine)
    svc.register("alice", "password123")
    device_a = svc.login("alice", "password123")
    device_b = svc.login("alice", "password123")
    rotated = svc.refresh(device_b.refresh_token.token)  # 旋转产生的后代行同样连坐

    svc.change_password(device_a.user.id, "password123", "newpassword123")

    for bundle in (
        device_a.refresh_token,
        device_b.refresh_token,
        rotated.refresh_token,
    ):
        with pytest.raises(InvalidCredentialsError):
            svc.refresh(bundle.token)
    assert svc.login("alice", "newpassword123").user.id == device_a.user.id


# ---------------- 过期清理 ----------------

def test_purge_expired_removes_only_expired_rows(engine):
    svc = make_service(engine)
    session = svc.register("purge", "password123")
    store = repo(engine)
    past = store.create(
        jti=uuid4().hex, user_id=session.user.id, family_id=uuid4().hex,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    future = store.create(
        jti=uuid4().hex, user_id=session.user.id, family_id=uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )

    removed = store.purge_expired(now=datetime.now(timezone.utc))

    assert removed >= 1
    assert store.get(past.jti) is None
    assert store.get(future.jti) is not None


def test_write_paths_trigger_opportunistic_purge(engine):
    """惯性清理接线：register/login/refresh 等写路径各触发一次
    _purge_expired——过期行被顺手清掉，登记表不随时间无限膨胀。"""
    svc = make_service(engine)
    session = svc.register("purge_wiring", "password123")
    store = repo(engine)
    expired = store.create(
        jti=uuid4().hex, user_id=session.user.id, family_id=uuid4().hex,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    # login（写路径）即清：无需等调度器
    svc.login("purge_wiring", "password123")

    assert store.get(expired.jti) is None
    # 本次登录自己的族根不受影响
    assert store.get(session.refresh_token.jti) is not None
