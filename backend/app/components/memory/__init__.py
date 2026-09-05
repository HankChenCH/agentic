"""长期记忆组件（v2 双层图谱）。

组件解剖学（范式见 app.components.base 与包 __init__ docstring）：
manifest.py 声明能力并装配工具，ability/ 持能力门面服务，internal/ 是
跨能力共享机件（app 层禁入），admin.py 是管理面端口实现，repositories/
是组件自有存储策略。
"""

from .ability.consolidation import MemoryConsolidationService, USER_ENTITY_NAME
from .ability.recall import MemoryRecallService
from .ability.user_node import UserNodeSyncService
from .manifest import MemoryComponent

__all__ = [
    "MemoryConsolidationService",
    "MemoryRecallService",
    "MemoryComponent",
    "UserNodeSyncService",
    "USER_ENTITY_NAME",
]
