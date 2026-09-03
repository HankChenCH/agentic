"""ConversationAttachmentStore 领域契约：key/url 布局、上传校验、预签名降级、
跨用户隔离与引用解析。

纯单测：LocalFilesystem + tmp 目录（presign 返回 None → 端点降级口径），
无需 rustfs。
"""

import base64
from io import BytesIO
from uuid import uuid4

import pytest

from app.api.deps import UserPrincipal
from app.api.v1.endpoints.attachments import display_attachment_url
from app.exceptions import (
    AttachmentNotFoundError,
    AttachmentTooLargeError,
    UnsupportedAttachmentTypeError,
)
from app.infrastructures.filesystem.local_provider import LocalFilesystem
from app.models.schema.response.biz_response import Response
from app.services.domain.conversation.attachments import (
    ATTACHMENT_URL_PREFIX,
    ConversationAttachmentStore,
)

from conftest import TEST_USER_ID, StubLoggerFactory


@pytest.fixture()
def store(tmp_path):
    return ConversationAttachmentStore(
        filesystem=LocalFilesystem(root=tmp_path / "attachments"),
        logger_factory=StubLoggerFactory(),
    )


def _save_png(store, name="pic.png", payload=b"\x89PNG-fake-bytes"):
    return store.save(TEST_USER_ID, name, "image/png", BytesIO(payload))


def test_save_returns_stable_reference_and_stores_bytes(store):
    stored = _save_png(store)

    assert stored.url.startswith(f"{ATTACHMENT_URL_PREFIX}")
    assert stored.filename == "pic.png"
    assert stored.mime_type == "image/png"
    assert stored.size_bytes == len(b"\x89PNG-fake-bytes")

    key = store.resolve_own_key(stored.url, TEST_USER_ID)
    assert key is not None
    assert store.read(key) == b"\x89PNG-fake-bytes"


def test_save_rejects_non_image(store):
    with pytest.raises(UnsupportedAttachmentTypeError):
        store.save(TEST_USER_ID, "notes.txt", "text/plain", BytesIO(b"hello"))
    # 扩展名推断不到图片类型同样拒绝（无 content_type 时）
    with pytest.raises(UnsupportedAttachmentTypeError):
        store.save(TEST_USER_ID, "archive.bin", None, BytesIO(b"\x00\x01"))


def test_save_rejects_oversize(store):
    big = BytesIO(b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(AttachmentTooLargeError):
        store.save(TEST_USER_ID, "big.png", "image/png", big)


def test_resolve_own_key_is_scoped_to_user(store):
    stored = _save_png(store)
    other_user = uuid4()

    own = store.resolve_own_key(stored.url, TEST_USER_ID)
    foreign = store.resolve_own_key(stored.url, other_user)

    # 他人引用解析到不存在的 key（按不存在处理，不泄露存在性）
    assert own is not None and foreign is not None
    assert own != foreign
    with pytest.raises(AttachmentNotFoundError):
        store.read(foreign)


def test_resolve_own_key_rejects_foreign_shapes(store):
    assert store.resolve_own_key("https://evil.example.com/x.png", TEST_USER_ID) is None
    assert store.resolve_own_key("/agentic/attachments/not-a-uuid/pic.png", TEST_USER_ID) is None
    assert store.resolve_own_key("/agentic/attachments/only-id", TEST_USER_ID) is None
    assert store.resolve_own_key("/knowledge/kb/doc/file", TEST_USER_ID) is None


def test_resolve_own_key_accepts_full_url_with_host(store):
    stored = _save_png(store)
    absolute = f"https://files.example.com{stored.url}?view=1"
    key = store.resolve_own_key(absolute, TEST_USER_ID)
    assert key == store.resolve_own_key(stored.url, TEST_USER_ID)


def test_presign_degrades_on_local_backend(store):
    stored = _save_png(store)
    key = store.resolve_own_key(stored.url, TEST_USER_ID)
    assert store.presign(key) is None  # local 后端不支持签名 → 端点降级流式回源


def test_read_missing_raises_not_found(store):
    with pytest.raises(AttachmentNotFoundError):
        store.read("conversation-attachments/nobody/00000000-0000-0000-0000-000000000000/x.png")


def test_own_url_resolver_roundtrip(store):
    stored = _save_png(store)
    payload = b"\x89PNG-fake-bytes"
    resolver = store.own_url_resolver(TEST_USER_ID)

    resolved = resolver(f"https://backend.example.com{stored.url}")
    assert base64.b64encode(resolved) == base64.b64encode(payload)
    assert resolver("https://evil.example.com/x.png") is None


def _principal():
    return UserPrincipal(user_id=TEST_USER_ID, username="tester")


def test_display_url_endpoint_returns_envelope(store, monkeypatch):
    """展示地址端点：预签名可用返回签名 URL，本地后端降级 url=None
    （调用方转鉴权回源）。ref 接受带 host 的绝对引用（resolve_own_key
    同口径）。"""
    stored = _save_png(store)

    resp = display_attachment_url(
        store=store, principal=_principal(), ref=f"http://any-host:8000{stored.url}",
    )
    assert resp == Response.success({"url": None}).to_dict()

    monkeypatch.setattr(store, "presign", lambda key, expires_in=600: f"http://signed/{key}")
    resp = display_attachment_url(
        store=store, principal=_principal(), ref=stored.url,
    )
    assert resp["response"]["url"].startswith("http://signed/")


def test_display_url_endpoint_rejects_foreign_ref(store):
    # 形态非法（非本域/非 UUID/缺文件名段）→ resolve_own_key 为 None → 404
    with pytest.raises(AttachmentNotFoundError):
        display_attachment_url(store=store, principal=_principal(), ref="/knowledge/kb/doc")
    with pytest.raises(AttachmentNotFoundError):
        display_attachment_url(
            store=store, principal=_principal(),
            ref="/agentic/attachments/not-a-uuid/pic.png",
        )


def test_display_url_endpoint_missing_object_degrades(store):
    # 形态合法但对象不存在：key 命中本人前缀下的空位，与下载 302 路径同款
    # ——签名地址指向不存在的对象，浏览器端 404（S3 预签名不做存在性检查）
    resp = display_attachment_url(
        store=store, principal=_principal(),
        ref="/agentic/attachments/00000000-0000-0000-0000-000000000000/gone.png",
    )
    assert resp == Response.success({"url": None}).to_dict()
