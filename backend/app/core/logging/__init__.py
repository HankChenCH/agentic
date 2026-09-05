"""日志基建：统一门面 + loguru 后端 + sink 装配 + wireup 注入工厂。

分层（自底向上）：
- :mod:`.base` —— :class:`AppLogger`，与标准库 ``logging.Logger`` 方法面
  一致的抽象接口，框架各组件只依赖它；
- :mod:`.loguru_backend` —— loguru 实现与 stdlib ``app`` 域桥接
  （:class:`InterceptHandler`）；
- :mod:`.setup` —— :func:`setup_logging` 把 ``logging.yaml`` 落成 sink
  （console / file + 轮转保留压缩），``create_app()`` 最早处调用；
- :mod:`.service` —— :class:`LoggerFactory`，wireup 单例注入工厂。

配合全局异常处理器（:mod:`app.api.exception_handlers`）的原则：
**日志永远记录完整堆栈，HTTP 响应才按环境脱敏** —— 全局级别 dev/test 取
DEBUG、prod 取 INFO，可经 ``APP_LOG_LEVEL`` 覆盖。
"""

from .base import AppLogger
from .loguru_backend import InterceptHandler, LoguruAppLogger
from .service import LoggerFactory
from .setup import setup_logging

__all__ = [
    "AppLogger",
    "InterceptHandler",
    "LoguruAppLogger",
    "LoggerFactory",
    "setup_logging",
]
