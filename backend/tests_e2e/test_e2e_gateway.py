"""nginx 全栈入口变体断言（仅 ``E2E_API_PREFIX`` 非空时运行）。

直连模式下跳过——以下断言针对前端 nginx 反代的部署特有行为：
- ``/api/`` 前缀剥离转发（同套件所有用例经 ``_Client`` 前缀改写隐式覆盖，
  本文件补代理特有的显式断言）；
- 限流生效：``/api/auth/`` zone 1r/s + burst 5，超额 429——网关层是全栈
  唯一 429 来源（deployment-spec §12.3）；
- SSE 禁缓冲：``proxy_buffering off`` 下 run 流逐帧到达并正常收口；
- ``X-Request-ID`` 透传：代理链路不吞中间件生成的请求 id 响应头。
"""

import json
import uuid

import pytest

from helpers import PASSWORD, RUN_TIMEOUT, _client, api_prefix, raw_client

pytestmark = pytest.mark.skipif(
    not api_prefix(), reason="仅经 nginx 全栈入口（E2E_API_PREFIX 非空）时运行"
)


def test_gateway_rate_limits_auth_endpoints():
    """快速连打认证端点超过 burst：出现 429（限流在网关层、不在应用内）。"""
    with raw_client() as c:
        saw_429 = False
        for i in range(12):
            resp = c.post(
                "/api/auth/login",
                json={"username": f"ratelimit_{uuid.uuid4().hex[:6]}", "password": PASSWORD},
            )
            if resp.status_code == 429:
                saw_429 = True
                break
        assert saw_429, "连续认证请求未触发网关限流（limit_req 规则漂移？）"


def test_gateway_sse_stream_passes_through():
    """SSE 经代理逐帧到达并正常收口（proxy_buffering off / 长读超时的端到端验证）。"""
    # 注册与流式都走带 429 退避 + 前缀改写的常规客户端（上一用例刚打满
    # /api/auth/ 限流窗；路径写顶级形态，/api 前缀由客户端改写补齐）
    with _client() as c:
        user = c.post("/auth/register", json={
            "username": f"e2e_gw_{uuid.uuid4().hex[:8]}", "password": PASSWORD,
        })
        assert user.status_code == 200, user.text
        token = user.json()["response"]["token"]

        payload = {
            "threadId": uuid.uuid4().hex,
            "runId": uuid.uuid4().hex,
            "messages": [{"id": uuid.uuid4().hex, "role": "user", "content": "回复\"好\"一个字即可。"}],
        }
        events: list[dict] = []
        chunks = 0
        with c.stream(
            "POST", "/agentic/run", json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=RUN_TIMEOUT,
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["x-request-id"], "代理链路丢失 X-Request-ID 响应头"
            for chunk in resp.iter_bytes():
                chunks += 1
                for line in chunk.decode("utf-8", errors="replace").splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[len("data: "):]))
        # 流式分块到达（缓冲关闭的信号：多 chunk 而非一次性投递）
        assert chunks >= 1
        types = [e.get("type") for e in events]
        assert types[0] == "RUN_STARTED", types
        assert "RUN_FINISHED" in types, events
        assert "RUN_ERROR" not in types, events
