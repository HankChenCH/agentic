"""业务异常：由业务编程用户按域定义与扩展。

错误码分段规划（int，0 为成功，沿用 Response 信封约定）：
1xxx 会话（conversation）、2xxx 智能体（agent）、3xxx 记忆（memory）、
4xxx 知识库（knowledge）、5xxx 用户（user）；新增业务域时在此追加分段
并保持全局唯一。

基类 :class:`~app.core.exceptions.business.BusinessError` 由框架提供
（:mod:`app.core.exceptions`）：继承它即获得全局异常处理器的透传语义
（任何环境 ``message`` 如实返回 + ``http_status`` 状态码）。
"""

from app.exceptions.agent import AgentError, AgentNotFoundError
from app.exceptions.conversation import (
    AttachmentNotFoundError,
    AttachmentTooLargeError,
    ConversationError,
    ConversationNotFoundError,
    TurnNotAtTipError,
    UnsupportedAttachmentTypeError,
)
from app.exceptions.knowledge import (
    KnowledgeDocumentInvalidError,
    KnowledgeDocumentNotFoundError,
    KnowledgeDocumentStatusError,
    KnowledgeError,
    KnowledgeNameDuplicatedError,
    KnowledgeNotFoundError,
    KnowledgeSegmentNotFoundError,
    KnowledgeSegmentStateError,
    KnowledgeStatusError,
)
from app.exceptions.memory import (
    MemoryComponentError,
    MemoryConstraintConflictError,
    MemoryInvalidParamError,
    MemoryInvalidTimeParamError,
    MemoryNameConflictError,
    MemoryNoChangeError,
    MemoryObjectNotFoundError,
    MemoryProtectedObjectError,
)
from app.exceptions.user import (
    InvalidCredentialsError,
    UserError,
    UserInvalidParamError,
    UserNotFoundError,
    UsernameDuplicatedError,
)

__all__ = [
    "ConversationError",
    "ConversationNotFoundError",
    "TurnNotAtTipError",
    "UnsupportedAttachmentTypeError",
    "AttachmentTooLargeError",
    "AttachmentNotFoundError",
    "AgentError",
    "AgentNotFoundError",
    "MemoryComponentError",
    "MemoryInvalidTimeParamError",
    "MemoryObjectNotFoundError",
    "MemoryProtectedObjectError",
    "MemoryNameConflictError",
    "MemoryNoChangeError",
    "MemoryConstraintConflictError",
    "MemoryInvalidParamError",
    "KnowledgeError",
    "KnowledgeNotFoundError",
    "KnowledgeNameDuplicatedError",
    "KnowledgeStatusError",
    "KnowledgeDocumentNotFoundError",
    "KnowledgeDocumentStatusError",
    "KnowledgeDocumentInvalidError",
    "KnowledgeSegmentNotFoundError",
    "KnowledgeSegmentStateError",
    "UserError",
    "UsernameDuplicatedError",
    "InvalidCredentialsError",
    "UserNotFoundError",
    "UserInvalidParamError",
]
