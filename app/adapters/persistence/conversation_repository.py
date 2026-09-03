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
        # get-or-create：仅用于 run() 的隐式建会话（describe 不应再走这里）。
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

    def update_agent_binding(self, thread_id: UUID, user_id: UUID, agentic_id: str) -> AgenticConversation:
        """切换会话绑定的智能体（归属内强制：他人会话按不存在处理返回 None 语义
        由上层前置比对，这里带 user_id 条件双保险）。"""
        with Session(self.engine, expire_on_commit=False) as session:
            conversation = session.exec(
                select(AgenticConversation).where(
                    AgenticConversation.thread_id == thread_id,
                    AgenticConversation.user_id == user_id,
                )
            ).first()
            if conversation is None:
                raise ValueError(f"conversation not found: {thread_id}")
            conversation.agentic_id = agentic_id
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

    def create_conversation_turn(
        self,
        conversation: AgenticConversation,
        run_id: str,
        turn_id: UUID,
        parent_turn_id: UUID | None,
        attempt_no: int,
    ) -> AgenticConversationTurn:
        """创建轮次行：turn_num 为线程内插入序（max+1）；不动会话的活跃叶子——
        current_turn_id 只在轮次 COMPLETED 时推进（complete_turn），重试失败时
        旧答案保持活跃。分支参数（parent/attempt）由领域层解析后传入。"""
        with Session(self.engine, expire_on_commit=False) as session:
            last_turn_num = session.exec(
                select(func.max(AgenticConversationTurn.turn_num))
                .where(AgenticConversationTurn.thread_id == conversation.thread_id)
            ).one()
            conversation_turn = AgenticConversationTurn(
                thread_id=conversation.thread_id,
                run_id=run_id,
                turn_id=turn_id,
                turn_num=(last_turn_num + 1) if last_turn_num is not None else 0,
                parent_turn_id=parent_turn_id,
                attempt_no=attempt_no,
            )
            session.add(conversation_turn)
            session.commit()
            session.refresh(conversation_turn)

        return conversation_turn

    def list_thread_turns(self, thread_id: UUID) -> List[AgenticConversationTurn]:
        """线程内全部轮次，创建序（旧→新）。数量有界（会话级），分支/活跃路径
        计算与历史过滤都在这份全量骨架上进行。"""
        with Session(self.engine, expire_on_commit=False) as session:
            turns = session.exec(
                select(AgenticConversationTurn)
                .where(AgenticConversationTurn.thread_id == thread_id)
                .order_by(col(AgenticConversationTurn.turn_num).asc(), col(AgenticConversationTurn.id).asc())
            ).all()
            session.commit()
        return list(turns)

    def map_turn_user_contents(self, thread_id: UUID) -> dict[UUID, List]:
        """turn_id → 该轮用户消息（存储形态 content）。用户行固定 sequence_num=0
        （open_turn 先写用户行），以 0 号行定位，缺行（异常轮次）不出现在映射中。"""
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(AgenticConversationMessage)
                .where(AgenticConversationMessage.thread_id == thread_id)
                .where(AgenticConversationMessage.sequence_num == 0)
            ).all()
            session.commit()
        return {row.turn_id: row.content for row in rows}

    def find_turn_id_by_message_id(self, thread_id: UUID, message_id: UUID) -> UUID | None:
        """按消息 id 定位所属轮次（显式分支信号 baseMessageId 的解析）。

        消息 id 与流式侧共用（注入 message_id），assistant 行在 live 与历史
        重载两种客户端形态下都携带库中 id，定位可靠；跨会话/不存在的 id 返回
        None（调用方降级为自动检测）。"""
        with Session(self.engine, expire_on_commit=False) as session:
            row = session.exec(
                select(AgenticConversationMessage)
                .where(AgenticConversationMessage.thread_id == thread_id)
                .where(AgenticConversationMessage.message_id == message_id)
            ).first()
            session.commit()
        return row.turn_id if row is not None else None

    def complete_turn(self, turn: AgenticConversationTurn) -> None:
        """轮次完成收口：置 COMPLETED 并把会话活跃叶子推进到本轮次（单事务）。
        失败/取消不走这里——叶子保持原位，被重试轮次经 parent 链仍活跃。"""
        with Session(self.engine, expire_on_commit=False) as session:
            turn.status = AgenticTurnStatus.COMPLETED
            session.add(turn)
            session.exec(
                update(AgenticConversation)
                .where(col(AgenticConversation.thread_id) == turn.thread_id)
                .values(current_turn_id=turn.turn_id)
            )
            session.commit()

    def activate_turn(self, thread_id: UUID, turn_id: UUID) -> AgenticConversation:
        """把活跃叶子切换到指定轮次（末梢扇形内的变体切换持久化）。

        只写 current_turn_id——后续 open_turn 的 parent 与 replay 的活跃路径
        都从这个叶子派生。tip 校验（目标须为叶子或其兄弟）在领域层完成，
        这里是纯写路径。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            conversation = session.exec(
                select(AgenticConversation).where(AgenticConversation.thread_id == thread_id)
            ).first()
            if conversation is None:
                return None
            conversation.current_turn_id = turn_id
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
        return conversation

    def store_conversation_turn(self, conversation_turn: AgenticConversationTurn) -> AgenticConversationTurn:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(conversation_turn)
            session.commit()
            session.refresh(conversation_turn)

        return conversation_turn    

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

    def list_replay_messages(self, thread_id: UUID, active_turn_ids: List[UUID], turn_limit: int = 20) -> List[AgenticConversationMessage]:
        """按旧→新返回可回放的多轮消息（仅活跃路径上的 COMPLETED 轮次）。

        多轮上下文以库为准（服务端权威）：``active_turn_ids`` 是沿当前轮次
        parent 链回溯出的活跃路径（branching.ancestor_chain 的产物，已含顺序），
        只保留其中 COMPLETED 的最近 turn_limit 轮——被重试替换的旧答案不在
        链上自然出局；FAILED/CANCELED 及悬挂 RUNNING 轮次的半截数据不回放。
        行→模型消息的组装由上层负责；thread_id 的归属已由 open_turn 确立。
        """
        if not active_turn_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            turns = session.exec(
                select(AgenticConversationTurn)
                .where(AgenticConversationTurn.thread_id == thread_id)
                .where(col(AgenticConversationTurn.turn_id).in_(active_turn_ids))
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
