"""记忆组件业务异常（错误码 3xxx）。

命名注意：不能叫 ``MemoryError``，会遮蔽 Python 内建的同名异常。
"""

from app.core.exceptions.business import BusinessError


class MemoryComponentError(BusinessError):
    """记忆组件通用业务错误。"""

    default_code = 3000
