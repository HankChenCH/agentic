from dataclasses import dataclass

from app.components.knowledge import KnowledgeRetrievalService
from app.components.memory import MemoryService


@dataclass
class AgentToolbox:
    """智能体可装配的能力集：build_tools() 从这里取依赖构造工具。

    新增能力（如知识库检索）时加字段即可，BaseAgent 构造签名不必再变。
    """

    memory: MemoryService
    knowledge: KnowledgeRetrievalService
