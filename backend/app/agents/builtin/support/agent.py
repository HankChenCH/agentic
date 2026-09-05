"""builtin:support 智能体：与 builtin:rag 同工具面、客服人设、透传脱溯源。

与 builtin:rag 共用 knowledge + memory 组件工具（不新增工具名，避免同图
工具面漂移），差异有两处：① 人设——面向最终用户的客服口径，检索出处
不外露（引用纪律属智能体人设层，见 prompts.py；工具 description 保持
中性）；② 流投影——SupportToolsTransformer 把检索结果的透传副本摘成
无出处纯文本（LLM 侧照常拿完整 JSON，作答质量不受影响）。前端零改动：
同名工具的溯源卡片因拿不到溯源 JSON 而自然降级。
"""

from app.agents.base import BaseAgent, register_agent
from app.agents.builtin.support.prompts import SYSTEM_PROMPT
from app.agents.builtin.support.transformer import SupportToolsTransformer

# knowledge 组件中会透出溯源元数据的检索三件（knowledge_document_list 是
# 库内浏览入口，客服场景用不上，不装配）
_KNOWLEDGE_TOOLS = frozenset({"knowledge_list", "knowledge_search", "knowledge_context"})


@register_agent
class SupportAgent(BaseAgent):
    """内置客服智能体：内部知识检索作答但不暴露出处，携带长期记忆。"""

    agentic_id = "builtin:support"
    display_name = "智能客服"
    description = "面向最终用户的客服助手：检索内部知识库解答疑问，不暴露资料出处，携带长期记忆"

    def build_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_tools(self) -> list:
        return [
            *self.toolbox.tools(self.agentic_id, only={"knowledge"}, tool_names=_KNOWLEDGE_TOOLS),
            *self.toolbox.tools(self.agentic_id, only={"memory"}),
        ]

    def build_transformers(self) -> list:
        return [SupportToolsTransformer]
