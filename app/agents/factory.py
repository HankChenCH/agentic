from dataclasses import dataclass

from wireup import injectable

from app.agents.base import AGENT_REGISTRY, BaseAgent
from app.agents.toolbox import AgentToolbox
from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.infrastructures.llm import ModelFactory
from app.components.knowledge import KnowledgeRetrievalService
from app.components.memory import MemoryService

import app.agents.builtin  # noqa: F401  触发内置智能体的 @register_agent 注册


@injectable
@dataclass
class AgentFactory:
    """智能体工厂：按 agentic_id 创建并缓存智能体实例。

    未指定或未注册的 id（含库中历史遗留拼写）→ 回退默认智能体（日志警告）；
    模型解析顺序：智能体偏好（preferred_provider，即 provider entry key）→ 全局默认。

    智能体的 prompt/tools 为构建期静态，图在实例化时编译一次；实例按 agentic_id
    缓存复用（langgraph 图支持以不同 thread_id 并发运行），多次 create 不重复建图。
    """

    model_factory: ModelFactory
    app_config: AppConfig
    memory: MemoryService
    knowledge: KnowledgeRetrievalService
    logger_factory: LoggerFactory

    def __post_init__(self):
        self._agent_cache: dict[str, BaseAgent] = {}
        self.logger = self.logger_factory.get_logger(__name__)

    @property
    def default_agentic_id(self) -> str:
        return self.app_config.default_agentic_id

    def create(self, agentic_id: str | None = None) -> BaseAgent:
        agent_cls = self._resolve(agentic_id)
        if agent_cls.agentic_id not in self._agent_cache:
            model = self.model_factory.create(agent_cls.preferred_provider)
            self._agent_cache[agent_cls.agentic_id] = agent_cls(
                model=model,
                toolbox=AgentToolbox(memory=self.memory, knowledge=self.knowledge),
            )
        return self._agent_cache[agent_cls.agentic_id]

    def _resolve(self, agentic_id: str | None) -> type[BaseAgent]:
        agent_id = agentic_id if agentic_id is not None else self.default_agentic_id
        agent_cls = AGENT_REGISTRY.get(agent_id)
        if agent_cls is not None:
            return agent_cls

        # 未注册：回退默认；默认也未注册才视为配置错误
        self.logger.warning("agent not registered: %s, fallback to default: %s", agent_id, self.default_agentic_id)
        default_cls = AGENT_REGISTRY.get(self.default_agentic_id)
        if default_cls is None:
            raise ValueError(
                f"default agent not registered: {self.default_agentic_id}, registered: {list(AGENT_REGISTRY)}"
            )
        return default_cls
