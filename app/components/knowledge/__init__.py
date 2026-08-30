"""知识库检索能力组件。

组件解剖学（范式见 app.components.base 与包 __init__ docstring）：
manifest.py 声明能力（knowledge_list / knowledge_search 双工具 + 前端
契约模型）并装配工具，ability/retrieval.py 持检索门面服务。无自有存储
（复用共享 repositories 层）与无管理面实现，故无 repositories/ 与
admin.py。

拓扑约束（见 server/AGENTS.md「服务层两层制」）：本组件依赖 core /
infrastructures / repositories / models，以及领域层的向量适配器
``app.services.domain.knowledge.vector_index``（components→domain 单向合法）；
严禁 import ``app.services.orchestration`` / ``app.agents`` / ``app.api``。
管理侧（绑定 CRUD、摄取编排）在 ``app/services/domain/knowledge``。
"""

from .ability.retrieval import KnowledgeRetrievalService, RetrievalHit
from .manifest import KnowledgeComponent, KnowledgeSearchArgs, KnowledgeSearchResult, KnowledgeSource

__all__ = [
    "KnowledgeRetrievalService",
    "RetrievalHit",
    "KnowledgeComponent",
    "KnowledgeSearchArgs",
    "KnowledgeSearchResult",
    "KnowledgeSource",
]
