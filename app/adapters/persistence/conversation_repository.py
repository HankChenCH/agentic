from typing import List, Tuple
from dataclasses import dataclass

from uuid import UUID
from wireup import injectable
from sqlmodel import Session, col, delete, select, update
from sqlalchemy import Engine, func

from app.models.domain.agentic import AgenticConversation, AgenticConversationTurn, AgenticConversationMessage, AgenticTurnStatus

@injectable
@dataclass
class ConversationRepository:
    engine: Engine

    def list_conversations(self, user_id: UUID, page: int, page_size: int) -> Tuple[List[AgenticConversation], int]:
        # 归属过滤在查询条件内强制：非本人会话不出现在列表与计数中
        offset = (page - 1) * page_size
        with Session(self.engine, expire_on_commit=False) as session:
            total = session.exec(
                select(func.count()).select_from(AgenticConversation).where(AgenticConversation.user_id == user_id)
            ).one()
            statement = (
                select(AgenticConversation)
                .where(AgenticConversation.user_id == user_id)
                .offset(offset)
                .limit(page_size)
                .order_by(col(AgenticConversation.id).desc())
            )
            result = session.exec(statement).all()
            session.commit()

        return list(result), int(total)

    def get_conversation(self, thread_id: UUID, user_id: UUID) -> AgenticConversation | None:
        # 纯查询（describe 只读语义）；非本人会话与不存在同返回 None，由上层统一 404
        with Session(self.engine, expire_on_commit=False) as session:
            conversation = session.exec(
                select(AgenticConversation)
                .where(AgenticConversation.thread_id == thread_id)
                .where(AgenticConversation.user_id == user_id)
            ).first()
            session.commit()
        return conversation

    def init_conversation(self, user_id: UUID, thread_id: UUID, agentic_id: str) -> AgenticConversation:
        # get-or-create：仅用于 chat() 的隐式建会话（describe 不应再走这里）。
        # 已存在时不校验归属——他人会话的拦截是业务规则，由上层
        # （ConversationService.open_turn）比对 user_id 后统一 404。
        # expire_on_commit=False：commit 后对象属性不失效，离开 session（detached）
        # 也能被上层安全访问，避免 DetachedInstanceError。
        with Session(self.engine, expire_on_commit=False) as session:
            conversation = session.exec(
                select(AgenticConversation).where(AgenticConversation.thread_id == thread_id)
            ).first()
            if conversation is None:
                conversation = AgenticConversation(user_id=user_id, thread_id=thread_id, agentic_id=agentic_id, conversation_title="")
                session.add(conversation)

            session.commit()
            session.refresh(conversation)

        return conversation

    def store_conversation(self, conversation: AgenticConversation) -> AgenticConversation:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

        return conversation

    def delete_conversation(self, thread_id: UUID, user_id: UUID) -> AgenticConversation | None:
        # 硬删除：会话+轮次+消息单事务清空；非本人会话与不存在同返回 None（由上层
        # 决定 404）。三表间无外键级联，须按 thread_id 显式删；messages 先于 turns
        # （message 有 FK 指向 turn，postgres 下先删父行会违反约束）。
        with Session(self.engine, expire_on_commit=False) as session:
            conversation = session.exec(
                select(AgenticConversation)
                .where(AgenticConversation.thread_id == thread_id)
                .where(AgenticConversation.user_id == user_id)
            ).first()
            if conversation is None:
                return None

            session.exec(delete(AgenticConversationMessage).where(AgenticConversationMessage.thread_id == thread_id))
            session.exec(delete(AgenticConversationTurn).where(AgenticConversationTurn.thread_id == thread_id))
            session.delete(conversation)
            session.commit()

        return conversation

    def create_conversation_turn(self, conversation: AgenticConversation, run_id: str, turn_id: UUID) -> AgenticConversationTurn:
        with Session(self.engine, expire_on_commit=False) as session:
            conversation_turn = AgenticConversationTurn(thread_id=conversation.thread_id, run_id=run_id, turn_id=turn_id, turn_num=0)
            last_turn = session.exec(
                select(AgenticConversationTurn)
                .where(AgenticConversationTurn.thread_id == conversation.thread_id)
                .where(AgenticConversationTurn.turn_id == conversation.current_turn_id)
            ).first()
            if last_turn is not None:
                conversation_turn.turn_num = last_turn.turn_num + 1
            session.add(conversation_turn)

            conversation.current_turn_id = turn_id
            session.exec(update(AgenticConversation).where(col(AgenticConversation.thread_id) == conversation.thread_id).values(current_turn_id=turn_id))

            session.commit()
            session.refresh(conversation_turn)

        return conversation_turn

    def store_conversation_turn(self, conversation_turn: AgenticConversationTurn) -> AgenticConversationTurn:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(conversation_turn)
            session.commit()
            session.refresh(conversation_turn)

        return conversation_turn    

    def list_conversation_history_turns(self, thread_id: UUID, offset: int | None, limit: int) -> Tuple[List[AgenticConversationTurn], int]:
        # 归属由调用方先行校验（list_history_messages 先 describe 再进来），此处按
        # thread_id 取数即可
        with Session(self.engine, expire_on_commit=False) as session:
            total = session.exec(
                select(func.count()).select_from(AgenticConversationTurn).where(AgenticConversationTurn.thread_id == thread_id)
            ).one()
            statement = session.exec(
                select(AgenticConversationTurn).
                where(AgenticConversationTurn.thread_id == thread_id).
                order_by(col(AgenticConversationTurn.turn_num).desc()).
                offset(offset if offset is not None else 0).
                limit(limit)
            )

            turns = statement.all()

            session.commit()

        return list(turns), int(total)
    
    def store_conversation_message(self, message: AgenticConversationMessage) -> AgenticConversationMessage:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(message)
            session.commit()
            session.refresh(message)

        return message

    def store_conversation_messages(self, messages: List[AgenticConversationMessage]):
        with Session(self.engine, expire_on_commit=False) as session:
            session.add_all(messages)
            session.commit()
            for message in messages:
                session.refresh(message)

    def list_replay_messages(self, thread_id: UUID, exclude_turn_id: UUID, turn_limit: int = 20) -> List[AgenticConversationMessage]:
        """按旧→新返回可回放的多轮消息（仅 COMPLETED 轮次，含全部消息类型）。

        多轮上下文以库为准（服务端权威）：取最近 turn_limit 个 COMPLETED 轮次
        （排除 exclude_turn_id 指定的当前进行中轮次；FAILED/CANCELED 及悬挂
        RUNNING 轮次的半截数据不回放），行→模型消息的组装由上层负责。
        thread_id 的归属已由 open_turn（get-or-create + 归属比对）确立。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            turns = session.exec(
                select(AgenticConversationTurn)
                .where(AgenticConversationTurn.thread_id == thread_id)
                .where(col(AgenticConversationTurn.turn_id) != exclude_turn_id)
                .where(AgenticConversationTurn.status == AgenticTurnStatus.COMPLETED)
                .order_by(col(AgenticConversationTurn.turn_num).desc())
                .limit(turn_limit)
            ).all()
            session.commit()

        messages: List[AgenticConversationMessage] = []
        for turn in reversed(list(turns)):
            # 轮内按 sequence_num 排序（StorageTranslator 的严格递增计数器，忠实还原流顺序）
            messages.extend(sorted(turn.messages, key=lambda m: m.sequence_num))
        return messages
