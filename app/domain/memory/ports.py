"""记忆域领域端口（依赖倒置）：协议住领域层，实现由外层回填。

图快照（graph_snapshot.py）需要记忆库的只读面，但领域层不得 import
components——与 ``knowledge/ports.py`` 的 ``AgentCatalog`` 同款解法：
实现住在 ``app/components/memory/graph_reader.py``（内部委托组件自有的
``MemoryRepository``），经 wireup ``@injectable(as_type=...)`` 按本协议
类型注入。依赖箭头 components ──► domain 合法。
"""

from datetime import datetime
from typing import Protocol

from app.models.domain.memory import MemoryEntity, MemoryEpisode, MemoryEpisodeLink, MemoryStatement


class MemoryGraphReader(Protocol):
    """图快照所需的记忆只读投影面。"""

    def list_entities(self, limit: int | None = None) -> list[MemoryEntity]: ...

    def get_entities(self, entity_ids: list[int]) -> dict[int, MemoryEntity]: ...

    def list_active_statements(self, limit: int | None = None) -> list[MemoryStatement]: ...

    def statements_valid_at(
        self, moment: datetime, subject_ids: list[int] | None = None
    ) -> list[MemoryStatement]: ...

    def list_episodes(self, limit: int | None = None) -> list[MemoryEpisode]: ...

    def links_for_episodes(self, episode_ids: list[int]) -> dict[int, list[MemoryEpisodeLink]]: ...
