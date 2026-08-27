from dataclasses import dataclass
from uuid import UUID

from wireup import injectable

from app.components.memory import MemoryService
from app.core.logging import LoggerFactory
from app.repositories.conversation_repository import ConversationRepository
from app.services.conversation_title_generator import ConversationTitleGenerator

from app.models.domain.agentic import (
    AgenticConversation,
    AgenticConversationMessage,
    AgenticConversationTurn,
)


@injectable
@dataclass
class TurnFinalizer:
    """chat 一轮结束后的收尾加工（流收尾后由 AgenticService 在后台 daemon 线程调用）。

    三步固定顺序执行，容错语义各不相同，故不做统一的阶段抽象：
    - 填标题：仅首轮（conversation_title 为空）调 LLM；失败只记日志，
      标题留空、下次 chat 重试；
    - 聚合 token 用量并落库轮次行：必须成功，失败交给外层兜底；
    - 写长期记忆：失败不影响主链路（与标题同款容错策略）。
    """

    title_generator: ConversationTitleGenerator
    conversation_repo: ConversationRepository
    memory: MemoryService
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
                        conversation.conversation_title = title
                        self.conversation_repo.store_conversation(conversation)
                except Exception:
                    # 标题生成失败不影响 turn 落库；会话标题仍为空，下次 chat 会重试
                    self.logger.exception("generate conversation_title failed")

            # 聚合本轮各条消息的用量（只收三个标准键，其余如 cached_tokens 不计）
            usage = dict(turn.token_usage)
            for msg in turn_messages:
                for key, value in msg.token_usage.items():
                    if key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
                        usage[key] = usage.get(key, 0) + value
            turn.token_usage = usage
            self.conversation_repo.store_conversation_turn(turn)

            try:
                self.memory.remember(query=query, turn_messages=turn_messages, thread_id=conversation.thread_id, turn_id=turn.turn_id)
            except Exception:
                self.logger.exception("memory remember failed")
        except Exception:
            self.logger.exception("after chat post-processing failed")
