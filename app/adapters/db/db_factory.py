from dataclasses import dataclass

from sqlalchemy import Engine
from wireup import injectable

from app.core.config import AppConfig, DBConfig, DBProviderEntry
from app.infrastructures.db.db_provider import DB_BUILDERS, DBProvider, DatabaseBuilder

import app.infrastructures.db.sqlite_provider  # noqa: F401  触发 @register 供应商注册
import app.infrastructures.db.postgresql_provider  # noqa: F401


@injectable
@dataclass
class DatabaseFactory:
    """数据库工厂：按 provider entry key 创建 SQLAlchemy Engine。

    契约类型即 :class:`sqlalchemy.Engine`（repositories 直接注入使用），
    消费方不感知 SQLite / PostgreSQL 的差异。Engine 自带连接池，属于
    重资源，按 entry key 缓存复用——同一 entry 多次 ``create`` 拿到同一
    实例。lazy：构造 Engine 不发起连接，真正的连接发生在首次执行。

    默认宽松、显式严格：未指定 name 时用 ``DBConfig.default``；显式指定
    但 providers 里不存在的 key，或 entry 的供应商 type 未注册构建器，
    则直接抛 ValueError。
    """

    app_config: AppConfig

    def __post_init__(self):
        self._engines: dict[str, Engine] = {}

    def create(self, *, name: str | None = None) -> Engine:
        """按 provider entry key 创建数据库 Engine。"""
        key = name if name is not None else self._config.default
        entry, builder = self._resolve(key)
        return self._engine_for(key, entry, builder)

    @property
    def _config(self) -> DBConfig:
        return self.app_config.db

    def _resolve(self, key: str) -> tuple[DBProviderEntry, DatabaseBuilder]:
        entry = self._config.providers.get(key)
        if entry is None:
            raise ValueError(f"unknown db provider: {key}, configured: {list(self._config.providers)}")

        try:
            provider = DBProvider(entry.type)
        except ValueError:
            supported = [p.value for p in DBProvider]
            raise ValueError(
                f"unsupported db provider type: {entry.type} (entry: {key}), supported: {supported}"
            )
        return entry, DB_BUILDERS[provider]()

    def _engine_for(self, key: str, entry: DBProviderEntry, builder: DatabaseBuilder) -> Engine:
        """按 entry key 取共享 Engine，首次调用时构建并缓存。"""
        if key not in self._engines:
            self._engines[key] = builder.build(entry)
        return self._engines[key]


@injectable(lifetime="singleton")
def create_default_db(factory: DatabaseFactory) -> Engine:
    """默认数据库实例（``DBConfig.default``）的注入入口。

    仿 ``filesystem_factory.create_default_filesystem`` 的惯例：消费方
    （repositories 等）直接注入 ``Engine`` 使用，无需感知工厂；需要指定
    其他 entry（如独立的分析库）时注入 ``DatabaseFactory`` 自行
    ``create(name=...)``。lazy——wireup 首次解析本函数才触发构建。
    """
    return factory.create()
