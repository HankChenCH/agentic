"""客服智能体的工具流投影：检索类工具结果对前端脱溯源（审计/展示双内容）。

继承 ``ToolsTransformer`` 只改写一处——knowledge 检索类工具的
``tool-result`` 投影内容。客服场景检索的是企业内部文档，出处（文档名/
页码/标题路径/位置框/相关度）不得透给最终用户；LLM 侧不受影响（进模型
的 ToolMessage 由图状态决定，transformer 只投影流帧）。

改写走 tools 通道的**双内容契约**：命中溯源工具时 push
``content=<真实结果>`` + ``display=<前端摘要>``——``content`` 语义不变
（LLM 所见的真实结果，供 StorageTranslator 落库审计），``display`` 是
``KnowledgeSearchResult`` JSON 摘成的「编号 + 内容」纯文本（AgUiTranslator
优先取它下发 SSE，前端永不见出处）。真实结果只入库、不经任何前端接口外发
（历史出口由 ConversationService.list_history_messages 换成展示版）。
工具 description 保持中性（无引用诱导），出处的口子由本投影关死。

契约依赖 agents ──► components 合法边：形状判定直接用
``KnowledgeSearchResult`` 契约模型（模型即契约，不做形状鸭子判别）。
"""

from typing import Any

from pydantic import ValidationError

from app.agents.tools_transformer import ToolsTransformer
from app.components.knowledge.manifest import KnowledgeSearchResult

# 命中结果会携带溯源元数据的 knowledge 工具（knowledge_list 返回库清单、
# memory 三件套无出处，均原样透传）
_PROVENANCE_TOOLS = frozenset({"knowledge_search", "knowledge_context"})


def _frontend_digest(content: Any) -> Any | None:
    """溯源 JSON → 前端摘要；非溯源 JSON（未命中说明/引导话术等）返回 None 原样透传。"""
    if not isinstance(content, str):
        return None
    try:
        result = KnowledgeSearchResult.model_validate_json(content)
    except ValidationError:
        return None
    if not result.sources:
        return None
    return "\n\n".join(f"{source.index}. {source.content}" for source in result.sources)


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
                digest = _frontend_digest(content)
                if digest is not None:
                    # 双内容契约：content 恒为真实结果（落库审计），display 为
                    # 前端展示版（流式 SSE 与历史出口都只见它）。短路 return
                    # 不走 super()，避免与基线投影双发。
                    self.channel.push({
                        "event": "tool-result",
                        "tool_call_id": payload["tool_call_id"],
                        "content": content,
                        "display": digest,
                    })
                    self.channel.push({
                        "event": "tool-finished",
                        "tool_call_id": payload["tool_call_id"],
                    })
                    return
        super()._process_tools_event(event)


def _output_content(output: Any) -> Any:
    return output.content if hasattr(output, "content") else output
