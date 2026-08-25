"""自定义异常共同根。

三级异常体系中，只有两级需要自定义类：

- 框架异常 :mod:`app.core.exceptions.framework` —— 启动/运行期的框架级错误；
- 业务异常 :mod:`app.exceptions` —— 业务侧继承 ``BusinessError`` 按域扩展。

基础异常（语法、系统等 built-in 及第三方库异常）不定义类，由全局异常处理器
（:mod:`app.api.exception_handlers`）统一归类为「基础/未知」档处理。
"""


class AgenticError(Exception):
    """所有自定义异常的共同根：携带稳定错误码与人类可读信息。

    ``code`` 是对外的 int 分段错误码（0 为成功，沿用 Response 信封约定），
    分段规划由业务包 :mod:`app.exceptions` 维护；框架/未知统一为 500。
    子类通过类属性 ``default_code`` 声明默认码，构造时可按次覆盖。
    """

    default_code: int = 500

    def __init__(self, message: str = "", *, code: int | None = None):
        self.code = code if code is not None else self.default_code
        self.message = message or self.__class__.__name__
        super().__init__(self.message)
