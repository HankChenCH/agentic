"""admin 复合命令行的 db 域：在真实 SQLite 文件库上走完整 Alembic 链路。

覆盖 alembic.ini 锚定（%(here)s，不依赖 CWD）→ env.py 从 AppConfig 解析
URL（DB_DSN 环境插值）→ 初始迁移建全表 / 回退 / 存量库 stamp 接入。
"""

import pytest
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import app.models.domain  # noqa: F401 收集全部表模型
from app.commands.db import _config, downgrade_cmd, stamp_cmd, upgrade_cmd
from app.core.config.loader import read_config


@pytest.fixture()
def db_dsn(tmp_path, monkeypatch):
    """DB_DSN 指向临时 SQLite 文件库。

    read_config 的 lru_cache 连同环境变量插值结果一起缓存，patch 环境变量
    前后必须 cache_clear，否则上一份插值结果会遮蔽本次 DB_DSN。
    """
    dsn = tmp_path / "migrated.db"
    monkeypatch.setenv("DB_DSN", str(dsn))
    read_config.cache_clear()
    yield dsn
    read_config.cache_clear()


def _head_revision() -> str:
    return ScriptDirectory.from_config(_config()).get_current_head()


def _current_revision(dsn) -> str | None:
    engine = create_engine(f"sqlite:///{dsn}")
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def _tables(dsn) -> set[str]:
    engine = create_engine(f"sqlite:///{dsn}")
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_creates_all_tables(db_dsn):
    upgrade_cmd("head")
    tables = _tables(db_dsn)
    assert set(SQLModel.metadata.tables) <= tables
    assert "alembic_version" in tables
    assert _current_revision(db_dsn) == _head_revision()


def test_upgrade_is_idempotent(db_dsn):
    upgrade_cmd("head")
    upgrade_cmd("head")  # 已在 head：重复执行无副作用、不报错
    assert _current_revision(db_dsn) == _head_revision()


def test_downgrade_to_base_drops_all_tables(db_dsn):
    upgrade_cmd("head")
    downgrade_cmd("base")
    assert not (set(SQLModel.metadata.tables) & _tables(db_dsn))
    assert _current_revision(db_dsn) is None


def test_stamp_adopts_create_all_database(db_dsn):
    """存量库接入路径：create_all 时代建好的库 stamp 到 head 后，upgrade 为 no-op。"""
    engine = create_engine(f"sqlite:///{db_dsn}")
    SQLModel.metadata.create_all(engine)
    engine.dispose()
    assert "alembic_version" not in _tables(db_dsn)

    stamp_cmd("head")
    assert "alembic_version" in _tables(db_dsn)
    assert _current_revision(db_dsn) == _head_revision()

    upgrade_cmd("head")  # 已 stamp 到 head：不重复建表、不报错
    assert _current_revision(db_dsn) == _head_revision()
