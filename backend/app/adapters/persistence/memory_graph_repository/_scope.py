"""图谱仓储的作用域小件：user_id 状态契约 + 过滤原语。

各聚合切片（entities/statements/episodes/identity/maintenance）混入本
Mixin 共享作用域强制——``user_id is None`` 即全局算子视角（仅维护链路）；
作用域视图内 id 直取命中他人行视为不存在、新行归属钉死、复合纠错先验
范围成员，跨用户访问不留旁路。
"""

from uuid import UUID

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.models.domain.memory import MemoryEpisode, MemoryEpisodeLink


class GraphScopeMixin:
    """作用域状态与过滤原语；``engine``/``user_id`` 由组合类（仓储本体）持有。"""

    engine: Engine
    user_id: UUID | None

    def _where_user(self, model):
        """作用域过滤条件；全局视角返回空列表（由调用方 * 解包）。

        episode_link 无 user_id 列（归属经 episode），以子查询限定在本用户的
        episode 集合内。
        """
        if self.user_id is None:
            return []
        if model is MemoryEpisodeLink:
            return [
                model.episode_id.in_(
                    select(MemoryEpisode.id).where(MemoryEpisode.user_id == self.user_id)
                )
            ]
        return [model.user_id == self.user_id]

    def _in_scope(self, row) -> bool:
        """id 直取结果的归属校验：全局视角恒 True，作用域内他人行视为不存在。"""
        return self.user_id is None or getattr(row, "user_id", None) == self.user_id

    def _stamp_user(self, row):
        """新行归属钉死到作用域用户（全局视角不改动——维护链路自行负责）。"""
        if self.user_id is not None:
            row.user_id = self.user_id
        return row

    def _link_in_scope(self, session: Session, link: MemoryEpisodeLink) -> bool:
        """参与边归属经其 episode 判定（link 表自身无 user_id 列）。"""
        if self.user_id is None:
            return True
        episode = session.get(MemoryEpisode, link.episode_id)
        return episode is not None and episode.user_id == self.user_id
