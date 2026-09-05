from typing import Any, Iterator
from uuid import UUID

from ag_ui.core import (
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
    """

    def __init__(self, thread_id: UUID, run_id: str):
        self.thread_id = str(thread_id)
        self.run_id = run_id
        self._enc = EventEncoder()
        # 最近一条 assistant 内容块 id，用作 ToolCallStart.parent_message_id 与
        # ToolCallResult.message_id 的归属关联。
        self._last_assistant_msg_id: str = ""

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
    def translate(self, name: str, item: Any, message_id: UUID) -> Iterator[str]:
        """消费 interleave 产出的 (name, item)。

        ``message_id`` 由消费方（service 遍历循环）按「每条 assistant 消息一次」
        生成并注入，与 StorageTranslator 落库行共用同一 id，保证流式事件与持久化
        消息可对账；tools 事件会收到最近一条 assistant 消息的 id（本方法忽略）。
        """
        if name == "messages":
            yield from self._translate_messages(item, message_id)
        elif name == "tools":
            yield from self._translate_tools(item)

    def _translate_messages(self, stream: Any, message_id: UUID) -> Iterator[str]:
        """遍历 ChatModelStream 的 reasoning / text 公开投影。

        投影在 buffer 空且未 done 时会驱动共享 graph pump，因此这里就是实时流式。
        reasoning 先、text 后；任一投影若空（该消息无对应内容）则 Start/End 也不发。
        """
        msg_id = str(message_id)
        self._last_assistant_msg_id = msg_id

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
                    parent_message_id=self._last_assistant_msg_id,
                    tool_call_id=payload.get("tool_call_id", ""),
                    tool_call_name=payload.get("tool_name", ""),
                )
            )
        elif event_type == "tool-result":
            # ToolCallResultEvent 需要 messageId，复用最近一条 assistant 消息 id
            # 作为结果归属；若无则用 tool_call_id 兜底（前端按 toolCallId 关联）。
            message_id = self._last_assistant_msg_id or payload.get("tool_call_id", "")
            yield self._enc.encode(
                ToolCallResultEvent(
                    message_id=message_id,
                    tool_call_id=payload.get("tool_call_id", ""),
                    content=payload.get("content", ""),
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
