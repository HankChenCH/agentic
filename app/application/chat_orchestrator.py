from dataclasses import dataclass
from datetime import datetime
from time import time
import threading

from uuid import UUID, uuid4
from wireup import injectable

from langchain.messages import HumanMessage

from app.core.config import get_environment
from app.core.exceptions import BusinessError
from app.core.logging import LoggerFactory
from app.agents import AgentFactory, AgentRunContext
from app.components.memory import MemoryService

from .translator import AgUiTranslator, StorageTranslator
from .turn_finalizer import TurnFinalizer
from app.services.domain.conversation.conversation_service import ConversationService

from app.models.domain.agentic import AgenticConversationTurn


@injectable
@dataclass
class ChatOrchestrator:
    """chat 用户侧行程编排：SSE 事件流、轮次生命周期与收尾触发的唯一归属。

    只做行程控制，不触碰持久化——会话/轮次/消息的读写规则全部在领域层
    ``ConversationService``。流内异常收口策略见 _run_error_message，与全局
    异常处理器保持同一口径。
    """

    agent_factory: AgentFactory

    conversations: ConversationService

    turn_finalizer: TurnFinalizer

    memory: MemoryService

    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def chat(self, thread_id: UUID, run_id: str, query: str):
        now = datetime.fromtimestamp(time())

        # 双翻译共存于同一次 interleave 遍历：
        # - ag_ui_translator：逐 delta 产出 ag-ui SSE 事件（流式）
        # - storage_translator：累积成完整 AgenticMessage（落库），不流式；
        #   其 turn_id 来自 open_turn 创建的轮次行，准备段成功后才实例化
        ag_ui_translator = AgUiTranslator(thread_id=thread_id, run_id=run_id)

        # RunStarted 不依赖任何外部状态，先发帧再干活：SSE 响应头一旦发出
        # （200 + text/event-stream）全局异常处理器就不再生效，后续任何异常
        # 只能以 RunErrorEvent 收尾——保证客户端总能看到完整的
        # Started → (Finished | Error) 生命周期，而不是 200 之后裸断流
        yield ag_ui_translator.start()

        turn: AgenticConversationTurn | None = None
        storage_translator = None
        try:
            # 初始化会话（get-or-create）、会话轮次（create），并存储用户消息
            conversation, turn = self.conversations.open_turn(thread_id=thread_id, run_id=run_id, query=query)
            storage_translator = StorageTranslator(thread_id=thread_id, turn_id=turn.turn_id)

            # 智能体实例由智能体层工厂创建（按会话绑定的 agentic_id，未注册回退默认）
            agent = self.agent_factory.create(conversation.agentic_id)

            # 多轮历史以库为准（服务端权威，不信任请求携带的历史）：
            # 最近轮次的 USER/ASSISTANT 文本 + 当前提问
            history = self.conversations.replay_history(thread_id=thread_id, exclude_turn_id=turn.turn_id)

            # 快速回忆：会话前按问题自动注入（纯 SQL 直读，失败不阻断对话主链路）
            memory_block = ""
            try:
                memory_block = self.memory.build_fast_context(query=query, thread_id=thread_id)
            except Exception:
                self.logger.warning("build fast memory context failed", exc_info=True)
            user_query = f"{memory_block}\n\n---\n\n用户提问：{query}" if memory_block else query
            run = agent.stream(AgentRunContext(
                messages=[*history, HumanMessage(content=user_query)],
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
                self._fail_turn_safely(turn)
            yield ag_ui_translator.error(self._run_error_message(e))
            return

        # messages item 是整条 assistant 消息的 ChatModelStream（非片段），
        # 每条消息生成一次 id 注入两个 translator，保证流式事件与落库行共用同一 message_id。
        # 深度回忆三件套的会话身份走 langgraph config 注入（BaseAgent._config 的
        # configurable.thread_id），不在此绑定——本生成器由 Starlette 逐次在不同
        # context 副本里恢复，ContextVar 的 set/reset 跨不过去（工具读不到值，
        # token reset 还会在流收尾抛 ValueError 打断 SSE 连接）。
        try:
            for name, item in run.interleave("messages", "tools"):
                message_id = uuid4()
                yield from ag_ui_translator.translate(name, item, message_id)
                storage_translator.translate(name, item, message_id)

            yield ag_ui_translator.finish()
            self.conversations.complete_turn(turn)
        except Exception as e:
            # 同准备段：先收口轮次状态，再发错误帧；对外消息按异常分级 +
            # 环境脱敏（不再透传裸 str(e)，策略见 _run_error_message）
            self._log_stream_error("chat 流式段异常", e)
            self._fail_turn_safely(turn)
            yield ag_ui_translator.error(self._run_error_message(e))

        # 流结束后批量落库 assistant 消息
        self.conversations.store_assistant_messages(storage_translator.messages)

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

    def _fail_turn_safely(self, turn: AgenticConversationTurn) -> None:
        """失败收口写库的兜底：写不进去（如存储已不可用）只记日志不再上抛，
        免得收尾的二次异常顶掉本要发给客户端的 RunErrorEvent。"""
        try:
            self.conversations.fail_turn(turn)
        except Exception:
            self.logger.exception("轮次失败状态落库失败 turn_id=%s", turn.turn_id)
