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


class TurnNotAtTipError(ConversationError):
    """分支操作只允许发生在末梢（最新问答）。

    分支模型约束"主干 + 末梢一层扇形"：编辑/重新生成、变体切换都只作用于
    最新问答；沿任一变体继续对话后节点即定型（冻结），不支持在历史节点上
    开分支或切换。
    """

    default_code = 1013
