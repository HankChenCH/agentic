"""会话附件上传 e2e：multipart 上传 → 展示地址换签 → 引用可达。

直连与经 nginx 全栈入口双跑（顺带覆盖 §12.3 反代的 multipart 放行口径
——nginx ``client_max_body_size 64m`` 与后端 upload 作用域对齐）。
"""


from helpers import _client, _register


def test_12_attachment_upload_and_display_url():
    with _client() as c:
        user = _register(c, "att")
        auth = {"Authorization": f"Bearer {user['token']}"}

        # 上传：返回稳定引用（前端入库进消息 content）
        upload = c.post(
            "/agentic/attachments", headers=auth,
            files={"file": ("e2e图.png", b"\x89PNG\r\n\x1a\ne2e-bytes", "image/png")},
        )
        assert upload.status_code == 200, upload.text
        body = upload.json()["response"]
        ref = body["url"]
        assert ref.startswith("/agentic/attachments/")
        assert body["size_bytes"] > 0

        # 展示地址换签：签名后端返回预签名 URL；本地磁盘后端 url=null（降级）
        display = c.get("/agentic/attachments/url", headers=auth, params={"ref": ref})
        assert display.status_code == 200, display.text
        display_body = display.json()["response"]
        assert display_body["url"] is None or display_body["url"].startswith("http")

        # 引用可达（属主本人）：302 到预签名或流式回源字节
        fetch = c.get(ref, headers=auth)
        assert fetch.status_code in (200, 302), fetch.text
        if fetch.status_code == 200:
            assert fetch.content == b"\x89PNG\r\n\x1a\ne2e-bytes"

        # 他人引用按不存在处理（他人 id 换进本人 key 命名空间 → 空位）：
        # s3 形态预签名先行 → 302 到指向空位的签名 URL（浏览器端 404）；
        # 本地磁盘形态降级回源 → 404。两种形态都不泄露他人对象存在性。
        stranger = _register(c, "att_s")
        status = c.get(ref, headers={"Authorization": f"Bearer {stranger['token']}"}).status_code
        assert status in (302, 404), status
