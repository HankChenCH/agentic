"""陈述切片：双时间轴事实行的读写与状态机迁移（ACTIVE/SUPERSEDED/ARCHIVED）。

覆盖语义约定（supersede 不删除、时点回放）见端口
``MemoryGraphRepositoryPort`` 的类注解。
"""

from datetime import datetime

from sqlmodel import Session, select

from app.adapters.persistence.memory_graph_repository._scope import GraphScopeMixin
from app.models.domain.memory import MemoryStatement, StatementState


class StatementMixin(GraphScopeMixin):
    """陈述（statement 表）的读写；取代式纠正的复合写同事务收口。"""

    def list_active_statements(self, limit: int | None = None) -> list[MemoryStatement]:
        with Session(self.engine, expire_on_commit=False) as session:
            query = select(MemoryStatement).where(
                MemoryStatement.state == StatementState.ACTIVE.value,
                *self._where_user(MemoryStatement),
            )
            if limit is not None:
                query = query.limit(limit)
            return list(session.exec(query).all())

    def find_active_statements(self, subject_ids: list[int]) -> list[MemoryStatement]:
        if not subject_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryStatement).where(
                    MemoryStatement.subject_id.in_(subject_ids),
                    MemoryStatement.state == StatementState.ACTIVE.value,
                    *self._where_user(MemoryStatement),
                )
            ).all())

    def statements_by_ids(self, statement_ids: list[int]) -> list[MemoryStatement]:
        if not statement_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(MemoryStatement).where(MemoryStatement.id.in_(statement_ids))
            ).all()
            return [row for row in rows if self._in_scope(row)]

    def statements_valid_at(
        self, moment: datetime, subject_ids: list[int] | None = None
    ) -> list[MemoryStatement]:
        query = select(MemoryStatement).where(
            MemoryStatement.valid_from.is_not(None),
            MemoryStatement.valid_from <= moment,
            *self._where_user(MemoryStatement),
        )
        query = query.where(
            (MemoryStatement.valid_to.is_(None)) | (MemoryStatement.valid_to > moment)
        )
        if subject_ids is not None:
            if not subject_ids:
                return []
            query = query.where(MemoryStatement.subject_id.in_(subject_ids))
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def insert_statement(self, statement: MemoryStatement) -> MemoryStatement:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(self._stamp_user(statement))
            session.commit()
            session.refresh(statement)
            return statement

    def get_statement(self, statement_id: int) -> MemoryStatement | None:
        with Session(self.engine, expire_on_commit=False) as session:
            statement = session.get(MemoryStatement, statement_id)
            return statement if statement is not None and self._in_scope(statement) else None

    def replace_statement(
        self, old_statement_id: int, new_statement: MemoryStatement,
        valid_to: datetime, invalidated_at: datetime,
    ) -> tuple[bool, MemoryStatement | None]:
        # 取代与落库同一事务：中断只会整体回滚，不会出现「旧行已废、新行未立」的断链态
        with Session(self.engine, expire_on_commit=False) as session:
            old = session.get(MemoryStatement, old_statement_id)
            if old is None or not self._in_scope(old) or old.state != StatementState.ACTIVE.value:
                return False, None
            old.state = StatementState.SUPERSEDED.value
            old.valid_to = valid_to
            old.invalidated_at = invalidated_at
            session.add(old)
            session.add(self._stamp_user(new_statement))
            session.commit()
            session.refresh(new_statement)
            return True, new_statement

    def archive_statement(self, statement_id: int, valid_to: datetime, invalidated_at: datetime) -> bool:
        with Session(self.engine, expire_on_commit=False) as session:
            statement = session.get(MemoryStatement, statement_id)
            if statement is None or not self._in_scope(statement) or statement.state != StatementState.ACTIVE.value:
                return False
            statement.state = StatementState.ARCHIVED.value
            statement.valid_to = valid_to
            statement.invalidated_at = invalidated_at
            session.add(statement)
            session.commit()
            return True

    def supersede_statement(self, statement_id: int, valid_to: datetime, invalidated_at: datetime) -> bool:
        with Session(self.engine, expire_on_commit=False) as session:
            statement = session.get(MemoryStatement, statement_id)
            if statement is None or not self._in_scope(statement) or statement.state != StatementState.ACTIVE.value:
                return False
            statement.state = StatementState.SUPERSEDED.value
            statement.valid_to = valid_to
            statement.invalidated_at = invalidated_at
            session.add(statement)
            session.commit()
            return True
