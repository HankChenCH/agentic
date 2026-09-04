from dataclasses import dataclass

from wireup import injectable

from app.agents.base import AGENT_REGISTRY, BaseAgent
from app.agents.toolbox import AgentToolbox
from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.adapters.llm import ModelFactory

import app.agents.builtin  # noqa: F401  触发内置智能体的 @register_agent 注册


@injectable
@dataclass
class AgentFactory:
    """智能体工厂：按 agentic_id 创建并缓存智能体实例。

    未指定或未注册的 id（含库中历史遗留拼写）→ 回退默认智能体（日志警告）；
    模型解析顺序：智能体偏好（preferred_provider，即 provider entry key）→ 全局默认。

    智能体的 prompt/tools 为构建期静态，图在实例化时编译一次；实例按 agentic_id
    缓存复用（langgraph 图支持以不同 thread_id 并发运行），多次 create 不重复建图。

    组件能力经注入的 ``AgentToolbox`` 获取（组件装配在 toolbox 完成，本类
    不感知具体组件）。
    """

    model_factory: ModelFactory
    app_config: AppConfig
    toolbox: AgentToolbox
    logger_factory: LoggerFactory

    def __post_init__(self):
        self._agent_cache: dict[str, BaseAgent] = {}
        self.logger = self.logger_factory.get_logger(__name__)

    @property
    def default_agentic_id(self) -> str:
        return self.app_config.default_agentic_id

    def is_registered(self, agentic_id: str) -> bool:
        """id 是否已注册（只查注册表，不触发实例化）。"""
        return agentic_id in AGENT_REGISTRY

    def registered_ids(self) -> list[str]:
        """已注册智能体 id 清单（注册表顺序，供目录类消费方枚举）。"""
        return list(AGENT_REGISTRY)

    def agent_class(self, agentic_id: str) -> type[BaseAgent] | None:
        """取注册类（不实例化）；未注册返回 None。"""
        return AGENT_REGISTRY.get(agentic_id)

    def create(self, agentic_id: str | None = None) -> BaseAgent:
        agent_cls = self._resolve(agentic_id)
        if agent_cls.agentic_id not in self._agent_cache:
            model = self.model_factory.create(agent_cls.preferred_provider)
            self._agent_cache[agent_cls.agentic_id] = agent_cls(
                model=model,
                toolbox=self.toolbox,
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
