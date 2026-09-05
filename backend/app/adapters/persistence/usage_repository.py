"""用量流水仓储：``UsageRepositoryPort`` 的 SQL 实现。

聚合口径：
- 时间范围半开区间 ``[start, end)``（created_at 过滤，UTC）；
- 按天分桶 ``func.date(created_at)``——SQLite 的 ``date()`` 与 PostgreSQL 的
  ``date(timestamp)`` 双方言通用，日界取 UTC（PG 依赖会话时区，容器部署
  默认 UTC）；
- 归属过滤（user_id）在每条查询条件内强制；
- 列表时间倒序（id 倒序，与 conversation 列表同口径）。
"""

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple
from uuid import UUID

from wireup import injectable
from sqlalchemy import Engine, func
from sqlalchemy import select as sa_select
from sqlmodel import Session, col, select

from app.domain.usage.ports import UsageRepositoryPort, UsageSlice, UsageSummary
from app.models.domain.usage import UsageRecord


@injectable(as_type=UsageRepositoryPort)
@dataclass
class UsageRepository:
    engine: Engine

    def record_many(self, records: Sequence[UsageRecord]) -> None:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add_all(records)
            session.commit()

    def summarize(self, user_id: UUID, start, end) -> UsageSummary:
        with Session(self.engine, expire_on_commit=False) as session:
            by_scene = self._group_sum(session, col(UsageRecord.scene), user_id, start, end)
            by_model = self._group_sum(session, col(UsageRecord.model), user_id, start, end)

        # 总量由任一维度切片累加（两维度同数据源，和恒等）
        totals = UsageSlice(key="total")
        for s in by_scene:
            totals.calls += s.calls
            totals.prompt_tokens += s.prompt_tokens
            totals.completion_tokens += s.completion_tokens
            totals.total_tokens += s.total_tokens
        return UsageSummary(totals=totals, by_scene=by_scene, by_model=by_model)

    def daily_series(self, user_id: UUID, start, end, scene: Optional[str] = None) -> List[UsageSlice]:
        day = func.date(col(UsageRecord.created_at)).label("day")
        conditions = [UsageRecord.user_id == user_id, *_range_conditions(start, end)]
        if scene:
            conditions.append(UsageRecord.scene == scene)
        with Session(self.engine, expire_on_commit=False) as session:
            # 多列裸聚合 select 用原生 execute（session.exec 对其行解包不可靠）
            rows = session.execute(
                sa_select(
                    day,
                    func.count().label("calls"),
                    _sum(UsageRecord.input_tokens),
                    _sum(UsageRecord.output_tokens),
                    _sum(UsageRecord.total_tokens),
                )
                .where(*conditions)
                .group_by(day)
                .order_by(day.asc())
            ).all()
        return [
            UsageSlice(
                key=str(row[0]),
                calls=int(row[1]),
                prompt_tokens=int(row[2]),
                completion_tokens=int(row[3]),
                total_tokens=int(row[4]),
            )
            for row in rows
        ]

    def list_records(
        self, user_id: UUID, start, end, scene: Optional[str], page: int, page_size: int
    ) -> Tuple[List[UsageRecord], int]:
        conditions = [UsageRecord.user_id == user_id, *_range_conditions(start, end)]
        if scene:
            conditions.append(UsageRecord.scene == scene)
        offset = (page - 1) * page_size
        with Session(self.engine, expire_on_commit=False) as session:
            total = session.exec(
                select(func.count()).select_from(UsageRecord).where(*conditions)
            ).one()
            rows = session.exec(
                select(UsageRecord)
                .where(*conditions)
                .offset(offset)
                .limit(page_size)
                .order_by(col(UsageRecord.id).desc())
            ).all()
            session.commit()
        return list(rows), int(total)

    # ------------------------------------------------------------------
    def _group_sum(self, session, key_col, user_id: UUID, start, end) -> List[UsageSlice]:
        """按 key_col（scene/model）分组求和小计，调用次数降序、次序稳定。"""
        rows = session.execute(
            sa_select(
                key_col.label("key"),
                func.count().label("calls"),
                _sum(UsageRecord.input_tokens),
                _sum(UsageRecord.output_tokens),
                _sum(UsageRecord.total_tokens),
            )
            .where(UsageRecord.user_id == user_id, *_range_conditions(start, end))
            .group_by(key_col)
            .order_by(func.count().desc(), key_col)
        ).all()
        return [
            UsageSlice(
                key=str(row[0]),
                calls=int(row[1]),
                prompt_tokens=int(row[2]),
                completion_tokens=int(row[3]),
                total_tokens=int(row[4]),
            )
            for row in rows
        ]


def _range_conditions(start, end) -> list:
    """半开区间 [start, end) 的 created_at 过滤条件。"""
    conditions = []
    if start is not None:
        conditions.append(col(UsageRecord.created_at) >= start)
    if end is not None:
        conditions.append(col(UsageRecord.created_at) < end)
    return conditions


def _sum(column):
    return func.coalesce(func.sum(column), 0)
