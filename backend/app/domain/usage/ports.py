"""用量统计域端口（依赖倒置）：协议住领域层，实现由 adapters 回填。

仓储协议（``UsageRepositoryPort``）的实现住
``app/adapters/persistence/usage_repository.py``——机制（SQLModel 会话、
SQL 方言聚合）不进 domain，归属过滤（user_id）在实现查询条件内强制，
与既有仓储同一口径。
"""

from dataclasses import dataclass
from typing import List, Protocol, Sequence, Tuple
from uuid import UUID

from app.models.domain.usage import UsageRecord


@dataclass
class UsageSlice:
    """一个分组切片的用量小计（总量 / 按场景 / 按模型 / 按天分桶共用同形）。"""

    key: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class UsageSummary:
    """汇总视图：总量 + 两个维度的分布切片。"""

    totals: UsageSlice
    by_scene: List[UsageSlice]
    by_model: List[UsageSlice]


class UsageRepositoryPort(Protocol):
    """用量流水数据访问协议（归属过滤在实现查询条件内强制）。

    时间范围语义统一为半开区间 ``[start, end)``：start 闭、end 开——
    ``end=None`` 表示不设上界；分桶/排序口径见实现。
    """

    def record_many(self, records: Sequence[UsageRecord]) -> None: ...

    def summarize(self, user_id: UUID, start, end) -> UsageSummary: ...

    def daily_series(self, user_id: UUID, start, end, scene: str | None = None) -> List[UsageSlice]: ...

    def list_records(
        self, user_id: UUID, start, end, scene: str | None, page: int, page_size: int
    ) -> Tuple[List[UsageRecord], int]: ...
