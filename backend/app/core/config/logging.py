"""日志配置分节：``logging.yaml`` → :class:`LoggingConfig`。

日志基建在 :mod:`app.core.logging`（门面 + sink 装配），本模块只描述
「输出到哪、怎么转、什么格式」。多 sink 各自独立配置：console / file
按 ``type`` 判别联合，file sink 支持 loguru 风格的 ``rotation`` /
``retention`` / ``compression`` / ``enqueue``；级别与格式每 sink 可覆盖，
缺省回落全局值。
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.core.exceptions.framework import ConfigError

# loguru 全部内置级别（名称大小写不敏感，装配时统一大写）
_LEVEL_NAMES = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")


def _normalize_level(value: str | None) -> str | None:
    """级别名归一化：空串（``${APP_LOG_LEVEL:}`` 缺省产物）视为未设置。"""
    if value is None:
        return None
    level = value.strip().upper()
    if not level:
        return None
    if level not in _LEVEL_NAMES:
        raise ConfigError(f"日志级别非法: {value!r}，可选: {list(_LEVEL_NAMES)}")
    return level


class _SinkBase(BaseModel):
    """sink 公共字段：级别/格式缺省回落全局值。"""

    level: str | None = Field(default=None, description="本 sink 级别；缺省用全局 level")
    format: str | None = Field(default=None, description="本 sink 格式串；缺省用全局 format")
    serialize: bool = Field(default=False, description="JSON 序列化输出，便于日志采集")


class ConsoleSinkConfig(_SinkBase):
    """控制台 sink。"""

    type: Literal["console"] = Field(default="console", description="判别字段")


class FileSinkConfig(_SinkBase):
    """文件 sink：流转规则（rotation/retention/compression）为本 sink 独有配置。"""

    type: Literal["file"] = Field(description="判别字段")
    path: str = Field(description="日志文件路径（父目录自动创建）")
    rotation: Union[str, int, None] = Field(
        default="100 MB",
        description="轮转规则：'500 MB' / '1 week' / '12:00'（每日定时）/ 字节数整数 / None 关闭",
    )
    retention: Union[str, int, None] = Field(
        default="10",
        description="保留规则：'10 days' / 保留文件数整数 / None 全部保留",
    )
    compression: Union[str, None] = Field(
        default=None,
        description="轮转文件压缩格式：'zip' / 'gz' / 'tar' / None 不压缩",
    )
    enqueue: bool = Field(
        default=True,
        description="队列异步写入（线程/进程安全，进程退出时自动 flush）",
    )


SinkConfig = Annotated[
    Union[ConsoleSinkConfig, FileSinkConfig],
    Field(discriminator="type"),
]


class LoggingConfig(BaseModel):
    """日志总配置：全局级别/格式 + sink 列表，装配逻辑见 :mod:`app.core.logging.setup`。"""

    level: Union[str, None] = Field(
        default=None,
        description="全局级别；缺省（或空串）按运行环境推导：dev/test=DEBUG，prod=INFO",
    )
    format: str = Field(
        default="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name} | {message}",
        description="全局格式串（loguru 语法：{time}/{level}/{name}/{function}/{line}/{message}；"
        "{name} 已由装配层 patcher 对齐门面/桥接声明的 logger 名，等价 stdlib %(name)s）",
    )
    backtrace: bool = Field(
        default=True,
        description="异常时向上扩展堆栈帧（配合「日志永远记录完整堆栈」原则）",
    )
    diagnose: bool = Field(
        default=True,
        description="异常帧显示变量值辅助定位；生产环境建议关闭以防泄露敏感值",
    )
    sinks: list[SinkConfig] = Field(
        default_factory=lambda: [ConsoleSinkConfig()],
        description="输出位置列表，逐个装配；空列表等价于静默",
    )

    @property
    def normalized_level(self) -> str | None:
        """归一化后的全局级别（空串 → None → 由装配层按环境推导）。"""
        return _normalize_level(self.level)

    @property
    def normalized_sink_levels(self) -> list[str | None]:
        return [_normalize_level(sink.level) for sink in self.sinks]
