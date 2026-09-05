"""用量统计域服务：记录的容错策略 + 个人用量查询的唯一归属。

记录侧（record_usage_safe / usage_sink）：用量是主流程的旁路观测数据，
**任何记录失败都不得阻断调用方**（与 cancel flag 的宽容策略同口径）——
落库失败只记警告日志。捕获点（编排层 StorageTranslator 流水、标题生成、
记忆巩固包装器）一律经本服务写入，不直触仓储端口。

查询侧（summary/daily/records）：个人用量视角——user_id 一律来自调用方
（api 层的 UserPrincipal），时间范围校验（非法抛 InvalidUsageRangeError，
错误码 6xxx）在此强制。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional
from uuid import UUID

from wireup import injectable

from app.core.logging import LoggerFactory
from app.exceptions import InvalidUsageRangeError

from app.models.domain.usage import UsageRecord, UsageScene

from .extract import normalize_usage
from .ports import UsageRepositoryPort, UsageSlice, UsageSummary


@injectable
@dataclass
class UsageService:
    """用量记录与个人用量查询的门面。"""

    repo: UsageRepositoryPort
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    # ---------------- 写路径（旁路观测，永不阻断主流程） ----------------

    def record_usage_safe(self, records: List[UsageRecord]) -> None:
        """批量落库用量流水：失败只记警告，不上抛（宽容策略归领域）。"""
        if not records:
            return
        try:
            self.repo.record_many(records)
        except Exception:
            self.logger.warning("用量流水落库失败（忽略，不影响主流程），rows=%d", len(records), exc_info=True)

    def usage_sink(
        self,
        *,
        user_id: UUID,
        scene: str,
        thread_id: Optional[UUID] = None,
        turn_id: Optional[UUID] = None,
    ) -> Callable[[str, dict], None]:
        """产出一个 ``sink(model_name, usage_dict)`` 回调：包装器/裸调用点把
        单次调用的用量交它落库（空用量静默忽略，不产生零值行）。"""
        if scene not in UsageScene.ALL:
            raise ValueError(f"unknown usage scene: {scene!r}, expected one of {UsageScene.ALL}")

        def sink(model_name: str, usage: dict) -> None:
            normalized = _normalized_or_none(usage)
            if normalized is None:
                return
            self.record_usage_safe([UsageRecord(
                user_id=user_id,
                scene=scene,
                model=model_name or "unknown",
                input_tokens=normalized["prompt_tokens"],
                output_tokens=normalized["completion_tokens"],
                total_tokens=normalized["total_tokens"],
                thread_id=thread_id,
                turn_id=turn_id,
            )])

        return sink

    # ---------------- 读路径（个人用量，管理侧读口径） ----------------

    def summary(self, user_id: UUID, start: Optional[datetime], end: Optional[datetime]) -> dict:
        """区间总量 + 按场景/按模型分布（camelCase 载荷，/stats 端点直出）。"""
        start, end = _normalize_range(start, end)
        result: UsageSummary = self.repo.summarize(user_id=user_id, start=start, end=end)
        return {
            "totals": _slice_payload(result.totals),
            "byScene": [_slice_payload(s) for s in result.by_scene],
            "byModel": [_slice_payload(s) for s in result.by_model],
        }

    def daily(self, user_id: UUID, start: Optional[datetime], end: Optional[datetime],
              scene: Optional[str] = None) -> dict:
        """按天时间序列（UTC 日分桶，日期升序补齐空洞由前端处理）。"""
        start, end = _normalize_range(start, end)
        points = self.repo.daily_series(user_id=user_id, start=start, end=end, scene=scene)
        return {"items": [_slice_payload(p) for p in points]}

    def records(self, user_id: UUID, start: Optional[datetime], end: Optional[datetime],
                scene: Optional[str], page: int, page_size: int) -> dict:
        """用量流水分页（时间倒序），行载荷 camelCase。"""
        start, end = _normalize_range(start, end)
        rows, total = self.repo.list_records(
            user_id=user_id, start=start, end=end, scene=scene, page=page, page_size=page_size,
        )
        return {
            "items": [_record_payload(r) for r in rows],
            "total": total,
            "page": page,
            "pageSize": page_size,
        }


def _normalized_or_none(usage: dict) -> Optional[dict]:
    """容错包装：调用方传来的用量可能是未归一的键族/空值——归一失败按无用量。"""
    try:
        return normalize_usage(usage) or None
    except (TypeError, ValueError):
        return None


def _normalize_range(start: Optional[datetime], end: Optional[datetime]) -> tuple:
    """时间范围校验与归一：naive 视为 UTC；半开区间 [start, end)；start > end 抛 6001。"""
    def _utc(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    start, end = _utc(start), _utc(end)
    if start is not None and end is not None and start >= end:
        raise InvalidUsageRangeError("usage time range is invalid: start must be earlier than end")
    return start, end


def _slice_payload(s: UsageSlice) -> dict:
    return {
        "key": s.key,
        "calls": s.calls,
        "promptTokens": s.prompt_tokens,
        "completionTokens": s.completion_tokens,
        "totalTokens": s.total_tokens,
    }


def _record_payload(r: UsageRecord) -> dict:
    return {
        "id": r.id,
        "scene": r.scene,
        "model": r.model,
        "promptTokens": r.input_tokens,
        "completionTokens": r.output_tokens,
        "totalTokens": r.total_tokens,
        "threadId": str(r.thread_id) if r.thread_id else None,
        "turnId": str(r.turn_id) if r.turn_id else None,
        "agenticId": r.agentic_id,
        "createdAt": r.created_at.isoformat() if r.created_at else None,
    }
