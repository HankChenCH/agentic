from typing import Any, Iterator
from uuid import UUID, uuid4

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

from app.packages.a2ui import A2UI_CUSTOM_EVENT_NAME


class AgUiTranslator:
    """把 langgraph 的 messages + tools 投影翻译成 ag-ui 事件（已 SSE encode）。

    唯一产生 ag-ui 事件的地方：
    - 生命周期（RunStarted/RunFinished/RunError）由 service 用 start()/finish()/
      error() 显式触发；
    - 内容（Text/Reasoning）消费原生 messages 投影里的 ``ChatModelStream`` 公开
      投影 ``stream.reasoning`` / ``stream.text``；
    - 工具（ToolCall*）消费 ``ToolsTransformer`` 投影出的稳定中间契约。

    输出已用 EventEncoder 编码成 ``data: {...}\\n\\n`` 帧，endpoint 可直接透传。

    投影式消费的取舍：同一条 assistant 消息内，所有 reasoning delta 先出、
    所有 text delta 后出（投影各自驱动 pump 至 message-finish）。对 DeepSeek
    这类「先 reasoning 后 text」的推理模型恰好正确。

    消息 id 契约：整个 run 的全部内容事件（Text/Reasoning/ToolCall 归属）共用
    __init__ 时生成的一个 run 级 messageId。前端 react-ag-ui ≥0.0.58 把
    TEXT_MESSAGE_* 里 messageId 的变化当作消息边界（新 id = 新开一条 assistant
    消息），ReAct 多步 run（工具前引导语 + 工具后回答是两次独立 LLM 调用）若
    逐条换 id 会被前端裂成多条消息——与落库侧「每次 LLM 调用一行」的粒度解耦：
    落库行 id 由 StorageTranslator 自行接收（service 按条生成），流式侧恒用本 id。
    """

    def __init__(self, thread_id: UUID, run_id: str):
        self.thread_id = str(thread_id)
        self.run_id = run_id
        self._enc = EventEncoder()
        # run 级 assistant 消息 id：一次 run 在前端只呈现一条 assistant 消息，
        # 亦用作 ToolCallStart.parent_message_id 与 ToolCallResult.message_id 的归属关联。
        self._assistant_msg_id: str = uuid4().hex

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
    def translate(self, name: str, item: Any) -> Iterator[str]:
        """消费 interleave 产出的 (name, item)。

        流式事件一律使用 run 级 ``_assistant_msg_id``（见类注释的消息 id 契约）；
        落库行的逐条 id 由 StorageTranslator 在 service 循环里独立接收，两侧
        粒度刻意不同（流式=一 run 一条消息，落库=一次 LLM 调用一行）。
        """
        if name == "messages":
            yield from self._translate_messages(item)
        elif name == "tools":
            yield from self._translate_tools(item)

    def _translate_messages(self, stream: Any) -> Iterator[str]:
        """遍历 ChatModelStream 的 reasoning / text 公开投影。

        投影在 buffer 空且未 done 时会驱动共享 graph pump，因此这里就是实时流式。
        reasoning 先、text 后；任一投影若空（该消息无对应内容）则 Start/End 也不发。
        多步 ReAct run 会多次进入本方法（每次 LLM 调用一次），id 恒定使前端把
        前后文本累积进同一条消息。
        """
        msg_id = self._assistant_msg_id

        has_reasoning = False
        for delta in stream.reasoning:
            if not has_reasoning:
                has_reasoning = True
                yield self._enc.encode(ReasoningMessageStartEvent(message_id=msg_id, role="reasoning"))
            yield self._enc.encode(
                ReasoningMessageContentEvent(message_id=msg_id, delta=delta)
            )
        if has_reasoning:
            yield self._enc.encode(ReasoningMessageEndEvent(message_id=msg_id))

        has_text = False
        for delta in stream.text:
            if not has_text:
                has_text = True
                yield self._enc.encode(TextMessageStartEvent(message_id=msg_id))
            yield self._enc.encode(
                TextMessageContentEvent(message_id=msg_id, delta=delta)
            )
        if has_text:
            yield self._enc.encode(TextMessageEndEvent(message_id=msg_id))

    def _translate_tools(self, payload: dict[str, Any]) -> Iterator[str]:
        """tools 投影产出的是 ToolsTransformer 归一化后的中间契约。"""
        event_type = payload.get("event")
        if event_type == "tool-started":
            yield self._enc.encode(
                ToolCallStartEvent(
                    parent_message_id=self._assistant_msg_id,
                    tool_call_id=payload.get("tool_call_id", ""),
                    tool_call_name=payload.get("tool_name", ""),
                )
            )
        elif event_type == "tool-result":
            # 双内容契约：``content`` 是真实结果（LLM 所见，供落库审计），
            # ``display`` 是可选的前端展示版（如客服检索脱溯源摘要）——带
            # display 时流式只发展示版，出处不透前端；无 display 回退 content。
            tool_call_id = payload.get("tool_call_id", "")
            yield self._enc.encode(
                ToolCallResultEvent(
                    message_id=self._assistant_msg_id,
                    tool_call_id=tool_call_id,
                    content=payload.get("display", payload.get("content", "")),
                )
            )
            # A2UI 通道：工具结果携带 UI 载荷（artifact 的 {"a2ui": [消息数组]}）
            # 时追加一条 CUSTOM 事件（value = 消息数组）。位置契约：紧跟本工具的
            # TOOL_CALL_RESULT 之后、ToolCallEnd 之前——前端 react-ag-ui 把 CUSTOM
            # 聚合为 data part 挂进当前 assistant 消息。
            ui_payload = payload.get("ui")
            if isinstance(ui_payload, dict) and ui_payload.get("a2ui"):
                yield self._enc.encode(
                    CustomEvent(
                        name=A2UI_CUSTOM_EVENT_NAME,
                        value=ui_payload["a2ui"],
                    )
                )
        elif event_type == "tool-finished":
            yield self._enc.encode(
                ToolCallEndEvent(tool_call_id=payload.get("tool_call_id", ""))
            )
        elif event_type == "tool-error":
            # 工具执行出错：仅收尾当前 tool-call，让 agent 在后续轮自行消化，
            # 不升级为 RunError（那是流级错误）。
            yield self._enc.encode(
                ToolCallEndEvent(tool_call_id=payload.get("tool_call_id", ""))
            )
