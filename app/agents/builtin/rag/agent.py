"""builtin:rag 智能体：自建 LangGraph 状态图的 RAG 专用智能体。

与 ``create_agent`` 预置 ReAct 循环的分工差异：检索不是 LLM 自主的工具调用，
而是图的固定节点——understand（分路 + 多轮凝练）→ retrieve（用户域混合
检索）→ grade（相关性过滤）→ generate（带 [n] 引用生成），未命中时
rewrite 重试一次，仍空如实兜底。中间 LLM 调用不进 UI 不落库，检索步骤以
tools 通道的伪工具事件呈现（前端零改动渲染"正在检索"与溯源卡片）。

人设（generate 系统提示词）即 ``build_system_prompt``；记忆快速上下文仍由
编排层注入本轮用户消息（对图透明）；深度回忆/知识检索等 LLM 工具不挂载
——本智能体的能力面就是图本身。
"""

from typing import List

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agents.base import BaseAgent, register_agent
from app.agents.builtin.rag.graph import build_rag_graph
from app.agents.builtin.rag.nodes import message_text
from app.agents.builtin.rag.prompts import GENERATE_SYSTEM_PROMPT
from app.agents.builtin.rag.stream import RagRunStream, ToolEventsTransformer
from app.agents.context import AgentRunContext
from app.agents.toolbox import AgentToolbox

RECURSION_LIMIT = 12  # 硬上限（理论最多 understand+2x(retrieve+grade)+rewrite+generate ≈ 8 步）


def _is_chat_text(message: BaseMessage) -> bool:
    """历史过滤谓词：RAG 图的凝练上下文只需 user/assistant 文本。"""
    if isinstance(message, ToolMessage):
        return False
    if isinstance(message, AIMessage) and message.tool_calls:
        return False
    return bool(message_text(message).strip())


@register_agent
class RagAgent(BaseAgent):
    """内置 RAG 智能体（自建 StateGraph：理解 → 检索 → 过滤 → 引用生成）。"""

    agentic_id = "builtin:rag"

    def __init__(self, model, toolbox: AgentToolbox):
        # 检索门面在 build_graph 之前取好（构造顺序与 BaseAgent 同约束）
        self.retrieval = toolbox.knowledge.retrieval
        super().__init__(model, toolbox)

    def build_system_prompt(self) -> str:
        return GENERATE_SYSTEM_PROMPT

    def build_graph(self) -> CompiledStateGraph:
        return build_rag_graph(self.model, self.retrieval)

    def stream(self, ctx: AgentRunContext):
        """启动流式运行：tools 通道接引自定义 transformer，messages 通道滤除内部调用。"""
        run = self._graph.stream_events(
            version="v3",
            input=self._input(ctx),
            config=self._config(ctx),
            transformers=[ToolEventsTransformer],
        )
        return RagRunStream(run)

    def _input(self, ctx: AgentRunContext) -> dict:
        """图输入：滤除工具痕迹后的历史 + 整体初始化的状态通道。

        多轮历史以库回放为准（图无 checkpointer）；检索痕迹（TOOL_CALL/
        TOOL_RESULT 回放对）不参与凝练上下文。运行期动态信息（当前时间）
        注入末条用户消息，与 BaseAgent 同口径。
        """
        messages: List[BaseMessage] = [m for m in ctx.messages if _is_chat_text(m)]
        if messages:
            messages[-1] = HumanMessage(f"[当前时间：{ctx.now}]\n\n{messages[-1].content}")
        return {
            "messages": messages,
            "route": "",
            "standalone_question": "",
            "documents": [],
            "relevant": [],
            "rewrite_count": 0,
            "generation": "",
        }

    def _config(self, ctx: AgentRunContext) -> RunnableConfig:
        config = super()._config(ctx)
        config["recursion_limit"] = RECURSION_LIMIT
        return config
