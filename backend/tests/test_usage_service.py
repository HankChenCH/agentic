"""UsageService：记录容错、sink 键族归一、时间范围校验与查询组装。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from conftest import TEST_USER_ID
from app.domain.usage.usage_service import UsageService
from app.domain.usage.ports import UsageSlice, UsageSummary
from app.exceptions import InvalidUsageRangeError
from app.models.domain.usage import UsageRecord


class StubRepo:
    """UsageRepositoryPort 替身：记录/查询都走内存，可注入失败。"""

    def __init__(self, error=None):
        self.rows = []
        self._error = error
        self._summary = None
        self._daily = []
        self._rows, self._total = [], 0

    def record_many(self, records):
        if self._error is not None:
            raise self._error
        self.rows.extend(records)

    def summarize(self, user_id, start, end):
        return self._summary

    def daily_series(self, user_id, start, end, scene=None):
        return self._daily

    def list_records(self, user_id, start, end, scene, page, page_size):
        return self._rows, self._total


def _service(repo=None):
    from conftest import StubLoggerFactory

    return UsageService(repo=repo or StubRepo(), logger_factory=StubLoggerFactory())


def test_record_usage_safe_swallows_storage_failure():
    repo = StubRepo(error=RuntimeError("db down"))
    service = _service(repo)

    # 不上抛（用量是旁路观测数据，记录失败不得阻断主流程）
    service.record_usage_safe([UsageRecord(
        user_id=TEST_USER_ID, scene="chat", model="m",
        input_tokens=1, output_tokens=2, total_tokens=3,
    )])
    assert repo.rows == []


def test_record_usage_safe_skips_empty_batch():
    repo = StubRepo()
    _service(repo).record_usage_safe([])
    assert repo.rows == []


def test_usage_sink_normalizes_and_skips_empty():
    service = _service()
    repo = service.repo
    sink = service.usage_sink(user_id=TEST_USER_ID, scene="memory", thread_id=uuid4(), turn_id=uuid4())

    sink("deepseek-chat", {"input_tokens": 50, "output_tokens": 8})   # usage_metadata 键族 + total 缺省补齐
    sink("deepseek-chat", {})                                          # 空用量：静默忽略
    sink("deepseek-chat", None)                                        # 非法：静默忽略

    assert len(repo.rows) == 1
    row = repo.rows[0]
    assert row.scene == "memory"
    assert row.model == "deepseek-chat"
    assert (row.input_tokens, row.output_tokens, row.total_tokens) == (50, 8, 58)


def test_usage_sink_rejects_unknown_scene():
    with pytest.raises(ValueError):
        _service().usage_sink(user_id=TEST_USER_ID, scene="bogus")


def test_summary_payload_camel_case():
    repo = StubRepo()
    repo._summary = UsageSummary(
        totals=UsageSlice(key="total", calls=3, prompt_tokens=10, completion_tokens=5, total_tokens=15),
        by_scene=[UsageSlice(key="chat", calls=3, prompt_tokens=10, completion_tokens=5, total_tokens=15)],
        by_model=[UsageSlice(key="deepseek-chat", calls=3, prompt_tokens=10, completion_tokens=5, total_tokens=15)],
    )
    payload = _service(repo).summary(user_id=TEST_USER_ID, start=None, end=None)

    assert payload["totals"] == {"key": "total", "calls": 3, "promptTokens": 10, "completionTokens": 5, "totalTokens": 15}
    assert payload["byScene"][0]["key"] == "chat"
    assert payload["byModel"][0]["promptTokens"] == 10


def test_invalid_range_rejected():
    service = _service()
    t = datetime(2026, 9, 1, tzinfo=timezone.utc)
    with pytest.raises(InvalidUsageRangeError):
        service.summary(user_id=TEST_USER_ID, start=t, end=t)
    with pytest.raises(InvalidUsageRangeError):
        service.records(user_id=TEST_USER_ID, start=t + timedelta(days=1), end=t,
                        scene=None, page=1, page_size=20)


def test_naive_range_treated_as_utc():
    """naive 时间参数按 UTC 解释（库内 created_at 恒为 UTC）。"""
    captured = {}

    class CaptureRepo(StubRepo):
        def summarize(self, user_id, start, end):
            captured["start"], captured["end"] = start, end
            return self._summary

    repo = CaptureRepo()
    repo._summary = UsageSummary(
        totals=UsageSlice(key="total"),
        by_scene=[],
        by_model=[],
    )
    service = _service(repo)
    service.summary(
        user_id=TEST_USER_ID,
        start=datetime(2026, 9, 1), end=datetime(2026, 9, 8),
    )

    assert captured["start"].tzinfo is not None
    assert captured["start"].utcoffset().total_seconds() == 0
