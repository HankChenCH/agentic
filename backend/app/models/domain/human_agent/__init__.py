"""人工客服坐席表模型（全局资源，运营统一维护）。"""

from app.models.domain.human_agent.human_agent import HumanAgent, HumanAgentStatus

__all__ = ["HumanAgent", "HumanAgentStatus"]
