from typing import ClassVar

from sqlalchemy import URL, Engine, create_engine

from app.core.config import PostgresDBProviderEntry
from app.infrastructures.db.db_provider import DBProvider, DatabaseBuilder, register


@register
class PostgresqlBuilder(DatabaseBuilder):
    """PostgreSQL 构建器：psycopg3 驱动（``postgresql+psycopg`` 方言）。

    用 :meth:`sqlalchemy.URL.create` 组装连接串（自动转义用户名 / 密码
    中的特殊字符）；``pool_pre_ping`` 在取连接前轻量探活，避免 PostgreSQL
    重启后池内残留失效连接导致首查报错。lazy：构造 Engine 不发起网络
    请求，真正的连接发生在首次执行。
    """

    provider: ClassVar[DBProvider] = DBProvider.POSTGRESQL

    def build(self, entry: PostgresDBProviderEntry) -> Engine:
        url = URL.create(
            "postgresql+psycopg",
            username=entry.user,
            password=entry.password.get_secret_value(),
            host=entry.host,
            port=entry.port,
            database=entry.db,
        )
        return create_engine(url, pool_pre_ping=True)
