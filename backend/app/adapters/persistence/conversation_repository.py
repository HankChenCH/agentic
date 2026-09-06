from typing import List, Tuple
from dataclasses import dataclass

from uuid import UUID, uuid4
from wireup import injectable
from sqlmodel import Session, col, delete, select, update
from sqlalchemy import Engine, func
from sqlalchemy.exc import IntegrityError

from app.domain.conversation.ports import ConversationRepositoryPort
from app.domain.ports import RepositoryConflictError
from app.models.domain.agentic import (
    AgenticConversation,
    AgenticConversationTurn,
    AgenticConversationMessage,
    AgenticMessageRole,
    AgenticMessageType,
    AgenticTurnStatus,
)

@injectable(as_type=ConversationRepositoryPort)
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

    def find_conversation(self, thread_id: UUID) -> AgenticConversation | None:
        """按 thread_id 读会话（不带归属过滤）——open_turn 的分支定位前置读。

        他人会话的统一 404 口径由调用方比对 user_id 完成（不泄露存在性）。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            conversation = session.exec(
                select(AgenticConversation).where(AgenticConversation.thread_id == thread_id)
            ).first()
            session.commit()
        return conversation

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
    ) -> Tuple[AgenticConversation, AgenticConversationTurn]:
        """开轮写路径单事务：get-or-create 会话（+显式绑定切换）+ 建轮次 + 落
        sequence_num=0 的用户消息，一个 Session 整体提交——任一步失败全部
        回滚，不产生「有轮次无用户消息」的半截状态（0 号用户行是
        map_turn_user_contents 与分支快照的定位依据）。

        分支定位（parent/attempt）与归属判定的业务决策在领域层完成，这里
        只接收解析产物执行；事务内对他人会话再校验一次（与归属判定的竞态
        窗口内整体回滚，ValueError 惯例同前）。turn_num 为线程内插入序
        （max+1）；不动 current_turn_id——活跃叶子只在 complete_turn 推进。

        并发边界：run_id 幂等由 (thread_id, run_id) 唯一约束（uq_turn_thread_run）
        在库层硬保证——同会话重复提交的 INSERT 在 commit 时触发 IntegrityError，
        在此翻译为契约级 RepositoryConflictError（本事务写集上唯一的约束就是它，
        裸翻译安全；驱动异常不出适配器边界）。其余不提供隔离：分支定位的读在
        服务层事务外完成；max(turn_num) 虽在本事务内但是普通快照读——无锁，
        (thread_id, parent_turn_id, attempt_no) 与 turn_num 亦无唯一约束，
        同一会话用不同 run_id 并发开轮仍可产生重复兄弟序号/turn_num。正确性
        由部署现实兜底（uvicorn 单进程 + 客户端不同时对同一会话发 run），不是
        数据库保证；需要硬保证时加部分唯一索引（同 parent 下 attempt 唯一）再谈。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            conversation = session.exec(
                select(AgenticConversation).where(AgenticConversation.thread_id == thread_id)
            ).first()
            if conversation is None:
                conversation = AgenticConversation(
                    user_id=user_id, thread_id=thread_id, agentic_id=agentic_id, conversation_title="",
                )
                session.add(conversation)
            elif conversation.user_id != user_id:
                raise ValueError(f"conversation not found: {thread_id}")
            elif rebind:
                conversation.agentic_id = agentic_id
                session.add(conversation)

            last_turn_num = session.exec(
                select(func.max(AgenticConversationTurn.turn_num))
                .where(AgenticConversationTurn.thread_id == thread_id)
            ).one()
            turn = AgenticConversationTurn(
                thread_id=thread_id,
                run_id=run_id,
                turn_id=turn_id,
                turn_num=(last_turn_num + 1) if last_turn_num is not None else 0,
                parent_turn_id=parent_turn_id,
                attempt_no=attempt_no,
            )
            session.add(turn)
            # turn 行先落：message 的 FK 指向 turn_id，PG 实时校验约束
            # turn 的 INSERT 在 flush 即执行，UNIQUE 约束（sqlite/PG 均非延迟）
            # 也在此刻抛 IntegrityError 而非 commit——翻译块必须罩住整段写路径
            try:
                session.flush()
                session.add(AgenticConversationMessage(
                    thread_id=thread_id,
                    turn_id=turn.turn_id,
                    message_id=uuid4(),
                    sequence_num=0,
                    role=AgenticMessageRole.USER,
                    message_type=AgenticMessageType.MESSAGE,
                    content=content,
                    token_usage={},
                    latency_ms=0,
                ))
                session.commit()
            except IntegrityError:
                # uq_turn_thread_run 冲突（重复 run_id 提交）：整体回滚（会话
                # get-or-create/改绑一并撤销），翻译为契约级冲突信号上抛
                session.rollback()
                raise RepositoryConflictError(
                    f"duplicate run_id {run_id} for thread {thread_id}"
                ) from None
            session.refresh(conversation)
            session.refresh(turn)

        return conversation, turn

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
        （开轮时与轮次行同事务原子写入），以 0 号行定位，缺行（异常轮次）不出现在映射中。"""
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
