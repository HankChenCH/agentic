from functools import lru_cache

from pydantic import BaseModel, Field
from wireup import injectable

from .llm import LLMConfig, LLMProviderEntry, ModelTaskType
from .auth import AuthConfig
from .db import DBConfig, DBProviderEntry, PostgresDBProviderEntry, SQLiteDBProviderEntry
from .document_parser import DocumentParserConfig, DocumentParserProviderEntry, MineruCloudEntry
from .vector_db import VectorDBConfig, VectorDBProviderEntry, WeaviateDBProviderEntry
from .filesystem import (
    FilesystemConfig,
    FilesystemProviderEntry,
    LocalFilesystemEntry,
    S3FilesystemEntry,
)
from .http import CorsConfig, HttpConfig, MaxBodyConfig
from .loader import ConfigError, load_section, read_config
from .logging import ConsoleSinkConfig, FileSinkConfig, LoggingConfig, SinkConfig
from .memory import MemoryConfig
from .metrics import MetricsConfig
from .redis import RedisConfig, RedisProviderEntry, StandaloneRedisProviderEntry
from .task import TaskConfig, TaskConnectionRef, resolve_url

__all__ = [
    "AppConfig",
    "ConfigError",
    "get_environment",
    "LLMConfig",
    "LLMProviderEntry",
    "ModelTaskType",
    "AuthConfig",
    "DBConfig",
    "DBProviderEntry",
    "SQLiteDBProviderEntry",
    "PostgresDBProviderEntry",
    "VectorDBConfig",
    "VectorDBProviderEntry",
    "WeaviateDBProviderEntry",
    "DocumentParserConfig",
    "DocumentParserProviderEntry",
    "MineruCloudEntry",
    "FilesystemConfig",
    "FilesystemProviderEntry",
    "LocalFilesystemEntry",
    "S3FilesystemEntry",
    "CorsConfig",
    "HttpConfig",
    "MaxBodyConfig",
    "LoggingConfig",
    "ConsoleSinkConfig",
    "FileSinkConfig",
    "SinkConfig",
    "MemoryConfig",
    "MetricsConfig",
    "RedisConfig",
    "RedisProviderEntry",
    "StandaloneRedisProviderEntry",
    "TaskConfig",
    "TaskConnectionRef",
    "resolve_url",
]

# 合法的运行环境取值；environment 决定异常响应等信息详略
_ENVIRONMENTS = ("dev", "test", "prod")


def _app_setting(key: str, fallback):
    """取 app.yaml 顶层标量，键缺省时回退代码默认值。"""
    return read_config("app.yaml").get(key, fallback)


@lru_cache(maxsize=1)
def get_environment() -> str:
    """当前运行环境：``app.yaml`` 顶层 ``environment``，可被环境变量 ``APP_ENV`` 覆盖。

    供异常处理器等请求期使用：单独读 app.yaml，不在 import 期实例化
    AppConfig，保持「配置错误 fail-fast 于 lifespan」的设计。非法取值立即
    抛 :class:`ConfigError`。
    """
    environment = str(_app_setting("environment", "dev"))
    if environment not in _ENVIRONMENTS:
        raise ConfigError(f"environment 取值非法: {environment!r}，可选: {list(_ENVIRONMENTS)}")
    return environment


@injectable
class AppConfig(BaseModel):
    """总体配置：由 ``app/configs/*.yaml`` 组装，wireup 无参实例化时触发加载。

    顶层元信息来自 ``app.yaml``；各分节文件与分节模型一一对应
    （llm.yaml → LLMConfig、db.yaml → DBConfig、memory.yaml → MemoryConfig、
    logging.yaml → LoggingConfig），加载与校验逻辑见 :mod:`.loader`。

    新增基础设施配置时：在包内新建子模块（如 ``memory.py``），在此聚合字段，
    并在上面的 import / __all__ 中导出。
    """
    name: str = Field(
        description="应用名称",
        default_factory=lambda: _app_setting("name", "agentic-app"),
    )
    description: str = Field(
        description="应用描述",
        default_factory=lambda: _app_setting("description", ""),
    )
    default_agentic_id: str = Field(
        description="默认智能体标识 <agent_type:agent_name>，未指定或未知时回退使用。",
        default_factory=lambda: _app_setting("default_agentic_id", "builtin:demo"),
    )
    environment: str = Field(
        description="运行环境 dev/test/prod：异常响应等信息按环境区分详略（可用环境变量 APP_ENV 覆盖）。",
        default_factory=get_environment,
    )
    http: HttpConfig = Field(
        description="HTTP 入口边缘策略：CORS 白名单与请求体大小上限（装配见 app.cmd.http；限流为 fastapi-limiter 依赖，见 app.api.rate_limit）。",
        default_factory=lambda: load_section("http.yaml", HttpConfig),
    )
    auth: AuthConfig = Field(
        description="认证配置：JWT 签发/验签参数（用户注册/登录）。",
        default_factory=lambda: load_section("auth.yaml", AuthConfig),
    )
    llm: LLMConfig = Field(
        description="LLM 基建配置：模型供应商实例表，供所有模型调用方共用。",
        default_factory=lambda: load_section("llm.yaml", LLMConfig),
    )
    db: DBConfig = Field(
        description="数据库配置。",
        default_factory=lambda: load_section("db.yaml", DBConfig),
    )
    vector_db: VectorDBConfig = Field(
        description="向量库配置：embedding 检索存储（供 RAG/检索组件使用）。",
        default_factory=lambda: load_section("vector_db.yaml", VectorDBConfig),
    )
    filesystem: FilesystemConfig = Field(
        description="文件存储配置：本地 / S3 兼容对象存储抽象（供知识库等文件能力使用）。",
        default_factory=lambda: load_section("filesystem.yaml", FilesystemConfig),
    )
    document_parser: DocumentParserConfig = Field(
        description="文档解析配置：PDF 等文档的结构化解析供应商（供知识库入库流水线使用）。",
        default_factory=lambda: load_section("document_parser.yaml", DocumentParserConfig),
    )
    memory: MemoryConfig = Field(
        description="记忆系统配置。",
        default_factory=lambda: load_section("memory.yaml", MemoryConfig),
    )
    redis: RedisConfig = Field(
        description="Redis 连接配置：直接使用 Redis 的能力（取消信号存储等）共用，与 Celery 任务队列配置相互独立。",
        default_factory=lambda: load_section("redis.yaml", RedisConfig),
    )
    logging: LoggingConfig = Field(
        description="日志配置：多 sink 输出与流转规则（装配见 app.core.logging）。",
        default_factory=lambda: load_section("logging.yaml", LoggingConfig),
    )
    task: TaskConfig = Field(
        description="任务队列配置：Celery broker/backend（入口见 app.cmd.task_executor）。",
        default_factory=lambda: load_section("task.yaml", TaskConfig),
    )
    metrics: MetricsConfig = Field(
        description="运维监控指标配置：/metrics 采集端点与 worker 指标端口（实现见 app.api.metrics）。",
        default_factory=lambda: load_section("metrics.yaml", MetricsConfig),
    )
