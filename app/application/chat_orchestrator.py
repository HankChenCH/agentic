from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from time import monotonic, time
import threading

from uuid import UUID, uuid4
from wireup import injectable

from langchain.messages import HumanMessage

from app.core.config import get_environment
from app.core.exceptions import BusinessError
from app.core.logging import LoggerFactory
from app.agents import AgentFactory, AgentRunContext
from app.components.memory import MemoryRecallService

from .translator import AgUiTranslator, StorageTranslator
from .turn_finalizer import TurnFinalizer
from app.services.domain.conversation.conversation_service import ConversationService

from app.models.domain.agentic import AgenticConversationTurn, AgenticTurnStatus

# 显式取消标志的检查间隔：首项必查，之后节流——每帧一查是每秒几十次无谓 RTT
CANCEL_CHECK_INTERVAL_SECONDS = 0.5


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

    memory: MemoryRecallService

    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)
        # 在途对话流登记（thread_id → 并发流数）：优雅关闭的信号处理器据此为
        # 所有在途流点亮取消标志（见 begin_shutdown）
        self._active_lock = threading.Lock()
        self._active_runs: Counter[UUID] = Counter()

    def chat(self, user_id: UUID, thread_id: UUID, run_id: str, query: str):
        """公共入口：登记在途流（供优雅关闭取消）后委托 _stream_chat 行程。
        finally 兜底注销——含 GeneratorExit/异常路径。"""
        with self._active_lock:
            self._active_runs[thread_id] += 1
        try:
            yield from self._stream_chat(user_id, thread_id, run_id, query)
        finally:
            with self._active_lock:
                self._active_runs[thread_id] -= 1
                if self._active_runs[thread_id] <= 0:
                    del self._active_runs[thread_id]

    def begin_shutdown(self) -> int:
        """进程收到 SIGTERM/SIGINT 时由 HTTP 入口的信号处理器调用：为所有在途
        对话流点亮 thread 作用域取消标志（复用显式取消通道），流式循环在帧级
        检查点（≤0.5s）静默收口——轮次置 CANCELED、半截消息不落库，与客户端
        断连/Stop 按钮同口径；随后 uvicorn 正常排空连接退出。信号处理器内
        只做标志写入（快路径），不触碰运行中的生成器。"""
        with self._active_lock:
            thread_ids = list(self._active_runs)
        for thread_id in thread_ids:
            try:
                self.conversations.cancel_run_flag(thread_id)
            except Exception:
                self.logger.exception("优雅关闭取消在途流失败 thread_id=%s", thread_id)
        if thread_ids:
            self.logger.info("优雅关闭：已为 %d 个在途对话流发出取消标志", len(thread_ids))
        return len(thread_ids)

    def _stream_chat(self, user_id: UUID, thread_id: UUID, run_id: str, query: str):
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
            # 初始化会话（get-or-create + 归属校验）、会话轮次（create），并存储用户消息
            conversation, turn = self.conversations.open_turn(user_id=user_id, thread_id=thread_id, run_id=run_id, query=query)
            storage_translator = StorageTranslator(thread_id=thread_id, turn_id=turn.turn_id)

            # 智能体实例由智能体层工厂创建（按会话绑定的 agentic_id，未注册回退默认）
            agent = self.agent_factory.create(conversation.agentic_id)

            # 多轮历史以库为准（服务端权威，不信任请求携带的历史）：
            # 最近轮次的 USER/ASSISTANT 文本 + 当前提问
            history = self.conversations.replay_history(thread_id=thread_id, exclude_turn_id=turn.turn_id)

            # 快速回忆：会话前按问题自动注入（纯 SQL 直读，失败不阻断对话主链路）
            memory_block = ""
            try:
                memory_block = self.memory.build_fast_context(query=query, user_id=user_id, thread_id=thread_id)
            except Exception:
                self.logger.warning("build fast memory context failed", exc_info=True)
            user_query = f"{memory_block}\n\n---\n\n用户提问：{query}" if memory_block else query
            run = agent.stream(AgentRunContext(
                messages=[*history, HumanMessage(content=user_query)],
                thread_id=str(thread_id),
                run_id=run_id,
                user_id=str(user_id),
                now=now,
                # 工具入口的协作式取消检查（经 configurable 注入工具，闭包即数据，
                # agents 层不依赖本层）
                cancel_check=lambda: self.conversations.is_run_canceled(thread_id),
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
        last_cancel_check = 0.0  # 置 0：首帧必查，兜住"流刚开始就收到取消"
        try:
            for name, item in run.interleave("messages", "tools"):
                message_id = uuid4()
                # 取消检查必须在帧级：interleave 的 item 是整条 assistant 消息，
                # 单消息轮次整个流式阶段只有一次 item 边界——item 级检查会把
                # 取标志的感知推迟到消息全部生成完。帧级 + 0.5s 节流（每帧一查
                # 是每秒几十次无谓 RTT）
                canceled = False
                for frame in ag_ui_translator.translate(name, item, message_id):
                    now_mono = monotonic()
                    if now_mono - last_cancel_check >= CANCEL_CHECK_INTERVAL_SECONDS:
                        last_cancel_check = now_mono
                        if self.conversations.is_run_canceled(thread_id):
                            canceled = True
                            break
                    yield frame
                if canceled:
                    # 显式取消（REST 接口）：静默断流——ag-ui 无服务端 CANCELLED
                    # 事件，客户端以本地 abort 态为准，不发 RUN_ERROR（口径同
                    # 断链路径）。半截消息不落库、收尾加工不执行，轮次收口
                    # CANCELED；translate 生成器被遗弃后由 GC 关闭（无副作用）。
                    self.logger.info("客户端取消，轮次已置 CANCELED thread_id=%s", thread_id)
                    self._cancel_turn_safely(turn)
                    return
                storage_translator.translate(name, item, message_id)

            yield ag_ui_translator.finish()
            self.conversations.complete_turn(turn)
        except GeneratorExit:
            # 客户端断连（刷新/关页/停止按钮 abort）时 Starlette 会向本生成器的
            # 挂起 yield 注入 GeneratorExit。它是 BaseException，下面的
            # except Exception 接不住，不收口轮次会永远停在 RUNNING。此处严禁
            # yield（会触发 RuntimeError: generator ignored GeneratorExit）——
            # 只同步收尾状态后原样上抛，半截 assistant 消息不落库、收尾加工不执行
            # （与异常路径同口径：客户端未收到的事件序列不产生"已完成"数据）。
            self._cancel_turn_safely(turn)
            raise
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

    def cancel_run(self, user_id: UUID, thread_id: UUID) -> None:
        """显式取消通道（REST cancel 端点）：置 thread 作用域标志，由流式循环在
        节流边界与工具入口感知收口——不直接触碰运行中的生成器（同步生成器无法
        从外部打断在执行的 next()）。Redis 不可用时标志写入失败异常上抛，端点
        经全局处理器如实反馈。非本人会话按不存在处理（404）——取消标志是
        thread 作用域的全局键，必须先过归属校验再写。"""
        self.conversations.describe_conversation(user_id=user_id, thread_id=thread_id)
        self.conversations.cancel_run_flag(thread_id)
        self.logger.info("收到取消请求 thread_id=%s", thread_id)

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

    def _cancel_turn_safely(self, turn: AgenticConversationTurn | None) -> None:
        """断连收口写库的兜底（风格同 _fail_turn_safely）：只在轮次仍是 RUNNING
        时置 CANCELED——若已被前序 except 置 FAILED，不改写；轮次行未建则无事可做。"""
        if turn is None or turn.status is not AgenticTurnStatus.RUNNING:
            return
        try:
            self.conversations.cancel_turn(turn)
            self.logger.info("客户端断连，轮次已置 CANCELED turn_id=%s", turn.turn_id)
        except Exception:
            self.logger.exception("轮次取消状态落库失败 turn_id=%s", turn.turn_id)
