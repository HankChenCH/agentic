"""AGENT_REGISTRY 的目录适配：以 as_type 绑定到领域端口 AgentCatalog。

agents→domain 单向合法（依赖箭头表），故协议住领域层（knowledge/ports.py）、
实现住本层。本模块经 services/agents 包扫描自动注册，无需手工登记容器。
"""

from dataclasses import dataclass

from wireup import injectable

from .base import AGENT_REGISTRY
from app.services.domain.knowledge.ports import AgentCatalog


@injectable(as_type=AgentCatalog)
@dataclass
class RegistryAgentCatalog:
    """按注册表现值应答，防止绑出悬空 agent。"""

    def exists(self, agent_id: str) -> bool:
        return agent_id in AGENT_REGISTRY

    def ids(self) -> list[str]:
        return sorted(AGENT_REGISTRY)
