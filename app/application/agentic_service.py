from dataclasses import dataclass
from datetime import datetime
from time import time
import threading

from uuid import uuid4, UUID
from wireup import injectable

from langchain.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages import BaseMessage

from app.core.config import get_environment
from app.core.exceptions import BusinessError
from app.core.logging import LoggerFactory
from app.agents import AgentFactory, AgentRunContext
from .translator import AgUiTranslator, StorageTranslator
from .turn_finalizer import TurnFinalizer
from app.repositories.conversation_repository import ConversationRepository

from app.models.domain.agentic import (
    AgenticTurnStatus,
    AgenticMessageRole,
    AgenticMessageType,
    AgenticConversation,
    AgenticConversationTurn,
    AgenticConversationMessage,
)


@injectable
@dataclass
class AgenticService:

    agent_factory: AgentFactory

    conversation_repo: ConversationRepository

    turn_finalizer: TurnFinalizer

    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def list_conversations(self, page: int, page_size: int):
        conversations, total = self.conversation_repo.list_conversations(page=page, page_size=page_size)
        return {"items": conversations, "total": total, "page": page, "pageSize": page_size}

    def describe_conversation(self, thread_id: UUID):
        # 只读：不存在返回 None，由 endpoint 决定 404（不再隐式创建）
        return self.conversation_repo.get_conversation(thread_id=thread_id)

    def delete_conversation(self, thread_id: UUID):
        # 硬删除：会话+轮次+消息单事务清空；不存在返回 None，由 endpoint 决定 404。
        # 长期记忆（agentic_memory）是跨会话的用户级数据，不随会话删除。
        return self.conversation_repo.delete_conversation(thread_id=thread_id)

    def list_conversation_history_messages(self, thread_id: UUID, offset: int | None = None, limit: int = 20):
        # 仓库按 id desc 取（最新一页），这里反转为旧→新以便前端顺序渲染
        turns, total = self.conversation_repo.list_conversation_history_turns(thread_id, offset, limit)
        turns.reverse()
        return {"items": turns, "total": total, "offset": offset or 0, "limit": limit}

    def chat(self, thread_id: UUID, run_id: str, query: str):
        # 生成一个新的 turn_id，作为本次会话轮次的唯一标识
        turn_id = uuid4()
        now = datetime.fromtimestamp(time())

        # 双翻译共存于同一次 interleave 遍历：
        # - ag_ui_translator：逐 delta 产出 ag-ui SSE 事件（流式）
        # - storage_translator：累积成完整 AgenticMessage（落库），不流式
        ag_ui_translator = AgUiTranslator(thread_id=thread_id, run_id=run_id)
        storage_translator = StorageTranslator(thread_id=thread_id, turn_id=turn_id)

        # RunStarted 不依赖任何外部状态，先发帧再干活：SSE 响应头一旦发出
        # （200 + text/event-stream）全局异常处理器就不再生效，后续任何异常
        # 只能以 RunErrorEvent 收尾——保证客户端总能看到完整的
        # Started → (Finished | Error) 生命周期，而不是 200 之后裸断流
        yield ag_ui_translator.start()

        turn: AgenticConversationTurn | None = None
        try:
            # 初始化会话（get-or-create）和会话轮次（create），并存储用户消息
            conversation = self.conversation_repo.init_conversation(
                thread_id=thread_id,
                agentic_id=self.agent_factory.default_agentic_id,
            )
            turn = self.conversation_repo.create_conversation_turn(conversation=conversation, run_id=run_id, turn_id=turn_id)
            self.conversation_repo.store_conversation_message(AgenticConversationMessage(
                thread_id=conversation.thread_id,
                turn_id=turn.turn_id,
                message_id=uuid4(),
                sequence_num=0,
                role=AgenticMessageRole.USER,
                message_type=AgenticMessageType.MESSAGE,
                content=[{"type": "text", "text": query}],
                token_usage={},
                latency_ms=0
            ))

            # 智能体实例由智能体层工厂创建（按会话绑定的 agentic_id，未注册回退默认）
            agent = self.agent_factory.create(conversation.agentic_id)

            # 多轮历史以库为准（服务端权威，不信任请求携带的历史）：
            # 最近轮次的 USER/ASSISTANT 文本 + 当前提问
            history = self._load_history_messages(thread_id=thread_id, exclude_turn_id=turn.turn_id)
            run = agent.stream(AgentRunContext(
                messages=[*history, HumanMessage(query)],
                thread_id=str(thread_id),
                run_id=run_id,
                now=now,
            ))
        except Exception as e:
            # 准备段异常兜底：轮次行可能已建（status=RUNNING），不收口会永远
            # 停在 RUNNING。先置 FAILED 落库、后发错误帧——yield 挂起期间
            # 客户端断连会向本生成器抛 GeneratorExit，yield 之后的代码不再执行
            self._log_stream_error("chat 准备段异常", e)
            if turn is not None:
                turn.status = AgenticTurnStatus.FAILED
                self._store_turn_safely(turn)
            yield ag_ui_translator.error(self._run_error_message(e))
            return

        # messages item 是整条 assistant 消息的 ChatModelStream（非片段），
        # 每条消息生成一次 id 注入两个 translator，保证流式事件与落库行共用同一 message_id
        message_id: UUID | None = None
        try:
            for name, item in run.interleave("messages", "tools"):
                message_id = uuid4()
                yield from ag_ui_translator.translate(name, item, message_id)
                storage_translator.translate(name, item, message_id)

            yield ag_ui_translator.finish()
            turn.status = AgenticTurnStatus.COMPLETED
            self.conversation_repo.store_conversation_turn(turn)
        except Exception as e:
            # 同准备段：先收口轮次状态，再发错误帧；对外消息按异常分级 +
            # 环境脱敏（不再透传裸 str(e)，策略见 _run_error_message）
            self._log_stream_error("chat 流式段异常", e)
            turn.status = AgenticTurnStatus.FAILED
            self._store_turn_safely(turn)
            yield ag_ui_translator.error(self._run_error_message(e))

        # 流结束后批量落库 assistant 消息
        if len(storage_translator.messages) > 0:
            self.conversation_repo.store_conversation_messages(storage_translator.messages)

        # 收尾加工含标题生成（LLM 调用），放后台线程避免拖住 SSE 连接收尾
        threading.Thread(
            target=self.turn_finalizer.run,
            args=(conversation, turn, storage_translator.messages, query),
            daemon=True,
        ).start()

    def _run_error_message(self, exc: Exception) -> str:
        """RunErrorEvent 的对外消息，策略与全局异常处理器（api/exception_handlers.py）
        一致：业务异常是预期内错误，message 如实透出；框架/基础/未知异常在
        dev/test 如实返回、prod 统一「服务内部错误」，避免连接串、厂商报错等
        内部细节经 SSE 泄漏给客户端。
        """
        if isinstance(exc, BusinessError):
            return exc.message
        if get_environment() == "prod":
            return "服务内部错误"
        return str(exc) or type(exc).__name__

    def _log_stream_error(self, stage: str, exc: Exception) -> None:
        """流内异常落日志：业务异常按 warning（预期内，不打堆栈噪音），
        其余按 error 带完整堆栈（日志永远不脱敏，与全局处理器同一口径）。"""
        if isinstance(exc, BusinessError):
            self.logger.warning("%s: [%s] %s", stage, exc.code, exc.message)
        else:
            self.logger.error("%s: %s", stage, exc, exc_info=exc)

    def _store_turn_safely(self, turn: AgenticConversationTurn) -> None:
        """失败收口写库的兜底：写不进去（如存储已不可用）只记日志不再上抛，
        免得收尾的二次异常顶掉本要发给客户端的 RunErrorEvent。"""
        try:
            self.conversation_repo.store_conversation_turn(turn)
        except Exception:
            self.logger.exception("轮次失败状态落库失败 turn_id=%s", turn.turn_id)

    def _load_history_messages(self, thread_id: UUID, exclude_turn_id: UUID) -> list[BaseMessage]:
        """从库如实组装多轮回放消息，排除当前轮次与未完成（非 COMPLETED）轮次。

        按厂商建议携带完整工具调用链：TOOL_CALL → AIMessage(tool_calls)、
        TOOL_RESULT → ToolMessage，两者成对回放——OpenAI 兼容 API 要求
        tool_calls 消息后必须紧跟对应 tool 消息，故未配对的调用/结果行跳过
        （工具出错时不落 TOOL_RESULT 行，孤儿调用直接回放会 400）。
        THOUGHT（reasoning）不回放：厂商 API 不接受历史 reasoning_content 注入。
        """
        rows = self.conversation_repo.list_replay_messages(thread_id=thread_id, exclude_turn_id=exclude_turn_id)

        history: list[BaseMessage] = []
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