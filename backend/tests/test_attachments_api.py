"""会话附件 HTTP 流程集成测试：上传 → 展示地址换签 → 读取（302/回源降级）。

真实 ConversationAppService + ConversationAttachmentStore，Filesystem 端口
换本地磁盘替身（presign 不可用 → 触发 url=null 与流式回源两条降级分支）。
store 层的类型/大小校验与稳定引用口径已在 test_conversation_attachments.py
锁定；本文件补齐此前缺失的 HTTP 面（multipart、信封、归属 404）。
"""


import pytest

import http_test_kit  # noqa: F401  必须先于 app.* 导入（env 就位）
from http_test_kit import (  # noqa: E402
    build_app,
    new_username,
    register_and_login,
    http_client_for,
)

from app.adapters.filesystem.local_provider import LocalFilesystem  # noqa: E402
from app.domain.ports import Filesystem  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("attachments-api")
    with http_client_for(build_app([(Filesystem, LocalFilesystem(root=root))])) as test_client:
        yield test_client


def _upload(client, headers, *, filename="hello.png", content="image/png", data=b"\x89PNG-bytes"):
    resp = client.post(
        "/agentic/attachments", headers=headers,
        files={"file": (filename, data, content)},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["response"]


def _ref(client, headers) -> str:
    return _upload(client, headers)["url"]


def test_upload_returns_stable_reference(client):
    headers, _ = register_and_login(client, new_username("att"))

    body = _upload(client, headers, filename="截图.png")
    assert body["filename"] == "截图.png"
    assert body["mime_type"] == "image/png"
    assert body["size_bytes"] == len(b"\x89PNG-bytes")
    # 稳定引用：{prefix}{id}/{filename}，前端入库进消息 content
    assert body["url"].startswith("/agentic/attachments/")
    assert body["id"] in body["url"]


def test_upload_rejects_non_image(client):
    headers, _ = register_and_login(client, new_username("att"))
    resp = client.post(
        "/agentic/attachments", headers=headers,
        files={"file": ("doc.txt", b"plain", "text/plain")},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error_code"] == 1010


def test_display_url_local_backend_degrades_to_null(client):
    """本地磁盘后端：签名不可用 → url=null，前端降级鉴权回源。"""
    headers, _ = register_and_login(client, new_username("att"))
    ref = _ref(client, headers)

    resp = client.get("/agentic/attachments/url", headers=headers, params={"ref": ref})
    assert resp.status_code == 200, resp.text
    assert resp.json()["response"] == {"url": None}


def test_display_url_accepts_absolute_ref_with_foreign_host(client):
    """前端发送「API 根 + 相对路径」的绝对 URL（host 任意）——按 path 前缀判定。"""
    headers, _ = register_and_login(client, new_username("att"))
    ref = _ref(client, headers)

    resp = client.get(
        "/agentic/attachments/url", headers=headers,
        params={"ref": f"https://any-host.example.com{ref}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["response"] == {"url": None}


def test_read_streams_back_when_presign_unavailable(client):
    headers, _ = register_and_login(client, new_username("att"))
    ref = _ref(client, headers)

    resp = client.get(ref, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.content == b"\x89PNG-bytes"
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.headers.get("cache-control") == "private, max-age=60"


def test_attachment_scoped_to_owner(client):
    """归属作用域：他人附件一律 404（读取与换签同口径，不泄露存在性）。"""
    headers_a, _ = register_and_login(client, new_username("att"))
    ref = _ref(client, headers_a)

    headers_b, _ = register_and_login(client, new_username("att"))
    # 读取：他人 id 换进本人 key 命名空间 → 对象不存在 → 404（不泄露存在性）
    assert client.get(ref, headers=headers_b).status_code == 404
    # 换签：同一语义落到「不存在的对象」——本地后端 url=null（签名地址本身
    # 指向空位，浏览器端 404；不外泄他人对象的存在性）
    assert client.get("/agentic/attachments/url", headers=headers_b, params={"ref": ref}).status_code == 200
    assert client.get("/agentic/attachments/url", headers=headers_b, params={"ref": ref}).json()["response"] == {"url": None}
    assert client.get(ref + "x", headers=headers_b).status_code == 404
