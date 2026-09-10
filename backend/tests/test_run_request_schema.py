"""RunMessage 多模态 content 校验契约：字符串兼容、InputContent 数组、
各上限闸（图片数/文本总量/URL 长度/binary 拒绝）与 storage_content 归一。

纯单测：pydantic 校验，无 IO。
"""

import pytest
from pydantic import ValidationError
from uuid import UUID

from app.models.schema.request.run import RunMessage, RunRequest


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


# ==================== 历史空 content 容忍与末条提问闸（RunRequest） ====================
#
# 回放历史里空字符串 content 是协议合法形态：工具空返回（如 timeline 空命中
# 曾落库 content:""）、只有工具调用没有文本的 assistant 消息。历史上该形态
# 被 min_length=1 拒绝后，整条会话的续聊/重试全部 422 毒化。


def _run_request(*role_content_pairs):
    messages = [
        RunMessage(id=f"m{i}", role=role, content=content)
        for i, (role, content) in enumerate(role_content_pairs)
    ]
    return RunRequest(
        threadId=UUID("7a50549d-9d99-4797-bc0b-c72f9a70b48d"),
        runId="run-1",
        messages=messages,
    )


def test_empty_string_history_content_accepted():
    msg = RunMessage(id="m12", role="tool", content="")
    assert msg.storage_content() == [{"type": "text", "text": ""}]


def test_run_request_accepts_empty_content_history_with_valid_prompt():
    req = _run_request(
        ("user", "复述：测试历史回放"),
        ("assistant", ""),   # 纯工具调用 assistant 消息（无文本）
        ("tool", ""),        # 工具空返回
        ("user", "继续"),
    )
    assert req.messages[-1].content == "继续"


def test_run_request_rejects_empty_last_message():
    with pytest.raises(ValidationError, match="current prompt"):
        _run_request(("user", "你好"), ("tool", ""), ("tool", ""))


def test_run_request_rejects_whitespace_only_last_message():
    with pytest.raises(ValidationError, match="current prompt"):
        _run_request(("user", "你好"), ("user", "   "))


def test_run_request_allows_image_only_last_message():
    req = _run_request(("user", "你好"), ("user", [_image_url()]))
    assert req.messages[-1].storage_content()[0]["type"] == "image"


def test_run_request_rejects_empty_messages_list():
    with pytest.raises(ValidationError):
        _run_request()
