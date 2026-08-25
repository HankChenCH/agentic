"""业务异常基类：框架对业务层暴露的契约。

具体业务异常由业务侧在 :mod:`app.exceptions` 按域定义并继承本类；
全局异常处理器（:mod:`app.api.exception_handlers`）以本类为注册锚点，
识别业务异常并按 ``http_status`` / ``code`` / ``message`` 如实返回。
"""

from app.core.exceptions.base import AgenticError


class BusinessError(AgenticError):
    """业务异常基类。

    与框架/基础异常不同：业务异常是预期内的调用错误，无敏感堆栈可泄漏，
    任何环境（含生产）都直接把 ``message`` 返回给调用方。

    子类通过类属性声明默认码与 HTTP 状态；错误码分段规划由业务包
    :mod:`app.exceptions` 自行维护。
    """

    default_code = 400
    default_http_status: int = 400

    def __init__(self, message: str = "", *, code: int | None = None, http_status: int | None = None):
        self.http_status = http_status if http_status is not None else self.default_http_status
        super().__init__(message, code=code)
