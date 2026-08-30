from dataclasses import dataclass

from wireup import injectable

from app.components.memory import MemoryConsolidationService
from app.core.logging import LoggerFactory
from app.services.domain.conversation.conversation_service import ConversationService
from app.services.domain.conversation.title_generator import ConversationTitleGenerator
from app.services.domain.user.ports import UserNodeSyncPort
from app.services.domain.user.user_service import UserService

from app.models.domain.agentic import (
    AgenticConversation,
    AgenticConversationMessage,
    AgenticConversationTurn,
)


@injectable
@dataclass
class TurnFinalizer:
    """agentic run 一轮结束后的收尾加工（流收尾后由 AgenticService 在后台 daemon 线程调用）。

    三步固定顺序执行，容错语义各不相同，故不做统一的阶段抽象：
    - 填标题：仅首轮（conversation_title 为空）调 LLM；失败只记日志，
      标题留空、下次 run 重试；
    - 聚合 token 用量并落库轮次行：必须成功，失败交给外层兜底；
    - 写长期记忆：失败不影响主链路（与标题同款容错策略）。
    """

    title_generator: ConversationTitleGenerator
    conversations: ConversationService
    memory: MemoryConsolidationService
    users: UserService
    memory_user_node: UserNodeSyncPort
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def run(
        self,
        conversation: AgenticConversation,
        turn: AgenticConversationTurn,
        turn_messages: list[AgenticConversationMessage],
        query: str,
    ) -> None:
        # 整体兜底：会话可能在流结束后被删除，后台线程对已删行的回写会在
        # daemon 线程里裸抛异常（threading 仅打印到 stderr），统一记日志吞掉。
        try:
            if conversation.conversation_title == "":
                try:
                    title = self.title_generator.generate(query, turn_messages)
                    if title:
                        self.conversations.record_title(conversation, title)
                except Exception:
                    # 标题生成失败不影响 turn 落库；会话标题仍为空，下次 run 会重试
                    self.logger.exception("generate conversation_title failed")

            self.conversations.record_turn_usage(turn, turn_messages)

            try:
                # 自愈刷新记忆「用户」节点的账号信息（资料变更/历史缺失都在
                # 此补齐）；失败不影响 remember
                try:
                    user = self.users.get_user(conversation.user_id)
                    self.memory_user_node.sync_user_node(
                        user.id, user.username, user.nickname,
                    )
                except Exception:
                    self.logger.exception("sync memory user node failed")
                self.memory.remember(
                    query=query,
                    turn_messages=turn_messages,
                    user_id=conversation.user_id,
                    thread_id=conversation.thread_id,
                    turn_id=turn.turn_id,
                )
            except Exception:
                self.logger.exception("memory remember failed")
        except Exception:
            self.logger.exception("after run post-processing failed")
