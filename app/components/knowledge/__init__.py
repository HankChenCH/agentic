"""知识库检索能力组件：检索编排 + agent 工具。

拓扑约束（见 server/AGENTS.md「服务层两层制」）：本组件依赖 core /
infrastructures / repositories / models，以及领域层的向量适配器
``app.services.domain.knowledge.vector_index``（components→domain 单向合法）；
严禁 import ``app.services.orchestration`` / ``app.agents`` / ``app.api``。
管理侧（绑定 CRUD、摄取编排）在 ``app/services/domain/knowledge``。
"""

from .service import KnowledgeRetrievalService, RetrievalHit
from .tools import build_knowledge_tools

__all__ = [
    "KnowledgeRetrievalService",
    "RetrievalHit",
    "build_knowledge_tools",
]
