"""builtin:rag 智能体：与 ``create_agent`` 预置 ReAct 循环直接组合。

检索不再走自建图的固定节点，而是 LLM 自主的知识工具调用（先列库再检索，
引导随工具 description 走）：人设取 prompts.SYSTEM_PROMPT，工具经 toolbox
装配知识四件 + 记忆三件套（不含演示工具，与 builtin:demo 划分职责），
快速记忆上下文走 BaseAgent 默认的 system prompt 组装，取消守卫/工具事件
流式/多模态输入等随基类同款链路。
"""

from app.agents.base import BaseAgent, register_agent
from app.agents.builtin.rag.prompts import SYSTEM_PROMPT


@register_agent
class RagAgent(BaseAgent):
    """内置 RAG 智能体：知识库检索 + 长期记忆的问答助手（ReAct 工具循环）。"""

    agentic_id = "builtin:rag"
    display_name = "知识库问答"
    description = "基于知识库检索的问答助手，回答附带溯源引用，携带长期记忆"

    def build_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_tools(self) -> list:
        # 知识检索两件 + 记忆三件套经 toolbox 按组件名装配（不含 get_weather）；
        # 知识库清单走 {knowledge_bases} prompt 片段，无需 knowledge_list 工具往返
        return self.toolbox.tools(
            self.agentic_id, only={"knowledge"}, tool_names={"knowledge_search", "knowledge_context"}
        ) + self.toolbox.tools(self.agentic_id, only={"memory"})
