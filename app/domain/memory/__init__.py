"""domain/memory 包：记忆域领域侧服务与端口。

- ``ports.MemoryGraphReader``：图快照只读端口（组件侧回填实现）；
- ``ports.MemoryEditor``：记忆纠错写端口（组件侧回填实现）；
- ``ports.MemoryVectorIndexPort``：记忆向量索引端口（实现住
  ``app/adapters/vector/memory_index.py``），契约数据类型
  （KIND_* / VectorEntry / MemoryVectorHit / vector_object_id）同在 ports；
- ``MemoryGraphService``：管理侧图快照（当前态 / 时点回放）；
- ``MemoryAdminService``：管理侧记忆编辑（L1 事实增/纠正/归档 + 实体改名）。

依赖箭头（AGENTS.md 分层规则）：本包不得 import application / components /
agents / api / adapters。
"""

from .admin_service import MemoryAdminService
from .graph_snapshot import MemoryGraphService
from .ports import (
    KIND_ENTITY,
    KIND_EPISODE,
    KIND_STATEMENT,
    FactWrite,
    MemoryEditor,
    MemoryGraphReader,
    MemoryVectorHit,
    MemoryVectorIndexPort,
    StatementMutation,
    VectorEntry,
    vector_object_id,
)

__all__ = [
    "FactWrite",
    "KIND_ENTITY",
    "KIND_EPISODE",
    "KIND_STATEMENT",
    "MemoryAdminService",
    "MemoryEditor",
    "MemoryGraphReader",
    "MemoryGraphService",
    "MemoryVectorHit",
    "MemoryVectorIndexPort",
    "StatementMutation",
    "VectorEntry",
    "vector_object_id",
]
