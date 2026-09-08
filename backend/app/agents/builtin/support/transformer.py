"""客服智能体的工具流投影：检索类工具结果对前端只报调用成败（审计/展示双内容）。

继承 ``ToolsTransformer`` 只改写一处——knowledge 检索类工具的
``tool-result`` 投影内容。客服场景检索的是企业内部文档，命中正文与出处
（文档名/页码/标题路径/位置框/相关度）都不得透给最终用户——前端只需要
知道这次调用成功与否，命中与否由助手正文自行表达；LLM 侧不受影响（进
模型的 ToolMessage 由图状态决定，transformer 只投影流帧）。

改写走 tools 通道的**双内容契约**：命中溯源工具时 push
``content=<真实结果>`` + ``display=<状态文案>``——``content`` 语义不变
（LLM 所见的真实结果，供 StorageTranslator 落库审计），``display`` 是
固定的「调用成功」状态串（AgUiTranslator 优先取它下发 SSE，前端不见
任何命中内容）。真实结果只入库、不经任何前端接口外发（历史出口由
ConversationService.list_history_messages 换成展示版）。
工具 description 保持中性（无引用诱导），内容外发的口子由本投影关死。

契约依赖 agents ──► components 合法边：形状判定直接用
``KnowledgeSearchResult`` 契约模型（模型即契约，不做形状鸭子判别）。
"""

from typing import Any

from pydantic import ValidationError

from app.agents.tools_transformer import ToolsTransformer
from app.components.knowledge.manifest import KnowledgeSearchResult

# 命中结果会携带溯源元数据与命中正文的 knowledge 工具（knowledge_list
# 返回库清单、memory 三件套无出处，均原样透传）
_PROVENANCE_TOOLS = frozenset({"knowledge_search", "knowledge_context"})

# 前端展示版：只报调用成败，不携带任何命中内容
_SUCCESS_DISPLAY = "调用成功"


def _frontend_display(content: Any) -> Any | None:
    """溯源 JSON → 前端状态文案；非溯源 JSON（未命中说明/引导话术等）返回 None 原样透传。"""
    if not isinstance(content, str):
        return None
    try:
        result = KnowledgeSearchResult.model_validate_json(content)
    except ValidationError:
        return None
    if not result.sources:
        return None
    return _SUCCESS_DISPLAY


class SupportToolsTransformer(ToolsTransformer):
    """客服面 ``ToolsTransformer``：检索结果拆成真实（审计）+ 摘要（展示）双内容。"""

    def __init__(self, scope: tuple[str, ...] = ()):
        super().__init__(scope)
        # tool_call_id → tool_name：tool-finished 载荷不含工具名，从 started 事件记下
        self._tool_names: dict[str, str] = {}

    def _process_tools_event(self, event) -> None:
        payload = event["params"]["data"]
        event_type = payload.get("event")
        if event_type == "tool-started":
            self._tool_names[payload["tool_call_id"]] = payload.get("tool_name", "")
        elif event_type == "tool-error":
            self._tool_names.pop(payload["tool_call_id"], None)
        elif event_type == "tool-finished":
            if self._tool_names.pop(payload["tool_call_id"], "") in _PROVENANCE_TOOLS:
                content = _output_content(payload.get("output"))
                display = _frontend_display(content)
                if display is not None:
                    # 双内容契约：content 恒为真实结果（落库审计），display 为
                    # 前端展示版（流式 SSE 与历史出口都只见它）。短路 return
                    # 不走 super()，避免与基线投影双发。
                    self.channel.push({
                        "event": "tool-result",
                        "tool_call_id": payload["tool_call_id"],
                        "content": content,
                        "display": display,
                    })
                    self.channel.push({
                        "event": "tool-finished",
                        "tool_call_id": payload["tool_call_id"],
                    })
                    return
        super()._process_tools_event(event)


def _output_content(output: Any) -> Any:
    return output.content if hasattr(output, "content") else output
