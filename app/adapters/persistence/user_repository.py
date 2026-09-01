from dataclasses import dataclass

from uuid import UUID
from wireup import injectable
from sqlmodel import Session, select
from sqlalchemy import Engine

from app.models.domain.user import User


@injectable
@dataclass
class UserRepository:
    engine: Engine

    def get_by_id(self, user_id: UUID) -> User | None:
        with Session(self.engine, expire_on_commit=False) as session:
            user = session.get(User, user_id)
            session.commit()
        return user

    def get_by_username(self, username: str) -> User | None:
        with Session(self.engine, expire_on_commit=False) as session:
            user = session.exec(
                select(User).where(User.username == username)
            ).first()
            session.commit()
        return user

    def create(self, username: str, password_hash: str, nickname: str) -> User:
        # 用户名唯一冲突的竞态（查重与插入之间）由调用方捕获 IntegrityError 兜底
        with Session(self.engine, expire_on_commit=False) as session:
            user = User(username=username, password_hash=password_hash, nickname=nickname)
            session.add(user)
            session.commit()
            session.refresh(user)
        return user

    def update_profile(self, user_id: UUID, nickname: str) -> User | None:
        with Session(self.engine, expire_on_commit=False) as session:
            user = session.get(User, user_id)
            if user is None:
                return None
            user.nickname = nickname
            session.commit()
            session.refresh(user)
        return user

    def update_password(self, user_id: UUID, password_hash: str) -> User | None:
        with Session(self.engine, expire_on_commit=False) as session:
            user = session.get(User, user_id)
            if user is None:
                return None
            user.password_hash = password_hash
            session.commit()
            session.refresh(user)
        return user
