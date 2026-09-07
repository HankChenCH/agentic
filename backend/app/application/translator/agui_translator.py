from typing import Any, Iterator
from uuid import UUID

from ag_ui.core import (
    CustomEvent,
    ReasoningMessageStartEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageContentEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

from app.application.translator.matrix import a2ui_messages
from app.packages.a2ui import A2UI_CUSTOM_EVENT_NAME


def message_frame_events(message_id: UUID, stream: Any) -> Iterator[Any]:
    """一次 LLM 调用（messages 投影）→ ag-ui 事件序列（纯函数，产出事件对象）。

    遍历 ``ChatModelStream`` 的 reasoning / text 公开投影；投影在 buffer 空且
    未 done 时会驱动共享 graph pump，因此这里就是实时流式。投影式消费的取舍：
    同一条 assistant 消息内，所有 reasoning delta 先出、所有 text delta 后出
    （投影各自驱动 pump 至 message-finish），对 DeepSeek 这类「先 reasoning 后
    text」的推理模型恰好正确。任一投影若空（该消息无对应内容）则对应
    Start/End 也不发。全部事件以注入的 ``message_id`` 为 messageId。
    """
    msg_id = message_id.hex

    has_reasoning = False
    for delta in stream.reasoning:
        if not has_reasoning:
            has_reasoning = True
            yield ReasoningMessageStartEvent(message_id=msg_id, role="reasoning")
        yield ReasoningMessageContentEvent(message_id=msg_id, delta=delta)
    if has_reasoning:
        yield ReasoningMessageEndEvent(message_id=msg_id)

    has_text = False
    for delta in stream.text:
        if not has_text:
            has_text = True
            yield TextMessageStartEvent(message_id=msg_id)
        yield TextMessageContentEvent(message_id=msg_id, delta=delta)
    if has_text:
        yield TextMessageEndEvent(message_id=msg_id)


def tool_frame_events(message_id: UUID, payload: dict[str, Any]) -> Iterator[Any]:
    """一个工具通道条目（ToolsTransformer 归一化契约）→ ag-ui 事件序列（纯函数）。

    双内容契约：``content`` 是真实结果（LLM 所见，供落库审计），``display``
    是可选的前端展示版（如客服检索脱溯源摘要）——带 display 时流式只发展示版，
    出处不透前端；无 display 回退 content。A2UI 通道：工具结果携带 UI 载荷时
    追加 CUSTOM 事件，位置契约：紧跟本工具的 TOOL_CALL_RESULT 之后、
    ToolCallEnd 之前——前端 react-ag-ui 把 CUSTOM 聚合为 data part 挂进当前
    assistant 消息。tool-error 仅收尾当前 tool-call，让 agent 在后续轮自行
    消化，不升级为 RunError（那是流级错误）。
    """
    event_type = payload.get("event")
    if event_type == "tool-started":
        yield ToolCallStartEvent(
            parent_message_id=message_id.hex,
            tool_call_id=payload.get("tool_call_id", ""),
            tool_call_name=payload.get("tool_name", ""),
        )
    elif event_type == "tool-result":
        yield ToolCallResultEvent(
            message_id=message_id.hex,
            tool_call_id=payload.get("tool_call_id", ""),
            content=payload.get("display", payload.get("content", "")),
        )
        a2ui = a2ui_messages(payload)
        if a2ui is not None:
            yield CustomEvent(name=A2UI_CUSTOM_EVENT_NAME, value=a2ui)
    elif event_type == "tool-finished":
        yield ToolCallEndEvent(tool_call_id=payload.get("tool_call_id", ""))
    elif event_type == "tool-error":
        yield ToolCallEndEvent(tool_call_id=payload.get("tool_call_id", ""))


class AgUiTranslator:
    """ag2ui 边的薄驱动器：langgraph 流块逐帧翻译成 ag-ui 事件（已 SSE encode）。

    纯转换内核在模块级 ``message_frame_events`` / ``tool_frame_events``（产出
    事件对象）；本类只持每 run 配置与 EventEncoder，translate() 逐帧分派 +
    编码成 ``data: {...}\\n\\n`` 帧，endpoint 可直接透传。唯一产生 ag-ui 事件的
    地方：生命周期（RunStarted/RunFinished/RunError）由 service 用 start()/
    finish()/error() 显式触发；内容/工具消费 interleave 的 (name, item) 条目。

    消息 id 契约：messageId 由消费方（service 的流式循环）随 translate() 逐条
    注入——messages 条目（= 一次 LLM 调用的 ChatModelStream）的 Text/Reasoning
    事件以它为 messageId，tools 条目事件以它为 ToolCall 归属；StorageTranslator
    在同一循环里接收同批 id 落库，流式与落库同粒度、可对账。前端 react-ag-ui
    ≥0.0.58 把 TEXT_MESSAGE_* 里 messageId 的变化当作消息边界（新 id = 新开一条
    assistant 消息），ReAct 多步 run（工具前引导语 + 工具后回答是两次独立 LLM
    调用）因此在前端裂成多条消息——已知取舍：曾试过流式侧恒用 run 级 id 保持
    「一次 run 一条消息」，但过程回复与最终回复随之粘连、更难分辨答案，已回退为
    按条注入（多区块问题待 react-ag-ui 升级后重评）。
    """

    def __init__(self, thread_id: UUID, run_id: str):
        self.thread_id = str(thread_id)
        self.run_id = run_id
        self._enc = EventEncoder()

    # ------------------------------------------------------------------
    # 生命周期（由 service 显式调用）
    # ------------------------------------------------------------------
    def start(self) -> str:
        return self._enc.encode(
            RunStartedEvent(thread_id=self.thread_id, run_id=self.run_id)
        )

    def finish(self) -> str:
        return self._enc.encode(
            RunFinishedEvent(thread_id=self.thread_id, run_id=self.run_id)
        )

    def error(self, message: str) -> str:
        return self._enc.encode(RunErrorEvent(message=message))

    # ------------------------------------------------------------------
    # 内容 / 工具翻译
    # ------------------------------------------------------------------
    def translate(self, message_id: UUID, name: str, item: Any) -> Iterator[str]:
        """消费 interleave 产出的 (name, item)。

        全部产出事件的归属 id 都用调用方注入的 ``message_id``（见类注释的消息
        id 契约）；StorageTranslator 在同一循环里接收同批 id 落库，两侧同粒度
        可对账。
        """
        if name == "messages":
            events: Iterator[Any] = message_frame_events(message_id, item)
        elif name == "tools":
            events = tool_frame_events(message_id, item)
        else:
            return
        for event in events:
            yield self._enc.encode(event)
