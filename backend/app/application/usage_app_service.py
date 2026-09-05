"""用量统计用例（个人用量视角）：汇总 / 按天序列 / 流水分页。

api 层唯一消费面；区间语义（半开 [start, end)、UTC 日分桶）与归属圈定
在 domain/usage。
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from wireup import injectable

from app.domain.usage import UsageService


@injectable
@dataclass
class UsageAppService:
    """用量统计用例门面。"""

    usage_service: UsageService

    def summary(self, user_id: UUID, start: datetime | None, end: datetime | None) -> dict:
        return self.usage_service.summary(user_id=user_id, start=start, end=end)

    def daily(
        self, user_id: UUID, start: datetime | None, end: datetime | None, scene: str | None
    ) -> dict:
        return self.usage_service.daily(user_id=user_id, start=start, end=end, scene=scene)

    def records(
        self, user_id: UUID, start: datetime | None, end: datetime | None,
        scene: str | None, page: int, page_size: int,
    ) -> dict:
        return self.usage_service.records(
            user_id=user_id, start=start, end=end, scene=scene, page=page, page_size=page_size,
        )
