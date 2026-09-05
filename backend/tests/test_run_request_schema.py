"""RunMessage 多模态 content 校验契约：字符串兼容、InputContent 数组、
各上限闸（图片数/文本总量/URL 长度/binary 拒绝）与 storage_content 归一。

纯单测：pydantic 校验，无 IO。
"""

import pytest
from pydantic import ValidationError

from app.models.schema.request.run import RunMessage


def _image_url(value="/agentic/attachments/3fa85f64-5717-4562-b3fc-2c963f66afa6/pic.png", mime="image/png"):
    return {"type": "image", "source": {"type": "url", "value": value, "mimeType": mime}}


def test_string_content_still_accepted():
    msg = RunMessage(id="m1", role="user", content="你好")
    assert msg.storage_content() == [{"type": "text", "text": "你好"}]


def test_multimodal_content_accepted_and_normalized():
    msg = RunMessage(id="m2", role="user", content=[
        {"type": "text", "text": "看这张图"},
        _image_url(),
    ])
    stored = msg.storage_content()
    assert stored[0] == {"type": "text", "text": "看这张图"}
    # camelCase 别名落盘（ag-ui 协议原样），metadata: None 被 exclude_none 剔除
    assert stored[1] == {
        "type": "image",
        "source": {"type": "url", "value": _image_url()["source"]["value"], "mimeType": "image/png"},
    }


def test_empty_content_array_rejected():
    with pytest.raises(ValidationError):
        RunMessage(id="m3", role="user", content=[])


def test_binary_part_rejected():
    with pytest.raises(ValidationError, match="binary"):
        RunMessage(id="m4", role="user", content=[
            {"type": "binary", "url": "https://example.com/a.png", "mime_type": "image/png"},
        ])


def test_more_than_four_images_rejected():
    with pytest.raises(ValidationError, match="at most 4"):
        RunMessage(id="m5", role="user", content=[
            {"type": "text", "text": "多图"},
            *[_image_url(f"https://example.com/{i}.png") for i in range(5)],
        ])


def test_unknown_part_type_rejected_by_union():
    with pytest.raises(ValidationError):
        RunMessage(id="m6", role="user", content=[{"type": "smell", "value": "x"}])


def test_array_without_text_or_image_rejected():
    # audio/video/document 本期不产生且无法被消费
    with pytest.raises(ValidationError, match="at least one text or image"):
        RunMessage(id="m7", role="user", content=[
            {"type": "document", "source": {"type": "url", "value": "https://example.com/a.pdf"}},
        ])


def test_oversize_url_rejected():
    with pytest.raises(ValidationError, match="within 2048"):
        RunMessage(id="m8", role="user", content=[_image_url("https://example.com/" + "a" * 3000)])


def test_oversize_text_rejected():
    with pytest.raises(ValidationError):
        RunMessage(id="m9", role="user", content=[{"type": "text", "text": "字" * 200_001}])


def test_text_accumulates_across_parts():
    with pytest.raises(ValidationError):
        RunMessage(id="m10", role="user", content=[
            {"type": "text", "text": "字" * 120_000},
            _image_url(),
            {"type": "text", "text": "字" * 120_000},
        ])


def test_image_only_message_allowed():
    # 图示提问：无文本纯图片合法
    msg = RunMessage(id="m11", role="user", content=[_image_url()])
    assert msg.storage_content()[0]["type"] == "image"
