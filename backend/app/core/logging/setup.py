"""sink 装配：把 :class:`~app.core.config.LoggingConfig` 落到 loguru。

策略（沿袭原 ``app`` 域约定）：
- 只配置本项目的日志管道：loguru sink 按 ``logging.yaml`` 逐个装配；stdlib
  侧仅 ``app`` logger 挂 :class:`~.loguru_backend.InterceptHandler` 转发，
  uvicorn / sqlalchemy 等第三方 logger 保持各自默认，互不干扰、不重复输出；
- 全局级别缺省按环境推导：dev/test 取 DEBUG 便于本地排查，prod 取 INFO
  降噪（可用环境变量 ``APP_LOG_LEVEL`` 覆盖）；
- 配置坏 → :class:`~app.core.exceptions.framework.ConfigError`，不带残缺
  配置启动（与其他分节一致的 fail-fast）。

:func:`setup_logging` 在 ``create_app()`` 最早处调用（容器创建之前），故
与 :func:`~app.core.config.get_environment` 同模式直接 ``load_section``，
不经 wireup；进程内幂等。
"""

import logging
import sys

from loguru import logger as _loguru

from app.core.config import (
    ConsoleSinkConfig,
    FileSinkConfig,
    LoggingConfig,
    get_environment,
    load_section,
)

from .loguru_backend import InterceptHandler, set_default_level

_configured = False


def _resolve_global_level(config: LoggingConfig) -> str:
    """全局级别：显式配置优先，缺省按运行环境推导。"""
    if config.normalized_level is not None:
        return config.normalized_level
    return "DEBUG" if get_environment() in ("dev", "test") else "INFO"


def setup_logging() -> None:
    """初始化日志管道（loguru sink + stdlib ``app`` 域桥接）；重复调用幂等。"""
    global _configured
    if _configured:
        return

    config = load_section("logging.yaml", LoggingConfig)
    global_level = _resolve_global_level(config)

    _loguru.remove()  # 去掉 loguru 默认 sink，输出完全由 logging.yaml 决定
    # 门面/桥接经 bind(logger_name=...) 显式声明归属；patcher 镜像到 record.name，
    # 使格式 token {name} 与 JSON 序列化字段都不依赖调用帧推导
    _loguru.configure(
        # request_id 默认 "-"：由 api.middleware.RequestIDMiddleware 在请求期覆盖，
        # 格式串/JSON 序列化统一引用，避免 extra 缺 key 报错
        extra={"logger_name": "app", "request_id": "-"},
        patcher=lambda record: record.__setitem__(
            "name", record["extra"].get("logger_name") or record["name"]
        ),
    )
    for sink, sink_level in zip(config.sinks, config.normalized_sink_levels):
        kwargs = {
            "level": sink_level or global_level,
            "format": sink.format or config.format,
            "serialize": sink.serialize,
            "backtrace": config.backtrace,
            "diagnose": config.diagnose,
        }
        if isinstance(sink, FileSinkConfig):
            _loguru.add(
                sink.path,
                rotation=sink.rotation,
                retention=sink.retention,
                compression=sink.compression,
                enqueue=sink.enqueue,
                **kwargs,
            )
        elif isinstance(sink, ConsoleSinkConfig):
            _loguru.add(sys.stderr, **kwargs)

    # stdlib app 域 → loguru：级别放开（DEBUG），过滤交给各 sink
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.DEBUG)
    app_logger.handlers = [InterceptHandler()]
    app_logger.propagate = False  # 不向 root 传播，避免与 uvicorn 等重复输出

    set_default_level(global_level)
    _configured = True
