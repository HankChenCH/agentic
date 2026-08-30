"""builtin:rag 的流式装配：tools 通道接引 + 内部 LLM 调用滤除。

- ToolEventsTransformer：把图节点内 ``get_stream_writer()`` 发的 tool-* 形态
  custom 事件接进 "tools" 命名通道，与预置 create_agent 的 ToolsTransformer
  同一中间契约——AgUiTranslator / StorageTranslator 零改动消费（检索步骤
  以 ToolCall* ag-ui 事件呈现、TOOL_CALL/TOOL_RESULT 行落库）。RAG 图没有
  模型发起的工具调用，原生 tools stream mode 恒空，本 transformer 是该图
  tools 通道的唯一来源。
- RagRunStream：包装 GraphRunStream，按节点名滤除内部 LLM 调用
  （understand/grade/rewrite 的 ChatModelStream 不进 UI、不落库）。跳过
  项不消费投影也不卡泵——interleave 自驱拉流，投影仅缓冲（已验证）。
"""

from typing import Any, Iterator, Tuple

from langgraph.stream import ProtocolEvent, StreamChannel, StreamTransformer

# 内部 LLM 调用所属的图节点：其 ChatModelStream 由 RagRunStream 滤除
INTERNAL_LLM_NODES = frozenset({"understand", "grade", "rewrite"})

_TOOL_EVENTS = frozenset({"tool-started", "tool-result", "tool-finished", "tool-error"})


class ToolEventsTransformer(StreamTransformer):
    """把节点内 writer 发的 tool-* 形态 custom 事件转发进 tools 通道。

    只转发本图约定的形态（dict + event 字段），其余 custom 载荷放行忽略；
    通道契约（键名与取值）与 ToolsTransformer 保持逐字一致。
    """

    required_stream_modes = ("custom",)

    def __init__(self, scope: tuple[str, ...] = ()):
        super().__init__(scope)
        self.channel: StreamChannel[dict[str, Any]] = StreamChannel("tools")

    def init(self) -> dict[str, StreamChannel]:
        return {"tools": self.channel}

    def process(self, event: ProtocolEvent) -> bool:
        if event["method"] == "custom":
            data = event["params"]["data"]
            if isinstance(data, dict) and data.get("event") in _TOOL_EVENTS:
                self.channel.push(data)
        return True


class RagRunStream:
    """stream() 运行句柄包装：interleave 时滤除内部节点的 LLM 调用。

    只需满足编排层消费面（``interleave("messages", "tools")``）；识别依据是
    ``ChatModelStream.node``（langgraph 标注的发起节点名），非本图的项一律
    放行，故对子图/未来扩展保持惰性兼容。
    """

    def __init__(self, run: Any, internal_nodes: frozenset[str] = INTERNAL_LLM_NODES):
        self._run = run
        self._internal_nodes = internal_nodes

    def interleave(self, *names: str) -> Iterator[Tuple[str, Any]]:
        for name, item in self._run.interleave(*names):
            if name == "messages" and getattr(item, "node", None) in self._internal_nodes:
                continue
            yield name, item
