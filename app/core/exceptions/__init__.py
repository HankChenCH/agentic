"""异常分级体系（框架基准定义）。

- 基础异常：built-in / 第三方库异常，不定义类，由全局处理器归类；
- 框架异常：``FrameworkError``（配置 ``ConfigError``、基建 ``InfrastructureError``）；
- 业务异常基类：``BusinessError``（框架契约），具体业务异常由业务侧在
  :mod:`app.exceptions` 按域定义。

本包保持零 FastAPI 依赖，异常处理器（HTTP wiring）在
:mod:`app.api.exception_handlers`，SSE 流式路径未来也可复用这里的定义。
"""

from app.core.exceptions.base import AgenticError
from app.core.exceptions.business import BusinessError
from app.core.exceptions.framework import ConfigError, FrameworkError, InfrastructureError

__all__ = [
    "AgenticError",
    "FrameworkError",
    "ConfigError",
    "InfrastructureError",
    "BusinessError",
]
