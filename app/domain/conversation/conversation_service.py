from dataclasses import dataclass
from time import monotonic
from uuid import UUID, uuid4
from typing import List, Tuple

from wireup import injectable
from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages import BaseMessage

from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.exceptions import ConversationNotFoundError
from app.repositories.conversation_repository import ConversationRepository
from app.services.domain.conversation.ports import CancelSignalStore

from app.models.domain.agentic import (
    AgenticConversation,
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticMessageRole,
    AgenticMessageType,
    AgenticTurnStatus,
)

# 计入轮次聚合的标准用量键（其余如 cached_tokens 不计）
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")

# 取消标志读失败警告的节流窗口（存储持续不可用时避免每 0.5s 刷一条日志）
_WARN_THROTTLE_SECONDS = 30.0


@injectable
@dataclass
class ConversationService:
    """会话聚合领域服务：持久化与业务规则的唯一门面。

    消费方按受众分流：chat 用户侧行程经编排层（ChatOrchestrator /
    TurnFinalizer）调用本服务；管理侧（会话增删查）与响应信封组装直接
    在此完成。存在性校验在服务内抛 ConversationNotFoundError——对齐
    知识域惯例，端点不再做 None 判断，HTTP 与后台拿到一致错误语义。
    """

    conversation_repo: ConversationRepository
    app_config: AppConfig
    cancel_signal_store: CancelSignalStore
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)
        self._last_warn_at = -_WARN_THROTTLE_SECONDS

    # ---- chat 行程所需写路径（原 AgenticService 准备段 / 收尾段下沉）----

    def open_turn(self, thread_id: UUID, run_id: str, query: str) -> Tuple[AgenticConversation, AgenticConversationTurn]:
        """get-or-create 会话（默认智能体兜底）+ 创建轮次 + 落库本轮用户消息。"""
        # 防御性清理遗留取消标志（上轮取消后 TTL 内残留会误杀本轮；正常收尾
        # 后标志本就应不存在，此处是无条件兜底）。清理失败不阻断开轮。
        try:
            self.cancel_signal_store.clear(thread_id)
        except Exception as exc:
            self.logger.warning("取消标志清理失败：%s", exc)
        conversation = self.conversation_repo.init_conversation(
            thread_id=thread_id,
            agentic_id=self.app_config.default_agentic_id,
        )
        turn = self.conversation_repo.create_conversation_turn(conversation=conversation, run_id=run_id, turn_id=uuid4())
        self.conversation_repo.store_conversation_message(AgenticConversationMessage(
            thread_id=conversation.thread_id,
            turn_id=turn.turn_id,
            message_id=uuid4(),
            sequence_num=0,
            role=AgenticMessageRole.USER,
            message_type=AgenticMessageType.MESSAGE,
            content=[{"type": "text", "text": query}],
            token_usage={},
            latency_ms=0,
        ))
        return conversation, turn

    def replay_history(self, thread_id: UUID, exclude_turn_id: UUID) -> List[BaseMessage]:
        """从库如实组装多轮回放消息，排除当前轮次与未完成（非 COMPLETED）轮次。

        按厂商建议携带完整工具调用链：TOOL_CALL → AIMessage(tool_calls)、
        TOOL_RESULT → ToolMessage，两者成对回放——OpenAI 兼容 API 要求
        tool_calls 消息后必须紧跟对应 tool 消息，故未配对的调用/结果行跳过
        （工具出错时不落 TOOL_RESULT 行，孤儿调用直接回放会 400）。
        THOUGHT（reasoning）不回放：厂商 API 不接受历史 reasoning_content 注入。
        """
        rows = self.conversation_repo.list_replay_messages(thread_id=thread_id, exclude_turn_id=exclude_turn_id)

        history: List[BaseMessage] = []
        pending_calls: dict[str, AIMessage] = {}
        for row in rows:
            part = row.content[0] if row.content else {}
            part_type = part.get("type")

            if part_type == "text":
                text = part.get("text", "")
                if not text:
                    continue
                if row.role == AgenticMessageRole.USER:
                    history.append(HumanMessage(content=text))
                elif row.message_type == AgenticMessageType.MESSAGE:
                    history.append(AIMessage(content=text))
            elif part_type == "tool_call":
                tool_call_id = part.get("tool_call_id", "")
                if not tool_call_id:
                    continue
                # 先缓冲，待配对的 TOOL_RESULT 到达时成对落列（保持与前后消息的相对顺序）
                pending_calls[tool_call_id] = AIMessage(content="", tool_calls=[{
                    "name": part.get("name", ""),
                    "args": part.get("args", {}),
                    "id": tool_call_id,
                    "type": "tool_call",
                }])
            elif part_type == "tool_result":
                tool_call_id = part.get("tool_call_id", "")
                call = pending_calls.pop(tool_call_id, None)
                if call is None:
                    continue
                history.append(call)
                history.append(ToolMessage(content=part.get("content", ""), tool_call_id=tool_call_id))

        return history

    def store_assistant_messages(self, messages: List[AgenticConversationMessage]) -> None:
        """流结束后批量落库 assistant 消息。"""
        if len(messages) > 0:
            self.conversation_repo.store_conversation_messages(messages)

    def complete_turn(self, turn: AgenticConversationTurn) -> None:
        turn.status = AgenticTurnStatus.COMPLETED
        self.save_turn(turn)

    def fail_turn(self, turn: AgenticConversationTurn) -> None:
        turn.status = AgenticTurnStatus.FAILED
        self.save_turn(turn)

    def cancel_turn(self, turn: AgenticConversationTurn) -> None:
        turn.status = AgenticTurnStatus.CANCELED
        self.save_turn(turn)

    # ---- 显式取消通道（REST cancel 接口写、流式循环/工具入口读）----

    def cancel_run_flag(self, thread_id: UUID) -> None:
        """置 thread 作用域取消标志（存储失败异常上抛，由端点反馈调用方）。"""
        self.cancel_signal_store.cancel(thread_id)

    def is_run_canceled(self, thread_id: UUID) -> bool:
        """读取消标志。宽容策略归领域：存储不可用绝不阻断聊天主链路——
        警告（节流）+ 视为未取消，降级为仅断链取消。"""
        try:
            return self.cancel_signal_store.is_canceled(thread_id)
        except Exception as exc:
            now = monotonic()
            if now - self._last_warn_at >= _WARN_THROTTLE_SECONDS:
                self._last_warn_at = now
                self.logger.warning("取消标志读取失败（视为未取消）：%s", exc)
            return False

    def save_turn(self, turn: AgenticConversationTurn) -> None:
        self.conversation_repo.store_conversation_turn(turn)

    def record_title(self, conversation: AgenticConversation, title: str) -> None:
        """标题生成成功后的回写（收尾链路专用）。"""
        conversation.conversation_title = title
        self.conversation_repo.store_conversation(conversation)

    def record_turn_usage(self, turn: AgenticConversationTurn, turn_messages: List[AgenticConversationMessage]) -> None:
        """聚合本轮各条消息的用量并落库（只收标准键，见 USAGE_KEYS）。"""
        usage = dict(turn.token_usage)
        for msg in turn_messages:
            for key, value in msg.token_usage.items():
                if key in USAGE_KEYS:
                    usage[key] = usage.get(key, 0) + value
        turn.token_usage = usage
        self.save_turn(turn)

    # ---- 管理侧读路径（/agentic/conversation* 端点的直接消费面）----

    def list_conversations(self, page: int, page_size: int):
        conversations, total = self.conversation_repo.list_conversations(page=page, page_size=page_size)
        return {"items": conversations, "total": total, "page": page, "pageSize": page_size}

    def describe_conversation(self, thread_id: UUID) -> AgenticConversation:
        conversation = self.conversation_repo.get_conversation(thread_id=thread_id)
        if conversation is None:
            raise ConversationNotFoundError("conversation not found")
        return conversation

    def delete_conversation(self, thread_id: UUID) -> AgenticConversation:
        # 硬删除：会话+轮次+消息单事务清空，返回删除前快照。
        # 长期记忆（components/memory 双层图谱模型）是跨会话的用户级数据，不随会话删除。
        conversation = self.conversation_repo.delete_conversation(thread_id=thread_id)
        if conversation is None:
            raise ConversationNotFoundError("conversation not found")
        return conversation

    def list_history_messages(self, thread_id: UUID, offset: int | None = None, limit: int = 20):
        # 仓库按 id desc 取（最新一页），这里反转为旧→新以便前端顺序渲染
        turns, total = self.conversation_repo.list_conversation_history_turns(thread_id, offset, limit)
        turns.reverse()
        return {"items": turns, "total": total, "offset": offset or 0, "limit": limit}
