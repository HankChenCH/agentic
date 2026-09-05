"""运维监控指标测试：/metrics 端点 + HTTP 请求指标中间件。

纯单元级：中间件挂最小 FastAPI 应用，不经 wireup 容器；指标为全局
REGISTRY 单例，计数断言用「先清理 samples 再请求」避免跨用例串扰。
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.metrics import HTTP_REQUESTS_TOTAL, MetricsMiddleware, router as metrics_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(metrics_router)

    @app.get("/demo/{item_id}")
    def demo(item_id: int):
        return {"item_id": item_id}

    app.add_middleware(MetricsMiddleware)
    return app


def _count_requests(labels: dict[str, str]) -> float:
    total = 0.0
    for metric in HTTP_REQUESTS_TOTAL.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total") and sample.labels == labels:
                total = sample.value
    return total


def test_metrics_endpoint_serves_exposition():
    with TestClient(_make_app()) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "agentic_http_requests_total" in body


def test_middleware_counts_route_template_and_status():
    client = TestClient(_make_app())
    client.get("/demo/1")
    client.get("/demo/2")
    # handler 为路由模板而非原始 path——两个请求落同一标签且状态 200
    assert _count_requests({"method": "GET", "handler": "/demo/{item_id}", "status": "200"}) == 2


def test_middleware_records_unmatched_for_404():
    client = TestClient(_make_app())
    client.get("/no-such-route")
    assert _count_requests({"method": "GET", "handler": "unmatched", "status": "404"}) == 1


def test_middleware_skips_metrics_endpoint_itself():
    client = TestClient(_make_app())
    client.get("/metrics")
    assert _count_requests({"method": "GET", "handler": "/metrics", "status": "200"}) == 0
