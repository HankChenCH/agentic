"""智能体域业务异常（错误码 2xxx）。"""

from app.core.exceptions.business import BusinessError


class AgentError(BusinessError):
    """智能体域通用业务错误。"""

    default_code = 2000


class AgentNotFoundError(AgentError):
    """智能体不存在。"""

    default_code = 2001
    default_http_status = 404
