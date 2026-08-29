from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, SecretStr, model_validator


class SQLiteDBProviderEntry(BaseModel):
    """SQLite 存储 entry。"""

    type: Literal["sqlite"] = Field(default="sqlite", description="存储类型标识")
    dsn: str = Field(
        default="data/agentic.db",
        description="SQLite 数据库文件路径",
    )


class PostgresDBProviderEntry(BaseModel):
    """PostgreSQL 存储 entry（psycopg3 驱动接入）。

    离散字段而非 DSN 串：与 docker-compose 的 ``POSTGRES_*`` 环境变量
    一一对应，密码经 :class:`~pydantic.SecretStr` 脱敏；连接 URL 由
    构建器组装。
    """

    type: Literal["postgresql"] = Field(default="postgresql", description="存储类型标识")
    host: str = Field(default="127.0.0.1", description="PostgreSQL 主机地址")
    port: int = Field(default=5432, description="PostgreSQL 端口")
    user: str = Field(description="用户名")
    password: SecretStr = Field(description="密码")
    db: str = Field(description="数据库名")
    # 连接池（SQLAlchemy QueuePool）参数；默认值与 SQLAlchemy 缺省行为一致
    pool_size: int = Field(default=5, ge=1, description="池内常驻连接数")
    max_overflow: int = Field(default=10, ge=0, description="高峰期允许临时超出的连接数")
    pool_recycle: int = Field(
        default=1800,
        ge=0,
        description="连接最大存活秒数，超龄回收重建（0 = 不回收）；防服务端/中间件静默断连",
    )


# 按 type 判别的 Union；未来新增数据库供应商时在此扩展
DBProviderEntry = Annotated[
    Union[SQLiteDBProviderEntry, PostgresDBProviderEntry],
    Field(discriminator="type"),
]


class DBConfig(BaseModel):
    """数据库配置：default 引用 providers 里的一个 entry key。"""

    default: str = Field(default="sqlite", description="默认 entry key")
    providers: dict[str, DBProviderEntry] = Field(description="具名数据库实例表")

    @model_validator(mode="after")
    def _default_must_exist(self):
        if self.default not in self.providers:
            raise ValueError(f"default provider '{self.default}' not in providers: {list(self.providers)}")
        return self
