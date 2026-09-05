"""会话域端口（依赖倒置）：协议住领域层，实现由 adapters 回填。

仓储协议（``ConversationRepositoryPort``）的实现住
``app/adapters/persistence/conversation_repository.py``；模型网关协议
（``ChatModelGateway``）的实现住 ``app/adapters/llm/gateway.py``——机制
（SQLModel 会话、供应商 SDK 客户端）不进 domain，编排与领域服务只依赖
本文件的协议面，wireup 经 ``@injectable(as_type=...)`` 按协议类型注入。
``ChatModel`` 的消息信封引用 langchain-core 抽象（领域侧框架白名单：
消息信封类型，非供应商 SDK）。
"""

from typing import List, Protocol, Sequence, Tuple
from uuid import UUID

from langchain_core.messages import BaseMessage

from app.models.domain.agentic import (
    AgenticConversation,
    AgenticConversationMessage,
    AgenticConversationTurn,
)


class ConversationRepositoryPort(Protocol):
    """会话聚合数据访问协议（归属过滤在实现查询条件内强制）。"""

    def list_conversations(
        self, user_id: UUID, page: int, page_size: int
    ) -> Tuple[List[AgenticConversation], int]: ...

    def get_conversation(self, thread_id: UUID, user_id: UUID) -> AgenticConversation | None: ...

    def find_conversation(self, thread_id: UUID) -> AgenticConversation | None: ...

    def open_turn(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
        agentic_id: str,
        rebind: bool,
        run_id: str,
        turn_id: UUID,
        parent_turn_id: UUID | None,
        attempt_no: int,
        content: List[dict],
    ) -> Tuple[AgenticConversation, AgenticConversationTurn]: ...

    def store_conversation(self, conversation: AgenticConversation) -> AgenticConversation: ...

    def delete_conversation(self, thread_id: UUID, user_id: UUID) -> AgenticConversation | None: ...

    def list_thread_turns(self, thread_id: UUID) -> List[AgenticConversationTurn]: ...

    def map_turn_user_contents(self, thread_id: UUID) -> dict[UUID, List]: ...

    def find_turn_id_by_message_id(self, thread_id: UUID, message_id: UUID) -> UUID | None: ...

    def complete_turn(self, turn: AgenticConversationTurn) -> None: ...

    def activate_turn(self, thread_id: UUID, turn_id: UUID) -> AgenticConversation: ...

    def store_conversation_turn(
        self, conversation_turn: AgenticConversationTurn
    ) -> AgenticConversationTurn: ...

    def store_conversation_messages(self, messages: List[AgenticConversationMessage]) -> None: ...

    def list_replay_messages(
        self, thread_id: UUID, active_turn_ids: List[UUID], turn_limit: int = 20
    ) -> List[AgenticConversationMessage]: ...


class ChatModel(Protocol):
    """裸聊天模型的最小调用面（一次性内部任务：标题生成等）。

    结构匹配 langchain 的 ``BaseChatModel.invoke``——实现侧无需继承任何
    基类，测试替身同理。
    """

    def invoke(self, messages: Sequence[BaseMessage]) -> BaseMessage: ...


class ChatModelGateway(Protocol):
    """内部裸模型的获取网关：按 llm 配置的 default entry 每次创建。

    领域不感知供应商与工厂细节；将来要为特定用例单独配模型时，在实现侧
    扩展为按用途路由即可（参照 memory.extraction_provider 的先例）。
    """

    def create(self) -> ChatModel: ...
