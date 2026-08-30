"""认证依赖 require_user：JWT 无状态验签与 401 口径（不走完整应用装配）。"""

import os

os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-0123456789abcdef-0123456789abcdef")

from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.deps import UserPrincipal, require_user
from app.exceptions import InvalidCredentialsError
from app.services.domain.user.token import encode_access_token

SECRET = os.environ["AUTH_JWT_SECRET"]


def _request(headers):
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


def test_missing_or_malformed_header_rejected():
    with pytest.raises(InvalidCredentialsError):
        require_user(_request({}))
    with pytest.raises(InvalidCredentialsError):
        require_user(_request({"Authorization": "Basic dXNlcjpwYXNz"}))
    with pytest.raises(InvalidCredentialsError):
        require_user(_request({"Authorization": "Bearer "}))
    with pytest.raises(InvalidCredentialsError):
        require_user(_request({"Authorization": "Bearer   "}))


def test_valid_token_yields_principal():
    bundle = encode_access_token(
        user_id=uuid4(), username="alice", secret=SECRET, algorithm="HS256",
        expires_minutes=5,
    )
    principal = require_user(_request({"Authorization": f"Bearer {bundle.token}"}))
    assert isinstance(principal, UserPrincipal)
    assert principal.username == "alice"


def test_garbage_and_cross_secret_tokens_rejected():
    with pytest.raises(InvalidCredentialsError):
        require_user(_request({"Authorization": "Bearer not-a-jwt"}))
    other = encode_access_token(
        user_id=uuid4(), username="alice", secret="another-secret-0123456789abcdef",
        algorithm="HS256", expires_minutes=5,
    )
    with pytest.raises(InvalidCredentialsError):
        require_user(_request({"Authorization": f"Bearer {other.token}"}))
