from abc import ABC, abstractmethod
from enum import Enum
from typing import ClassVar

from sqlalchemy import Engine

from app.core.config import DBProviderEntry


class DBProvider(str, Enum):
    """数据库供应商标识，与 ``DBProviderEntry.type`` 对应。"""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


class DatabaseBuilder(ABC):
    """数据库构建器：每个供应商一个子类，把 provider entry 翻译成 Engine。

    契约类型即 SQLAlchemy :class:`~sqlalchemy.engine.Engine`（repositories
    直接注入使用），供应商差异全部收敛在连接 URL / connect_args / 池参数
    里，无需 filesystem 那样的两层 build_client/build——Engine 自带连接池，
    本身就是由工厂按 entry key 缓存的重资源。entry 已携带全部直连参数，
    构建器自身无需再读全局配置；工厂按 entry 的 ``type`` 路由到注册的
    构建器，保证类型与构建器一一对应。
    """

    provider: ClassVar[DBProvider]

    @abstractmethod
    def build(self, entry: DBProviderEntry) -> Engine:
        """按 entry 创建 SQLAlchemy Engine（连接池等重资源，由工厂缓存复用）。"""


# 供应商注册表：@register 自动登记，新增供应商不改工厂
DB_BUILDERS: dict[DBProvider, type[DatabaseBuilder]] = {}


def register(builder_cls: type[DatabaseBuilder]) -> type[DatabaseBuilder]:
    DB_BUILDERS[builder_cls.provider] = builder_cls
    return builder_cls
