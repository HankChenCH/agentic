"""MemoryGraphReader 端口的组件侧回填：薄委托到组件自有仓储。

绑定 ``@injectable(as_type=MemoryGraphReader)``（协议定义在
``services/domain/memory/ports.py``，依赖箭头 components ──► domain 合法）。
存在的唯一意义是把「领域层拿得到只读面」与「数据访问收敛在组件仓储」
两个约束同时满足——不复制任何查询逻辑。
"""

from datetime import datetime
from dataclasses import dataclass

from wireup import injectable

from app.components.memory.repositories import MemoryRepository
from app.models.domain.memory import MemoryEntity, MemoryEpisode, MemoryEpisodeLink, MemoryStatement
from app.services.domain.memory.ports import MemoryGraphReader


@injectable(as_type=MemoryGraphReader)
@dataclass
class MemoryRepositoryGraphReader:
    memory_repo: MemoryRepository

    def list_entities(self, limit: int | None = None) -> list[MemoryEntity]:
        return self.memory_repo.list_entities(limit)

    def get_entities(self, entity_ids: list[int]) -> dict[int, MemoryEntity]:
        return self.memory_repo.get_entities(entity_ids)

    def list_active_statements(self, limit: int | None = None) -> list[MemoryStatement]:
        return self.memory_repo.list_active_statements(limit)

    def statements_valid_at(
        self, moment: datetime, subject_ids: list[int] | None = None
    ) -> list[MemoryStatement]:
        return self.memory_repo.statements_valid_at(moment, subject_ids)

    def list_episodes(self, limit: int | None = None) -> list[MemoryEpisode]:
        return self.memory_repo.list_episodes(limit)

    def links_for_episodes(self, episode_ids: list[int]) -> dict[int, list[MemoryEpisodeLink]]:
        return self.memory_repo.links_for_episodes(episode_ids)
