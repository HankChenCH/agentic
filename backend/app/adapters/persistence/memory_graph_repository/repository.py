"""图谱仓储本体：``MemoryGraphRepositoryPort`` 的持久化实现（组合各聚合切片）。

复用共享 db 基建注入的 Engine。逐方法短会话（``expire_on_commit=False``）
沿用既有仓储惯例；当前量级下个别全表扫描（别名消歧）可接受，策略升级时
新增实现类换绑即可。

切片布局（同包）：``_scope`` 作用域小件 + entities/statements/episodes/
identity/maintenance 各聚合切片（Mixin 混入，方法体与单文件时代逐字一致）；
本模块只留组合、DI 绑定与作用域视图工厂。
"""

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Engine
from wireup import injectable

from app.adapters.persistence.memory_graph_repository._scope import GraphScopeMixin
from app.adapters.persistence.memory_graph_repository.entities import EntityMixin
from app.adapters.persistence.memory_graph_repository.episodes import EpisodeMixin
from app.adapters.persistence.memory_graph_repository.identity import IdentityMixin
from app.adapters.persistence.memory_graph_repository.maintenance import MaintenanceMixin
from app.adapters.persistence.memory_graph_repository.statements import StatementMixin
from app.domain.memory.ports import MemoryGraphRepositoryPort


@injectable(as_type=MemoryGraphRepositoryPort)
@dataclass
class MemoryGraphRepository(
    EntityMixin,
    StatementMixin,
    EpisodeMixin,
    IdentityMixin,
    MaintenanceMixin,
    GraphScopeMixin,  # 各切片的公共基座，按 C3 须排在切片之后
    MemoryGraphRepositoryPort,  # 端口垫底：抽象桩须让位于各切片的真实现
):
    """关系库承载图谱事实源；引用完整性（FK/唯一约束）由库层兜底。"""

    engine: Engine
    # 作用域标记：不进 __init__（init=False）——wireup 按 __init__ 签名提取依赖，
    # 可选的 UUID 参数会与其注册表校验冲突；单例实例恒为 None（全局算子视角），
    # 作用域视图由 for_user 构造后回填。
    user_id: UUID | None = field(default=None, init=False, compare=False)

    def for_user(self, user_id: UUID) -> "MemoryGraphRepository":
        scoped = MemoryGraphRepository(engine=self.engine)
        scoped.user_id = user_id
        return scoped
