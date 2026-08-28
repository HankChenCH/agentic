"""domain/memory 包：记忆域领域侧服务。

- ``ports.MemoryGraphReader``：图快照只读端口（组件侧回填实现）；
- ``MemoryGraphService``：管理侧图快照（当前态 / 时点回放）；
- ``MemoryVectorIndex``：记忆向量适配器（归位领域层，components→domain 合法边）。

依赖箭头（AGENTS.md 服务层两层制）：本包不得 import components /
orchestration / agents / api。
"""

from .graph_snapshot import MemoryGraphService
from .ports import MemoryGraphReader
from .vector_index import (
    KIND_ENTITY,
    KIND_EPISODE,
    KIND_STATEMENT,
    MemoryVectorHit,
    MemoryVectorIndex,
    VectorEntry,
    vector_object_id,
)

__all__ = [
    "MemoryGraphReader",
    "MemoryGraphService",
    "KIND_ENTITY",
    "KIND_EPISODE",
    "KIND_STATEMENT",
    "MemoryVectorHit",
    "MemoryVectorIndex",
    "VectorEntry",
    "vector_object_id",
]
