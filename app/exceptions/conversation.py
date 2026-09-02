"""会话域业务异常（错误码 1xxx）。"""

from app.core.exceptions.business import BusinessError


class ConversationError(BusinessError):
    """会话域通用业务错误。"""

    default_code = 1000


class ConversationNotFoundError(ConversationError):
    """会话不存在。"""

    default_code = 1001
    default_http_status = 404


class UnsupportedAttachmentTypeError(ConversationError):
    """附件类型不受支持（本期仅图片）。"""

    default_code = 1010


class AttachmentTooLargeError(ConversationError):
    """附件超过大小上限。"""

    default_code = 1011
    default_http_status = 413


class AttachmentNotFoundError(ConversationError):
    """附件对象不存在（或不属于当前用户，按不存在处理不泄露存在性）。"""

    default_code = 1012
    default_http_status = 404
