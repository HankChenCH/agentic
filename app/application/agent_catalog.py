"""智能体目录：把注册表（AGENT_REGISTRY）的展示元数据暴露给前端选择 UI。

为什么在编排层包一层：依赖箭头表（AST 强制）禁止 api 直触 app.agents，
与 ``ToolCatalogService`` 同理——api 只消费 orchestration/domain。

``supportsVision`` 需要模型实例上的 capabilities 声明（ClassVar 不可得），
故经 ``AgentFactory.create`` 实例读取（工厂按 id 缓存实例，一次构建后复用）；
单个智能体构建失败降级为 ``false`` 并记日志——目录端点不因个别智能体的
配置问题整体 500。
"""

from dataclasses import dataclass
from typing import Any

from wireup import injectable

from app.agents.factory import AgentFactory
from app.core.logging import LoggerFactory


@injectable
@dataclass
class AgentCatalogService:
    """智能体目录服务：``describe()`` 返回全量注册表的智能体清单。"""

    agent_factory: AgentFactory
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def describe(self) -> dict[str, Any]:
        default_id = self.agent_factory.default_agentic_id
        agents = []
        for agent_id in self.agent_factory.registered_ids():
            agent_cls = self.agent_factory.agent_class(agent_id)
            agents.append({
                "id": agent_id,
                "name": agent_cls.display_name,
                "description": agent_cls.description,
                "supportsVision": self._supports_vision(agent_id),
            })
        # 默认智能体排首位（前端选择 UI 的缺省项）
        agents.sort(key=lambda item: item["id"] != default_id)
        return {"defaultAgentId": default_id, "agents": agents}

    def _supports_vision(self, agent_id: str) -> bool:
        try:
            return self.agent_factory.create(agent_id).supports_vision
        except Exception:
            self.logger.exception("读取智能体图片能力失败，按不支持降级 agent_id=%s", agent_id)
            return False
