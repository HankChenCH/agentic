"""ConversationAttachmentStore 领域契约：key/url 布局、上传校验、预签名降级、
跨用户隔离与引用解析。

纯单测：LocalFilesystem + tmp 目录（presign 返回 None → 端点降级口径），
无需 rustfs。
"""

import base64
from io import BytesIO
from uuid import uuid4

import pytest

from app.exceptions import (
    AttachmentNotFoundError,
    AttachmentTooLargeError,
    UnsupportedAttachmentTypeError,
)
from app.infrastructures.filesystem.local_provider import LocalFilesystem
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
