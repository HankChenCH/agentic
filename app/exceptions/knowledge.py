"""知识库域业务异常（错误码 4xxx）。"""

from app.core.exceptions.business import BusinessError


class KnowledgeError(BusinessError):
    """知识库域通用业务错误。"""

    default_code = 4000


class KnowledgeNotFoundError(KnowledgeError):
    """知识库不存在。"""

    default_code = 4001
    default_http_status = 404


class KnowledgeNameDuplicatedError(KnowledgeError):
    """知识库名称已存在。"""

    default_code = 4002
    default_http_status = 409


class KnowledgeStatusError(KnowledgeError):
    """知识库状态迁移不合法（如 pending 状态下启用）。"""

    default_code = 4003


class KnowledgeDocumentNotFoundError(KnowledgeError):
    """知识库文档不存在。"""

    default_code = 4004
    default_http_status = 404


class KnowledgeDocumentStatusError(KnowledgeError):
    """知识库文档状态迁移不合法。"""

    default_code = 4005


class KnowledgeDocumentInvalidError(KnowledgeError):
    """知识库文档内容非法（空文件、超出大小限制、非法文件名等）。"""

    default_code = 4006


class KnowledgeSegmentNotFoundError(KnowledgeError):
    """知识库文档分段不存在。"""

    default_code = 4007
    default_http_status = 404


class KnowledgeSegmentStateError(KnowledgeError):
    """分段状态冲突（所属文档处理中/删除中不可管理分段，或并发追加 position 冲突）。"""

    default_code = 4008
    default_http_status = 409

