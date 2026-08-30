"""用户域：注册/登录/令牌与认证依赖。

纯单测：临时 SQLite（conftest 的 engine fixture）+ 真实 UserService；
auth.yaml 的 JWT 密钥经环境变量注入避免读取仓库配置（isolated environ）。
"""

import os
from datetime import datetime, timezone
from uuid import uuid4

import jwt
import pytest

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-0123456789abcdef-0123456789abcdef")

from app.exceptions import (
    InvalidCredentialsError,
    UserInvalidParamError,
    UsernameDuplicatedError,
    UserNotFoundError,
)
from app.models.domain.user import DEFAULT_USER_ID
from app.repositories.user_repository import UserRepository
from app.services.domain.user.passwords import hash_password, verify_password
from app.services.domain.user.token import decode_access_token, encode_access_token
from app.services.domain.user.user_service import UserService

from conftest import StubLoggerFactory


def make_service(engine, memory_user_node=None):
    return UserService(
        user_repo=UserRepository(engine=engine),
        memory_user_node=memory_user_node or RecordingUserNodeSync(),
        app_config=type("Cfg", (), {"auth": type(
            "Auth", (),
            {"jwt_secret": "test-secret-0123456789abcdef-0123456789abcdef",
             "jwt_algorithm": "HS256", "token_expire_minutes": 30},
        )()})(),
        logger_factory=StubLoggerFactory(),
    )


class RecordingUserNodeSync:
    """UserNodeSyncPort 替身：记录同步调用，可注入失败。"""

    def __init__(self, error=None):
        self.calls = []
        self._error = error

    def sync_user_node(self, user_id, username, nickname):
        if self._error is not None:
            raise self._error
        self.calls.append((user_id, username, nickname))
        return object()


# ---------------- 注册 ----------------

def test_register_creates_user_and_issues_token(engine):
    svc = make_service(engine)
    session = svc.register("alice", "password123")

    assert session.user.username == "alice"
    assert session.user.password_hash != "password123"     # 不落明文
    assert session.user.id != DEFAULT_USER_ID              # 默认用户是回填专用，不会被复用
    payload = decode_access_token(
        session.token.token,
        secret="test-secret-0123456789abcdef-0123456789abcdef", algorithm="HS256",
    )
    assert payload.user_id == session.user.id and payload.username == "alice"


def test_register_syncs_memory_user_node(engine):
    sync = RecordingUserNodeSync()
    svc = make_service(engine, memory_user_node=sync)
    session = svc.register("alice", "password123")

    assert sync.calls == [(session.user.id, "alice", "")]  # 注册即预建并写账号信息


def test_register_memory_sync_failure_does_not_block(engine):
    sync = RecordingUserNodeSync(error=RuntimeError("memory down"))
    svc = make_service(engine, memory_user_node=sync)

    session = svc.register("alice", "password123")         # 同步失败仅告警，注册照常成功
    assert svc.login("alice", "password123").user.id == session.user.id


def test_register_duplicate_username_rejected(engine):
    svc = make_service(engine)
    svc.register("bob", "password123")
    with pytest.raises(UsernameDuplicatedError):
        svc.register("bob", "another-pass")


def test_register_validates_username_and_password_format(engine):
    svc = make_service(engine)
    with pytest.raises(UserInvalidParamError):
        svc.register("ab", "password123")                  # 用户名过短
    with pytest.raises(UserInvalidParamError):
        svc.register("bad name!", "password123")           # 非法字符
    with pytest.raises(UserInvalidParamError):
        svc.register("alice", "short")                     # 密码过短


# ---------------- 登录 ----------------

def test_login_success_and_wrong_password_same_shape(engine):
    svc = make_service(engine)
    svc.register("carol", "password123")

    session = svc.login("carol", "password123")
    assert session.user.username == "carol" and session.token.token

    with pytest.raises(InvalidCredentialsError):
        svc.login("carol", "wrong-password")
    with pytest.raises(InvalidCredentialsError):
        svc.login("ghost", "password123")                  # 用户不存在同口径，不泄露存在性


def test_default_user_cannot_login(engine):
    """迁移回填的默认用户密码哈希为空串：验密恒失败（占位不可登录）。"""
    svc = make_service(engine)
    with pytest.raises(InvalidCredentialsError):
        svc.login("default", "")


# ---------------- 查询 ----------------

def test_get_user_missing_raises(engine):
    svc = make_service(engine)
    with pytest.raises(UserNotFoundError):
        svc.get_user(uuid4())


# ---------------- 令牌与密码基元 ----------------

def test_token_roundtrip_and_expiry():
    secret = "test-secret-0123456789abcdef-0123456789abcdef"
    bundle = encode_access_token(
        user_id=uuid4(), username="u", secret=secret, algorithm="HS256",
        expires_minutes=5,
    )
    payload = decode_access_token(bundle.token, secret=secret, algorithm="HS256")
    assert payload.username == "u"

    expired = encode_access_token(
        user_id=uuid4(), username="u", secret=secret, algorithm="HS256",
        expires_minutes=-1,
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired.token, secret=secret, algorithm="HS256")


def test_password_hash_roundtrip_and_empty_stored():
    stored = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("anything", "")             # 空哈希（默认用户）恒失败
    assert not verify_password("anything", "garbage-format")


def datetime_utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)
