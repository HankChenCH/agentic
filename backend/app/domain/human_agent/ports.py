"""人工客服坐席域端口（依赖倒置）：协议住领域层，实现由 adapters 回填。

仓储协议（``HumanAgentRepositoryPort``）的实现住 ``app/adapters/persistence/``；
领域服务与组件只依赖本文件的协议面，wireup 经 ``@injectable(as_type=...)``
按协议类型注入。
"""

from typing import List, Protocol
from uuid import UUID

from app.models.domain.human_agent import HumanAgent


class HumanAgentRepositoryPort(Protocol):
    """人工客服坐席聚合数据访问协议（全局资源，无属主作用域）。"""

    def create_agent(self, agent: HumanAgent) -> HumanAgent: ...

    def get_agent(self, agent_id: UUID) -> HumanAgent | None: ...

    def get_agent_by_name(self, name: str) -> HumanAgent | None: ...

    def update_agent(self, agent: HumanAgent) -> HumanAgent: ...

    def delete_agent(self, agent_id: UUID) -> bool: ...

    def list_agents(self) -> List[HumanAgent]: ...
