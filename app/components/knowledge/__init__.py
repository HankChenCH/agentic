"""知识库检索能力组件：向量索引适配器 + 检索编排 + agent 工具。

拓扑约束：本组件只依赖 core / infrastructures / repositories / models，
严禁 import ``app.services`` / ``app.agents``。管理侧（绑定 CRUD、
摄取编排）在 ``app/services/domain/knowledge``，经本组件访问向量库；
依赖箭头表见 server/AGENTS.md「服务层两层制」。
"""

from .service import KnowledgeRetrievalService, RetrievalHit
from .tools import build_knowledge_tools
from .vector_index import DEFAULT_TOP_K, KnowledgeVectorIndex, VectorHit

__all__ = [
    "KnowledgeVectorIndex",
    "VectorHit",
    "DEFAULT_TOP_K",
    "KnowledgeRetrievalService",
    "RetrievalHit",
    "build_knowledge_tools",
]
