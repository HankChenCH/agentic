"""事件切片：事件梗概（episode）与参与边（episode_link）的读写与编辑。

事件编辑（L3）的向量同步归 components/memory 的编辑端口，本切片只管行。
"""

from sqlmodel import Session, select
from sqlalchemy.exc import IntegrityError

from app.adapters.persistence.memory_graph_repository._scope import GraphScopeMixin
from app.models.domain.memory import MemoryEpisode, MemoryEpisodeLink


class EpisodeMixin(GraphScopeMixin):
    """事件与参与边的读写；删除事件物理删并先行清空参与边。"""

    def list_episodes(self, limit: int | None = None) -> list[MemoryEpisode]:
        query = select(MemoryEpisode).where(*self._where_user(MemoryEpisode)).order_by(MemoryEpisode.occurred_at.desc())
        if limit is not None:
            query = query.limit(limit)
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def insert_episode(self, episode: MemoryEpisode) -> MemoryEpisode:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(self._stamp_user(episode))
            session.commit()
            session.refresh(episode)
            return episode

    def episodes_by_ids(self, episode_ids: list[int]) -> list[MemoryEpisode]:
        if not episode_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(MemoryEpisode).where(MemoryEpisode.id.in_(episode_ids))
            ).all()
            return [row for row in rows if self._in_scope(row)]

    def link_episode_entities(self, links: list[MemoryEpisodeLink]) -> None:
        if not links:
            return
        with Session(self.engine, expire_on_commit=False) as session:
            session.add_all(links)
            try:
                session.commit()
            except IntegrityError:
                # 重复挂接幂等跳过：整体失败后逐条补插、冲突项丢弃
                session.rollback()
                for link in links:
                    try:
                        session.add(link)
                        session.commit()
                    except IntegrityError:
                        session.rollback()

    def episodes_for_entities(self, entity_ids: list[int], limit: int | None = None) -> list[MemoryEpisode]:
        if not entity_ids:
            return []
        query = (
            select(MemoryEpisode)
            .join(MemoryEpisodeLink, MemoryEpisode.id == MemoryEpisodeLink.episode_id)
            .where(MemoryEpisodeLink.entity_id.in_(entity_ids), *self._where_user(MemoryEpisode))
            .distinct()
            .order_by(MemoryEpisode.occurred_at.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def links_for_episodes(self, episode_ids: list[int]) -> dict[int, list[MemoryEpisodeLink]]:
        if not episode_ids:
            return {}
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(MemoryEpisodeLink).where(MemoryEpisodeLink.episode_id.in_(episode_ids))
            ).all()
            grouped: dict[int, list[MemoryEpisodeLink]] = {}
            for link in rows:
                grouped.setdefault(link.episode_id, []).append(link)
            return grouped

    def episode_links_by_ids(self, link_ids: list[int]) -> list[MemoryEpisodeLink]:
        if not link_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(MemoryEpisodeLink).where(MemoryEpisodeLink.id.in_(link_ids))
            ).all()
            return [row for row in rows if self._link_in_scope(session, row)]

    def get_episode(self, episode_id: int) -> MemoryEpisode | None:
        with Session(self.engine, expire_on_commit=False) as session:
            episode = session.get(MemoryEpisode, episode_id)
            return episode if episode is not None and self._in_scope(episode) else None

    def save_episode(self, episode: MemoryEpisode) -> MemoryEpisode:
        with Session(self.engine, expire_on_commit=False) as session:
            if episode.id is not None:
                existing = session.get(MemoryEpisode, episode.id)
                if existing is not None and not self._in_scope(existing):
                    raise PermissionError(f"episode #{episode.id} 不在当前作用域内")
            merged = session.merge(episode)
            session.commit()
            session.refresh(merged)
            return merged

    def delete_episode(self, episode_id: int) -> MemoryEpisode | None:
        with Session(self.engine, expire_on_commit=False) as session:
            episode = session.get(MemoryEpisode, episode_id)
            if episode is None or not self._in_scope(episode):
                return None
            # 参与边先行：事件删除是物理删（无状态机），参与边随行清空防悬挂
            for link in session.exec(
                select(MemoryEpisodeLink).where(MemoryEpisodeLink.episode_id == episode_id)
            ).all():
                session.delete(link)
            session.delete(episode)
            session.commit()
            return episode

    def get_episode_link(self, link_id: int) -> MemoryEpisodeLink | None:
        with Session(self.engine, expire_on_commit=False) as session:
            link = session.get(MemoryEpisodeLink, link_id)
            return link if link is not None and self._link_in_scope(session, link) else None

    def save_episode_link(self, link: MemoryEpisodeLink) -> MemoryEpisodeLink | None:
        with Session(self.engine, expire_on_commit=False) as session:
            try:
                merged = session.merge(link)
                session.commit()
            except IntegrityError:
                # (episode, entity, role) 组合唯一：改挂撞已有参与 → 3007 由端口翻译
                session.rollback()
                return None
            session.refresh(merged)
            return merged
