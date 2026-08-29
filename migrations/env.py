"""Alembic 迁移环境：URL 与表元数据均取自应用自身。

- URL：``AppConfig → DatabaseFactory → engine.url``——URL 组装只发生在
  infrastructures builders（sqlite/postgresql 同一入口），这里不手工拼串；
  ``AGENTIC_CONFIG_DIR`` / ``AGENTIC_ENV_FILE`` 与应用侧完全一致。
- 元数据：``import app.models.domain`` 收集全部 SQLModel 表模型后取
  ``SQLModel.metadata``（新增模型包时同步补进该聚合入口即可）。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlmodel import SQLModel

import app.models.domain  # noqa: F401  注册全部表模型到 SQLModel.metadata
from app.core.config import AppConfig
from app.infrastructures.db.db_factory import DatabaseFactory

config = context.config

# ini 提供 alembic/sqlalchemy 的 logger 配置；程序化调用（无 ini）时跳过
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def _engine_url() -> str:
    """从应用配置链解析默认库 URL（lazy Engine，只取 url 不发起连接）。"""
    engine = DatabaseFactory(app_config=AppConfig()).create()
    return engine.url.render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    """离线模式（``--sql``）：不连库，仅输出 DDL 脚本。"""
    context.configure(
        url=_engine_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接默认库执行迁移。"""
    engine = create_engine(_engine_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
