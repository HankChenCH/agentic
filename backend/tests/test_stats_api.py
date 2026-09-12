"""用量统计端点（个人视角）的参数口径集成测试：区间半开语义、UTC 日分桶、
场景过滤、分页。

种子数据直接写 usage_record 表（计量流水在 run 链路由真实 LLM usage_metadata
产生，替身链路不产出——直接种行等价且确定）。汇总/序列/流水的组合口径见
test_usage_service.py（领域级）；本文件锁 HTTP 层的参数透传与响应形态。
"""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from sqlmodel import Session, create_engine

import http_test_kit  # noqa: F401  必须先于 app.* 导入（env 就位）
from http_test_kit import (  # noqa: E402
    build_app,
    new_username,
    register_and_login,
    http_client_for,
)

from app.models.domain.usage import UsageRecord  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with http_client_for(build_app()) as test_client:
        yield test_client


def _engine():
    return create_engine(f"sqlite:///{__import__('os').environ['SQLITE_DB_PATH']}")


def _seed(user_id: UUID, *, scene: str, model: str, day: str, prompt: int, completion: int, thread=None):
    record = UsageRecord(
        user_id=user_id, scene=scene, model=model,
        input_tokens=prompt, output_tokens=completion, total_tokens=prompt + completion,
        created_at=datetime.fromisoformat(day).replace(tzinfo=timezone.utc),
        thread_id=thread,
    )
    with Session(_engine()) as session:
        session.add(record)
        session.commit()


def _user_id(client, headers) -> UUID:
    return UUID(client.get("/auth/me", headers=headers).json()["response"]["id"])


def test_summary_totals_and_breakdowns(client):
    headers, _ = register_and_login(client, new_username("stats"))
    user_id = _user_id(client, headers)
    _seed(user_id, scene="chat", model="deepseek-chat", day="2026-09-01T10:00:00", prompt=100, completion=50)
    _seed(user_id, scene="chat", model="deepseek-chat", day="2026-09-02T10:00:00", prompt=10, completion=5)
    _seed(user_id, scene="title", model="deepseek-chat", day="2026-09-02T11:00:00", prompt=7, completion=3)
    # 他人数据不进本人视角
    other_headers, _ = register_and_login(client, new_username("stats"))
    _seed(_user_id(client, other_headers), scene="chat", model="deepseek-chat",
          day="2026-09-02T12:00:00", prompt=999, completion=999)

    resp = client.get("/stats/usage/summary", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()["response"]
    assert body["totals"]["calls"] == 3
    assert body["totals"]["promptTokens"] == 117
    assert body["totals"]["completionTokens"] == 58
    assert body["totals"]["totalTokens"] == 175
    by_scene = {row["key"]: row["calls"] for row in body["byScene"]}
    assert by_scene == {"chat": 2, "title": 1}
    by_model = {row["key"]: row["totalTokens"] for row in body["byModel"]}
    assert by_model == {"deepseek-chat": 175}


def test_summary_half_open_interval(client):
    """半开区间 [start, end)：start 时刻的数据计入，end 时刻的不计。"""
    headers, _ = register_and_login(client, new_username("stats"))
    user_id = _user_id(client, headers)
    _seed(user_id, scene="chat", model="m", day="2026-09-02T00:00:00", prompt=1, completion=1)
    _seed(user_id, scene="chat", model="m", day="2026-09-03T00:00:00", prompt=2, completion=2)

    resp = client.get("/stats/usage/summary", headers=headers, params={
        "start": "2026-09-02T00:00:00Z", "end": "2026-09-03T00:00:00Z",
    })
    body = resp.json()["response"]
    assert body["totals"]["calls"] == 1
    assert body["totals"]["promptTokens"] == 1


def test_daily_buckets_by_utc_day_ascending(client):
    headers, _ = register_and_login(client, new_username("stats"))
    user_id = _user_id(client, headers)
    _seed(user_id, scene="chat", model="m", day="2026-09-03T00:00:00", prompt=2, completion=2)
    _seed(user_id, scene="chat", model="m", day="2026-09-01T23:00:00", prompt=1, completion=1)
    _seed(user_id, scene="title", model="m", day="2026-09-03T12:00:00", prompt=5, completion=5)

    resp = client.get("/stats/usage/daily", headers=headers)
    items = resp.json()["response"]["items"]
    assert [row["key"] for row in items] == ["2026-09-01", "2026-09-03"]

    # 场景过滤：只看 title
    resp = client.get("/stats/usage/daily", headers=headers, params={"scene": "title"})
    items = resp.json()["response"]["items"]
    assert len(items) == 1 and items[0]["key"] == "2026-09-03"


def test_records_pagination_and_ordering(client):
    headers, _ = register_and_login(client, new_username("stats"))
    user_id = _user_id(client, headers)
    for i in range(3):
        _seed(user_id, scene="chat", model=f"m{i}", day=f"2026-09-0{i + 1}T08:00:00",
              prompt=i, completion=i)

    # 时间倒序（新→旧）
    resp = client.get("/stats/usage/records", headers=headers)
    body = resp.json()["response"]
    assert body["total"] == 3
    assert [row["model"] for row in body["items"]] == ["m2", "m1", "m0"]

    # 分页：page=2、pageSize=2 → 只剩最旧一条
    resp = client.get("/stats/usage/records", headers=headers,
                      params={"page": 2, "pageSize": 2})
    body = resp.json()["response"]
    assert body["page"] == 2
    assert [row["model"] for row in body["items"]] == ["m0"]


def test_invalid_range_rejected_before_query(client):
    headers, _ = register_and_login(client, new_username("stats"))
    resp = client.get("/stats/usage/daily", headers=headers, params={
        "start": "2026-09-02T00:00:00Z", "end": "2026-09-01T00:00:00Z",
    })
    assert resp.status_code == 400 and resp.json()["error_code"] == 6001


def test_records_isolated_per_user(client):
    headers_a, _ = register_and_login(client, new_username("stats"))
    _seed(_user_id(client, headers_a), scene="chat", model="a-model",
          day="2026-09-02T08:00:00", prompt=1, completion=1)

    headers_b, _ = register_and_login(client, new_username("stats"))
    body = client.get("/stats/usage/records", headers=headers_b).json()["response"]
    assert body["total"] == 0 and body["items"] == []
