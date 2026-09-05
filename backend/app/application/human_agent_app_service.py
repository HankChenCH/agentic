"""人工客服坐席管理用例：坐席 CRUD（运营维护入口的领域口径）。

坐席是客服智能体「转人工」场景的数据源（components/human_agent 查库读），
写入口收敛在本用例（HTTP 管理面出现前，admin CLI 是唯一消费者）。数据
访问经 ``HumanAgentRepositoryPort``（实现住 adapters/persistence）。
"""

from dataclasses import dataclass
from uuid import UUID

from wireup import injectable

from app.domain.human_agent import HumanAgentRepositoryPort
from app.models.domain.human_agent import HumanAgent, HumanAgentStatus


@injectable
@dataclass
class HumanAgentAppService:
    """坐席管理用例门面（全局资源，无属主作用域）。"""

    repo: HumanAgentRepositoryPort

    def create_agent(
        self, name: str, title: str,
        specialty: str = "", intro: str = "",
        status: HumanAgentStatus = HumanAgentStatus.OFFLINE,
    ) -> HumanAgent:
        return self.repo.create_agent(HumanAgent(
            name=name, title=title, specialty=specialty, intro=intro, status=status,
        ))

    def list_agents(self) -> list[HumanAgent]:
        """全部坐席，在线优先、同级按创建时间。"""
        agents = self.repo.list_agents()
        return sorted(agents, key=lambda a: (a.status != HumanAgentStatus.ONLINE, a.created_at))

    def update_agent(
        self, agent_id: UUID, *,
        name: str | None = None, title: str | None = None,
        specialty: str | None = None, intro: str | None = None,
        status: HumanAgentStatus | None = None,
    ) -> HumanAgent | None:
        """部分更新（仅显式传入字段被修改）；坐席不存在返回 None。"""
        agent = self.repo.get_agent(agent_id)
        if agent is None:
            return None
        for field, value in (
            ("name", name), ("title", title), ("specialty", specialty),
            ("intro", intro), ("status", status),
        ):
            if value is not None:
                setattr(agent, field, value)
        return self.repo.update_agent(agent)

    def delete_agent(self, agent_id: UUID) -> bool:
        return self.repo.delete_agent(agent_id)
