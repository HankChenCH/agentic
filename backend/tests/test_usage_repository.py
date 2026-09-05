"""UsageRepository：聚合口径（总量/分维度/按天/分页）与归属隔离。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from conftest import OTHER_USER_ID, TEST_USER_ID
from app.adapters.persistence.usage_repository import UsageRepository
from app.models.domain.usage import UsageRecord


def _row(user_id=TEST_USER_ID, scene="chat", model="deepseek-chat", prompt=10, completion=5,
         thread_id=None, turn_id=None, agentic_id="builtin:demo", created_at=None):
    total = prompt + completion
    return UsageRecord(
        user_id=user_id, scene=scene, model=model,
        input_tokens=prompt, output_tokens=completion, total_tokens=total,
        thread_id=thread_id or uuid4(), turn_id=turn_id or uuid4(),
        agentic_id=agentic_id, created_at=created_at or datetime.now(timezone.utc),
    )


def _repo(engine):
    return UsageRepository(engine=engine)


def test_record_many_roundtrip(engine):
    repo = _repo(engine)
    repo.record_many([_row(prompt=100, completion=50), _row(scene="title", model="deepseek-pro", prompt=7, completion=3)])

    summary = repo.summarize(user_id=TEST_USER_ID, start=None, end=None)
    assert summary.totals.calls == 2
    assert summary.totals.prompt_tokens == 107
    assert summary.totals.completion_tokens == 53
    assert summary.totals.total_tokens == 160


def test_summarize_breaks_down_by_scene_and_model(engine):
    repo = _repo(engine)
    repo.record_many([
        _row(scene="chat", model="deepseek-chat", prompt=10, completion=5),
        _row(scene="chat", model="deepseek-chat", prompt=1, completion=2),
        _row(scene="chat", model="deepseek-pro", prompt=100, completion=50),
        _row(scene="title", model="deepseek-chat", prompt=7, completion=3),
    ])

    summary = repo.summarize(user_id=TEST_USER_ID, start=None, end=None)

    by_scene = {s.key: s for s in summary.by_scene}
    assert set(by_scene) == {"chat", "title"}
    assert by_scene["chat"].calls == 3
    assert by_scene["chat"].total_tokens == 168
    assert by_scene["title"].total_tokens == 10

    by_model = {s.key: s for s in summary.by_model}
    assert set(by_model) == {"deepseek-chat", "deepseek-pro"}
    assert by_model["deepseek-chat"].calls == 3
    assert by_model["deepseek-pro"].prompt_tokens == 100


def test_user_isolation(engine):
    """他人用量不出现在任何读面（个人用量视角的归属过滤在仓储强制）。"""
    repo = _repo(engine)
    repo.record_many([
        _row(user_id=TEST_USER_ID, prompt=10, completion=5),
        _row(user_id=OTHER_USER_ID, prompt=999, completion=999),
    ])

    summary = repo.summarize(user_id=TEST_USER_ID, start=None, end=None)
    assert summary.totals.calls == 1
    assert summary.totals.total_tokens == 15

    daily = repo.daily_series(user_id=TEST_USER_ID, start=None, end=None)
    assert all(p.total_tokens == 15 for p in daily)

    rows, total = repo.list_records(user_id=TEST_USER_ID, start=None, end=None, scene=None, page=1, page_size=10)
    assert total == 1
    assert rows[0].total_tokens == 15


def test_time_range_is_half_open(engine):
    """[start, end) 半开区间：start 当刻计入、end 当刻不计入。"""
    repo = _repo(engine)
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    repo.record_many([
        _row(created_at=base),
        _row(created_at=base + timedelta(days=1)),
        _row(created_at=base + timedelta(days=2)),
    ])

    _, total = repo.list_records(
        user_id=TEST_USER_ID,
        start=base, end=base + timedelta(days=2), scene=None, page=1, page_size=10,
    )
    assert total == 2

    _, total_open = repo.list_records(
        user_id=TEST_USER_ID, start=None, end=base, scene=None, page=1, page_size=10,
    )
    assert total_open == 0


def test_daily_series_buckets_by_utc_day(engine):
    repo = _repo(engine)
    base = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    repo.record_many([
        _row(prompt=10, completion=5, created_at=base),
        _row(prompt=1, completion=2, created_at=base + timedelta(hours=3)),
        _row(prompt=100, completion=50, created_at=base + timedelta(days=1)),
    ])

    daily = repo.daily_series(user_id=TEST_USER_ID, start=None, end=None)

    assert [p.key for p in daily] == ["2026-09-01", "2026-09-02"]
    assert daily[0].calls == 2
    assert daily[0].total_tokens == 18
    assert daily[1].total_tokens == 150


def test_daily_series_scene_filter(engine):
    repo = _repo(engine)
    base = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    repo.record_many([
        _row(scene="chat", prompt=10, completion=5, created_at=base),
        _row(scene="title", prompt=7, completion=3, created_at=base),
    ])

    daily = repo.daily_series(user_id=TEST_USER_ID, start=None, end=None, scene="title")

    assert len(daily) == 1
    assert daily[0].calls == 1
    assert daily[0].total_tokens == 10


def test_list_records_pagination_time_desc(engine):
    repo = _repo(engine)
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    repo.record_many([_row(prompt=i, created_at=base + timedelta(hours=i)) for i in range(5)])

    rows, total = repo.list_records(
        user_id=TEST_USER_ID, start=None, end=None, scene=None, page=2, page_size=2,
    )

    assert total == 5
    assert [r.input_tokens for r in rows] == [2, 1]  # id 倒序 = 时间倒序
