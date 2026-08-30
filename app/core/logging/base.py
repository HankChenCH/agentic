"""日志门面抽象：与标准库 :class:`logging.Logger` 方法面一致的接口。

框架各组件只依赖本模块的 :class:`AppLogger`，不感知底层实现
（当前为 loguru，见 :mod:`.loguru_backend`）。日后替换日志组件时，
只需新写一个 ``AppLogger`` 实现并在 :class:`~.service.LoggerFactory`
切换，业务代码零改动。

语义对齐标准库：
- ``msg % args`` 惰性格式化 —— 级别不够时既不格式化也不输出；
- ``exc_info`` 接受 ``None`` / ``True``（当前异常）/ 异常实例 / 三元组，
  ``exception()`` 默认携带当前异常的完整堆栈；
- ``**extra`` 透传结构化附加字段（等价 stdlib 的 ``extra=``，可直接进入
  JSON sink 输出）。
"""

from abc import ABC, abstractmethod


class AppLogger(ABC):
    """项目统一日志接口：方法面刻意复刻 ``logging.Logger``，便于迁移与替换。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """logger 名称（惯例为模块 ``__name__``，如 ``app.services.orchestration.agentic_service``）。"""

    @abstractmethod
    def debug(self, msg, *args, exc_info=None, **extra) -> None: ...

    @abstractmethod
    def info(self, msg, *args, exc_info=None, **extra) -> None: ...

    @abstractmethod
    def warning(self, msg, *args, exc_info=None, **extra) -> None: ...

    @abstractmethod
    def error(self, msg, *args, exc_info=None, **extra) -> None: ...

    @abstractmethod
    def critical(self, msg, *args, exc_info=None, **extra) -> None: ...

    @abstractmethod
    def exception(self, msg, *args, exc_info=True, **extra) -> None:
        """带当前异常完整堆栈的 ``error``（语义同 stdlib）。"""

    @abstractmethod
    def log(self, level, msg, *args, exc_info=None, **extra) -> None:
        """按级别输出：``level`` 为 stdlib 级别常量（``logging.INFO`` 等）或级别名。"""

    @abstractmethod
    def isEnabledFor(self, level: int) -> bool:
        """该级别是否会被输出（stdlib 级别常量）。"""

    @abstractmethod
    def setLevel(self, level) -> None:
        """调整本 logger 实例的级别阈值（stdlib 常量或级别名）。"""

    @abstractmethod
    def getChild(self, suffix: str) -> "AppLogger":
        """派生子 logger：``name + "." + suffix``（语义同 stdlib）。"""

    # stdlib 历史别名（具体方法，委托 warning，避免抽象属性传染子类）
    def warn(self, msg, *args, exc_info=None, **extra) -> None:
        self.warning(msg, *args, exc_info=exc_info, **extra)
