"""DatabaseFactory 实例管理：entry 解析 / Engine 缓存 / 错误路径。

纯单测：只触 sqlite（临时目录文件），不连 PostgreSQL——postgres 构建器
与 sqlite 走同一条 DatabaseBuilder 注册表路径（lazy：Engine 构造不发
网络请求），无需中间件即可覆盖工厂的路由与缓存逻辑。
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import Engine

from app.core.config import DBConfig, SQLiteDBProviderEntry
from app.adapters.db import DatabaseFactory, create_default_db


@dataclass
class _StubAppConfig:
    """最小 AppConfig 替身：工厂只消费 db 分节，避免单测加载全量 yaml。"""

    db: DBConfig


def _factory(tmp_path, entries: dict[str, str], default: str = "main") -> DatabaseFactory:
    """entries：entry key → sqlite 数据库文件名。"""
    config = DBConfig(
        default=default,
        providers={key: SQLiteDBProviderEntry(dsn=str(tmp_path / name)) for key, name in entries.items()},
    )
    return DatabaseFactory(app_config=_StubAppConfig(db=config))


def test_create_default_entry_builds_sqlite_engine(tmp_path):
    factory = _factory(tmp_path, {"main": "sub/main.db"})

    engine = factory.create()

    assert isinstance(engine, Engine)
    assert engine.url.get_backend_name() == "sqlite"
    assert engine.url.database == str(tmp_path / "sub" / "main.db")
    # SQLite 构建器提前创建父目录，避免首次连接失败
    assert (tmp_path / "sub").is_dir()
    engine.dispose()


def test_create_by_name_selects_matching_entry(tmp_path):
    factory = _factory(tmp_path, {"main": "main.db", "aux": "aux.db"})

    main = factory.create()
    aux = factory.create(name="aux")

    assert main.url.database == str(tmp_path / "main.db")
    assert aux.url.database == str(tmp_path / "aux.db")
    main.dispose()
    aux.dispose()


def test_engine_cached_per_entry_key(tmp_path):
    # Engine 自带连接池（重资源），同一 entry 多次 create 复用同一实例
    factory = _factory(tmp_path, {"main": "main.db", "aux": "aux.db"})

    assert factory.create() is factory.create()
    assert factory.create() is not factory.create(name="aux")


def test_unknown_key_raises(tmp_path):
    factory = _factory(tmp_path, {"main": "main.db"})

    with pytest.raises(ValueError, match="unknown db provider: nope"):
        factory.create(name="nope")


def test_unsupported_type_raises(tmp_path):
    # model_construct 绕过 pydantic 校验构造 type 未注册的脏 entry，
    # 走 _resolve 的 supported 报错分支（合法配置在校验期即被拒绝）
    dirty = SQLiteDBProviderEntry.model_construct(type="bogus")
    config = DBConfig.model_construct(default="main", providers={"main": dirty})
    factory = DatabaseFactory(app_config=_StubAppConfig(db=config))

    with pytest.raises(ValueError, match="unsupported db provider type: bogus"):
        factory.create()


def test_create_default_db_delegates_to_factory(tmp_path):
    factory = _factory(tmp_path, {"main": "main.db"})

    engine = create_default_db(factory)

    assert engine is factory.create()
    engine.dispose()
