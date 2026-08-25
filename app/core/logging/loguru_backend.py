"""loguru 后端实现：:class:`AppLogger` 门面之下的实际日志组件。

本模块是全项目唯一直接 import loguru 的地方（:mod:`.setup` 除外——它负责
sink 装配）。包括：
- :class:`LoguruAppLogger` —— 门面实现，桥接 stdlib 风格调用到 loguru；
- :class:`InterceptHandler` —— stdlib ``app`` 域记录转发进 loguru 的官方
  配方，使 ``logging.getLogger(__name__)`` 的存量用法与门面输出到同一套
  sink（uvicorn / sqlalchemy 等第三方 logger 不受影响）。
"""

import inspect
import logging
import sys

from loguru import logger as _loguru

from .base import AppLogger

# stdlib 级别常量 → loguru 级别名（两套体系同名不同源，这里显式对齐）
_STD_TO_LOGURU: dict[int, str] = {
    5: "TRACE",
    logging.DEBUG: "DEBUG",
    20: "INFO",
    25: "SUCCESS",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}
_LOGURU_TO_STD: dict[str, int] = {name: no for no, name in _STD_TO_LOGURU.items()}
_LOGURU_TO_STD["WARN"] = logging.WARNING  # stdlib 历史别名


def _as_std_level(level) -> int:
    """级别统一为 stdlib 常量：接受 int / ``logging`` 常量 / 级别名。"""
    if isinstance(level, str):
        upper = level.strip().upper()
        if upper not in _LOGURU_TO_STD:
            raise ValueError(f"未知日志级别: {level!r}，可选: {sorted(_LOGURU_TO_STD)}")
        return _LOGURU_TO_STD[upper]
    if level not in _STD_TO_LOGURU:
        raise ValueError(f"未知日志级别数值: {level!r}，可选: {sorted(_STD_TO_LOGURU)}")
    return level


def _coerce_exception(exc_info):
    """stdlib ``exc_info`` 语义 → loguru ``exception=`` 取值。

    ``True`` → 当前活跃异常；``None``/``False`` → 不带异常（stdlib 的
    ``None`` 永不猜异常，与 loguru 一致）；异常实例/三元组原样传递。
    """
    if exc_info is True:
        exc_type, exc, tb = sys.exc_info()
        return (exc_type, exc, tb) if exc is not None else None
    if not exc_info:
        return None
    return exc_info


# 门面实例未显式 setLevel 时跟随的全局阈值；由 setup_logging 依据配置写入。
# NOTSET(0) 视为全放行，级别过滤完全交给各 sink 的 level 参数。
_default_threshold: int = logging.NOTSET


def set_default_level(level) -> None:
    """设置全局默认阈值（stdlib 常量或级别名），新门面实例缺省继承。"""
    global _default_threshold
    _default_threshold = _as_std_level(level)


class LoguruAppLogger(AppLogger):
    """loguru 实现的日志门面。

    - 惰性 ``%`` 格式化：先按阈值守卫，未达级别不执行 ``msg % args``；
    - ``opt(depth=2)`` 校正调用点归属，``{name}`` 显示业务模块而非本模块；
    - ``**extra`` 直接透传 loguru kwargs，进入 record.extra（JSON 可见）。
    """

    def __init__(self, name: str, level=None):
        self._name = name
        self._threshold: int | None = _as_std_level(level) if level is not None else None

    @property
    def name(self) -> str:
        return self._name

    def _effective_threshold(self) -> int:
        if self._threshold is not None:
            return self._threshold
        return _default_threshold if _default_threshold != logging.NOTSET else logging.DEBUG

    def isEnabledFor(self, level: int) -> bool:
        return level >= self._effective_threshold()

    def setLevel(self, level) -> None:
        self._threshold = _as_std_level(level)

    def getChild(self, suffix: str) -> "LoguruAppLogger":
        return LoguruAppLogger(f"{self._name}.{suffix}")

    # 公开方法 → _log → loguru 的调用链固定两层，depth=2 使 {name}/{function}/{line}
    # 指向业务代码。若增删中间层需同步调整。
    def debug(self, msg, *args, exc_info=None, **extra) -> None:
        self._log(logging.DEBUG, msg, args, exc_info, extra)

    def info(self, msg, *args, exc_info=None, **extra) -> None:
        self._log(logging.INFO, msg, args, exc_info, extra)

    def warning(self, msg, *args, exc_info=None, **extra) -> None:
        self._log(logging.WARNING, msg, args, exc_info, extra)

    def error(self, msg, *args, exc_info=None, **extra) -> None:
        self._log(logging.ERROR, msg, args, exc_info, extra)

    def critical(self, msg, *args, exc_info=None, **extra) -> None:
        self._log(logging.CRITICAL, msg, args, exc_info, extra)

    def exception(self, msg, *args, exc_info=True, **extra) -> None:
        self._log(logging.ERROR, msg, args, exc_info, extra)

    def log(self, level, msg, *args, exc_info=None, **extra) -> None:
        self._log(_as_std_level(level), msg, args, exc_info, extra)

    def _log(self, level: int, msg, args, exc_info, extra: dict) -> None:
        if not self.isEnabledFor(level):
            return
        text = str(msg) % args if args else str(msg)
        # bind 固定显示名（不随调用帧漂移）；depth=2 让 {function}/{line} 指向
        # 业务代码。若增删中间层需同步调整。
        _loguru.bind(logger_name=self._name).opt(
            exception=_coerce_exception(exc_info), depth=2
        ).log(_STD_TO_LOGURU[level], text, **extra)


class InterceptHandler(logging.Handler):
    """stdlib → loguru 转发 handler（基于 loguru 官方配方）。

    挂在 stdlib ``app`` logger 上：存量 ``logging.getLogger(__name__)``
    用法与门面输出汇入同一套 sink；uvicorn 等第三方 logger 保持原样。
    ``bind(logger_name=record.name)`` 保留 stdlib 原始 logger 名。
    """

    def emit(self, record: logging.LogRecord) -> None:
        # stdlib 与 loguru 级别名基本同名；映射不到时退回数值
        try:
            level = _loguru.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 向上跳出 stdlib logging 内部帧，让 {function}/{line} 指向真正的
        # 调用方（record.getMessage() 已完成 % 格式化）
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        _loguru.bind(logger_name=record.name).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )
