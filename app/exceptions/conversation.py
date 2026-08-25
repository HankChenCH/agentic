"""会话域业务异常（错误码 1xxx）。"""

from app.core.exceptions.business import BusinessError


class ConversationError(BusinessError):
    """会话域通用业务错误。"""

    default_code = 1000


class ConversationNotFoundError(ConversationError):
    """会话不存在。"""

    default_code = 1001
    default_http_status = 404
