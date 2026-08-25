import os
from typing import ClassVar

from sqlalchemy import Engine
from sqlmodel import create_engine

from app.core.config import SQLiteDBProviderEntry
from app.infrastructures.db.db_provider import DBProvider, DatabaseBuilder, register


@register
class SqliteBuilder(DatabaseBuilder):
    """SQLite 构建器：单文件本地库，供开发 / 单测使用。

    ``check_same_thread=False`` 允许多线程共享 Engine（FastAPI 线程池 /
    Celery prefork 场景）；SQLite 不会自动创建父目录，提前建好以免
    连接失败。
    """

    provider: ClassVar[DBProvider] = DBProvider.SQLITE

    def build(self, entry: SQLiteDBProviderEntry) -> Engine:
        # SQLite 不会自动创建父目录，提前建好以免连接失败
        db_dir = os.path.dirname(entry.dsn)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        return create_engine(f"sqlite:///{entry.dsn}", connect_args={"check_same_thread": False})
