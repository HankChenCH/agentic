"""用户域业务异常（错误码 5xxx）。"""

from app.core.exceptions.business import BusinessError


class UserError(BusinessError):
    """用户域通用业务错误。"""

    default_code = 5000


class UsernameDuplicatedError(UserError):
    """用户名已被注册。"""

    default_code = 5001
    default_http_status = 409


class InvalidCredentialsError(UserError):
    """认证失败：用户名不存在或密码错误（统一口径，不泄露用户存在性）。"""

    default_code = 5002
    default_http_status = 401


class UserNotFoundError(UserError):
    """用户不存在。"""

    default_code = 5003
    default_http_status = 404


class UserInvalidParamError(UserError):
    """用户名/密码格式不合法。"""

    default_code = 5004
    default_http_status = 422
