"""多模态消息翻译（multimodal.user_message_from_content）的图片解析策略。

重点回归：本域附件引用的判定取 path 比前缀（与 resolve_own_key 同口径），
「API 根 + 相对路径」的绝对 URL 与裸相对路径都要走服务端读对象存储转
base64——按原始串 startswith 判定会把绝对引用误当外部 URL 透传给厂商
（云端拉不到本域地址，run 400/RUN_ERROR，e2e 实测回归）。

纯单测：LocalFilesystem + tmp 目录，无需 rustfs。
"""

import base64
from io import BytesIO

import pytest

from app.infrastructures.filesystem.local_provider import LocalFilesystem
from app.services.domain.conversation.attachments import ConversationAttachmentStore
from app.services.domain.conversation.multimodal import user_message_from_content

from conftest import TEST_USER_ID, StubLoggerFactory

_PAYLOAD = b"\x89PNG-fake-bytes"


@pytest.fixture()
def store(tmp_path):
    return ConversationAttachmentStore(
        filesystem=LocalFilesystem(root=tmp_path / "attachments"),
        logger_factory=StubLoggerFactory(),
    )


def _content_parts(*images: str):
    """一条「文字 + N 张图」的存储形态消息（camelCase，同前端落库形态）。"""
    parts: list[dict] = [{"type": "text", "text": "看看这张图"}]
    parts += [{"type": "image", "source": {"type": "url", "value": url}} for url in images]
    return parts


def _resolver_returning(payload: bytes | None):
    return lambda url: payload


def _image_blocks(message) -> list[dict]:
    return [b for b in message.content if isinstance(b, dict) and b.get("type") == "image"]


def test_plain_text_content_stays_string():
    assert user_message_from_content("你好", supports_vision=True).content == "你好"


def test_data_source_becomes_base64_block():
    msg = user_message_from_content(
        [{"type": "image", "source": {"type": "data", "value": "abc", "mimeType": "image/webp"}}],
        supports_vision=True,
    )
    assert _image_blocks(msg) == [{"type": "image", "base64": "abc", "mime_type": "image/webp"}]


def test_relative_attachment_url_resolved_to_base64():
    msg = user_message_from_content(
        _content_parts("/agentic/attachments/00000000-0000-0000-0000-000000000000/pic.png"),
        supports_vision=True,
        image_resolver=_resolver_returning(_PAYLOAD),
    )
    assert _image_blocks(msg) == [{
        "type": "image",
        "base64": base64.b64encode(_PAYLOAD).decode("ascii"),
        "mime_type": "image/png",
    }]


def test_absolute_url_with_api_root_resolved_to_base64():
    """回归：绝对 URL（API 根 + 本域路径）曾被原始串前缀判定误判为外部
    URL 透传厂商（云端拉不到 127.0.0.1，run 400）——必须服务端转 base64。"""
    absolute = "http://127.0.0.1:8000/agentic/attachments/a887f26f-8c08-4301-aa5b-22d1c2500000/pic.png"
    msg = user_message_from_content(
        _content_parts(absolute),
        supports_vision=True,
        image_resolver=_resolver_returning(_PAYLOAD),
    )
    assert _image_blocks(msg) == [{
        "type": "image",
        "base64": base64.b64encode(_PAYLOAD).decode("ascii"),
        "mime_type": "image/png",
    }]


def test_external_url_passthrough_untouched():
    msg = user_message_from_content(
        _content_parts("https://example.com/photos/cat.png"),
        supports_vision=True,
        image_resolver=_resolver_returning(_PAYLOAD),  # 外部 URL 不应消费 resolver
    )
    assert _image_blocks(msg) == [{"type": "image", "url": "https://example.com/photos/cat.png"}]


def test_attachment_reference_read_failure_degrades_to_drop():
    msg = user_message_from_content(
        _content_parts("/agentic/attachments/00000000-0000-0000-0000-000000000000/gone.png"),
        supports_vision=True,
        image_resolver=_resolver_returning(None),
    )
    assert _image_blocks(msg) == []
    assert msg.content == [{"type": "text", "text": "看看这张图"}]


def test_foreign_host_with_attachment_path_not_passthrough():
    """path 撞上本域前缀的外部地址走 resolver（按属主 key 读对象存储，读不到
    即降级丢图）而非透传 url 块——不存在服务端代拉任意外部地址的通路。"""
    foreign = "https://evil.example.com/agentic/attachments/00000000-0000-0000-0000-000000000000/x.png"
    msg = user_message_from_content(
        _content_parts(foreign),
        supports_vision=True,
        image_resolver=_resolver_returning(None),
    )
    assert _image_blocks(msg) == []


def test_attachment_reference_without_resolver_dropped():
    msg = user_message_from_content(
        _content_parts("/agentic/attachments/00000000-0000-0000-0000-000000000000/pic.png"),
        supports_vision=True,
        image_resolver=None,
    )
    assert _image_blocks(msg) == []


def test_text_block_leads_mixed_content():
    msg = user_message_from_content(
        _content_parts("/agentic/attachments/00000000-0000-0000-0000-000000000000/pic.png"),
        supports_vision=True,
        image_resolver=_resolver_returning(_PAYLOAD),
    )
    assert msg.content[0] == {"type": "text", "text": "看看这张图"}
    assert len(msg.content) == 2


def test_non_vision_model_drops_images_with_note():
    msg = user_message_from_content(_content_parts("https://example.com/a.png"), supports_vision=False)
    assert msg.content == "看看这张图\n\n（已忽略 1 张图片：当前模型不支持图片输入）"


def test_non_vision_history_replay_silent():
    msg = user_message_from_content(
        _content_parts("https://example.com/a.png"), supports_vision=False, note_on_degrade=False,
    )
    assert msg.content == "看看这张图"


def test_real_store_roundtrip_absolute_url(store):
    """端到端同形：真实上传的稳定引用拼 API 根后，经 resolver 取回字节转
    base64——e2e 报错链路的单测收敛。"""
    stored = store.save(TEST_USER_ID, "pic.png", "image/png", BytesIO(_PAYLOAD))
    absolute = f"http://127.0.0.1:8000{stored.url}"
    resolver = store.own_url_resolver(TEST_USER_ID)

    msg = user_message_from_content(
        _content_parts(absolute), supports_vision=True, image_resolver=resolver,
    )
    block = _image_blocks(msg)[0]
    assert base64.b64decode(block["base64"]) == _PAYLOAD
    assert block["mime_type"] == "image/png"
