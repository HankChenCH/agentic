"""坐席目录门面：人工客服坐席列表的领域口径（排序/契约投影）。"""

from dataclasses import dataclass

from wireup import injectable

from app.domain.human_agent import HumanAgentRepositoryPort
from app.models.domain.human_agent import HumanAgent, HumanAgentStatus


@injectable
@dataclass
class HumanAgentDirectoryService:
    """坐席目录服务：供 manifest 工具与 prompt 片段消费的单一门面（查库 + 展示口径）。"""

    repo: HumanAgentRepositoryPort

    def list_agents(self) -> list[HumanAgent]:
        """全部坐席，在线优先（可接待的排前面，供转接卡片优先展示）。"""
        agents = self.repo.list_agents()
        return sorted(agents, key=lambda a: a.status != HumanAgentStatus.ONLINE)

    def list_digest(self) -> str:
        """坐席清单摘要（prompt 片段用）：在线优先，行内含职务/擅长/状态。

        无坐席返回空串（prompt 侧整节移除）。
        """
        agents = self.list_agents()
        if not agents:
            return ""
        lines = []
        for agent in agents:
            line = f"- {agent.name}（{agent.title}，{agent.status.value}）"
            if agent.specialty:
                line += f" 擅长：{agent.specialty}"
            if agent.intro:
                line += f" — {agent.intro}"
            lines.append(line)
        return "\n".join(lines)
