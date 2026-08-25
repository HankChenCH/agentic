from typing import Any

from langgraph.stream import ProtocolEvent, StreamChannel, StreamTransformer


class ToolsTransformer(StreamTransformer):
    """投影 tools stream mode 事件，并做形态归一化。

    纯 langgraph 领域：不产生任何 ag-ui 事件、不感知 ag-ui 协议。把 langgraph
    内部的 tools stream mode payload 转成一个稳定的中间契约 ``{event,
    tool_call_id, ...}``，供下游 translator（AgUiTranslator）消费。langgraph
    内部形状若有变化，只需改本类。

    存在理由：内置 ``ChatModelStream.tool_calls`` 只携带 LLM 的 tool-call 请求；
    tool 的执行结果只出现在 tools stream mode（tool-started / tool-finished /
    tool-error），而该 mode 没有任何内置 transformer 声明，故需本 transformer
    接进流并投影出来。
    """

    required_stream_modes = ("tools",)

    def __init__(self, scope: tuple[str, ...] = ()):
        super().__init__(scope)
        # 命名 channel：除供 interleave 按 key 消费外，mux 还会自动转发成
        # ``custom:tools`` 协议事件进主事件流（即使有人直接遍历原始流也能看到）。
        self.channel: StreamChannel[dict[str, Any]] = StreamChannel("tools")

    def init(self) -> dict[str, StreamChannel]:
        return {"tools": self.channel}

    def process(self, event: ProtocolEvent) -> bool:
        if event["method"] == "tools":
            self._process_tools_event(event)
        return True

    def _process_tools_event(self, event: ProtocolEvent) -> None:
        # tools 事件的 params["data"] 是裸 dict（不像 messages 那样是
        # (payload, metadata) 二元组），直接读取即可。
        payload = event["params"]["data"]
        event_type = payload.get("event")

        if event_type == "tool-started":
            self.channel.push({
                "event": "tool-started",
                "tool_call_id": payload["tool_call_id"],
                "tool_name": payload["tool_name"],
            })
        elif event_type == "tool-error":
            self.channel.push({
                "event": "tool-error",
                "tool_call_id": payload["tool_call_id"],
            })
        elif event_type == "tool-finished":
            # 把「带输出」与「收尾」拆成两个事件：translator 据此分别映射
            # ToolCallResult / ToolCallEnd。取 ToolMessage.content 作为干净输出，
            # 不把 name/id/tool_call_id 等无关字段泄漏出去。
            output = payload.get("output")
            content = output.content if hasattr(output, "content") else str(output)
            self.channel.push({
                "event": "tool-result",
                "tool_call_id": payload["tool_call_id"],
                "content": content,
            })
            self.channel.push({
                "event": "tool-finished",
                "tool_call_id": payload["tool_call_id"],
            })
