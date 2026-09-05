"""会话管理用例：会话列表/详情/历史/删除 + 分支变体切换 + 会话附件。

api 层唯一消费面；归属校验（他人资源 404 不泄露存在性）在领域层——
``ConversationService`` 的查询条件与 ``ConversationAttachmentStore`` 的
resolve_own_key 各自强制。附件的 HTTP 形态（302/降级回源/媒体类型推断）
留在 api，本门面只产出数据与决策。
"""

from dataclasses import dataclass
from uuid import UUID

from wireup import injectable

from app.domain.conversation import ConversationService
from app.domain.conversation.attachments import ConversationAttachmentStore
from app.exceptions import AttachmentNotFoundError


@dataclass(frozen=True)
class AttachmentDownload:
    """附件读取的决策载荷：``presigned`` 可用则 302，否则 ``key`` 回源。"""

    key: str
    presigned: str | None


@injectable
@dataclass
class ConversationAppService:
    """会话管理用例门面。"""

    conversations: ConversationService
    attachments: ConversationAttachmentStore

    # ---------------- 会话管理 ----------------

    def list_conversations(self, user_id: UUID, page: int, page_size: int) -> dict:
        return self.conversations.list_conversations(user_id=user_id, page=page, page_size=page_size)

    def describe_conversation(self, user_id: UUID, thread_id: UUID) -> dict:
        return self.conversations.describe_conversation(user_id=user_id, thread_id=thread_id)

    def list_history_messages(
        self, user_id: UUID, thread_id: UUID, offset: int, limit: int
    ) -> list:
        return self.conversations.list_history_messages(
            user_id=user_id, thread_id=thread_id, offset=offset, limit=limit
        )

    def delete_conversation(self, user_id: UUID, thread_id: UUID) -> dict:
        return self.conversations.delete_conversation(user_id=user_id, thread_id=thread_id)

    def activate_turn(self, user_id: UUID, thread_id: UUID, turn_id: UUID) -> dict:
        """末梢扇形内的分支变体切换（归属 + 已完成 + 末梢校验在领域层）。"""
        return self.conversations.activate_turn(user_id=user_id, thread_id=thread_id, turn_id=turn_id)

    # ---------------- 会话附件 ----------------

    def save_attachment(self, user_id: UUID, *, filename: str, content_type: str | None, stream) -> object:
        """上传会话附件（类型/大小校验在附件 store 内收口，流式转存）。"""
        return self.attachments.save(
            user_id=user_id, filename=filename, content_type=content_type, stream=stream,
        )

    def display_url(self, user_id: UUID, ref: str) -> str | None:
        """解析稳定引用为预签名展示地址；签名不可用（本地磁盘）返回 None。"""
        key = self.attachments.resolve_own_key(ref, user_id)
        if key is None:
            raise AttachmentNotFoundError("attachment not found")
        return self.attachments.presign(key)

    def resolve_download(self, user_id: UUID, attachment_path: str) -> AttachmentDownload:
        """附件读取决策：属主校验 + 预签名（不可用则由调用方按 key 回源）。"""
        key = self.attachments.resolve_own_key(attachment_path, user_id)
        if key is None:
            raise AttachmentNotFoundError("attachment not found")
        return AttachmentDownload(key=key, presigned=self.attachments.presign(key))

    def read_attachment(self, key: str) -> bytes:
        """按已归属校验的 key 读取字节（本地磁盘后端的降级回源通道）。"""
        return self.attachments.read(key)
