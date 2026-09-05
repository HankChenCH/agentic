"""统一日志门面工厂（wireup 注入口）。

wireup 无法感知「注入目标类」（无 ``ILogger<T>`` 等价物），因此组件按
stdlib 惯例显式以模块名取 logger：

    @injectable
    @dataclass
    class SomeService:
        logger_factory: LoggerFactory

        def __post_init__(self):
            self.logger = self.logger_factory.get_logger(__name__)

返回 :class:`~.base.AppLogger` 门面，业务代码不感知底层组件
（见 :mod:`.loguru_backend`）。非注入场景（如模块级函数）可直接用
stdlib ``logging.getLogger(__name__)`` —— ``app`` 域已由
:class:`~.loguru_backend.InterceptHandler` 桥接进同一套 sink。
"""

from dataclasses import dataclass

from wireup import injectable

from .base import AppLogger
from .loguru_backend import LoguruAppLogger


@injectable(lifetime="singleton")
@dataclass
class LoggerFactory:
    """日志门面工厂：单例；``get_logger`` 返回轻量门面，可随意创建。"""

    def get_logger(self, name: str) -> AppLogger:
        return LoguruAppLogger(name)
