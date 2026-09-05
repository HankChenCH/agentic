"""builtin:support 智能体：与 builtin:rag 同知识/记忆工具面、客服人设、透传脱溯源。

在知识 + 记忆组件之外另挂转人工面：human_agent 组件查库取坐席列表，
a2ui 组件提供 a2ui_compose 生成通道——转接卡片由 LLM 以组件树参数自主
构造（Generative UI），工具只做校验/翻译/下发。差异有两处：① 人设——
面向最终用户的客服口径，检索出处不外露（引用纪律属智能体人设层，见
prompts.py；工具 description 保持中性）；② 流投影——SupportToolsTransformer
把检索结果的透传副本摘成无出处纯文本（LLM 侧照常拿完整 JSON，作答质量
不受影响；human_agent/a2ui 工具不在脱溯源名单内，artifact 经 ui 透传）。
前端零组件改动：同名工具的溯源卡片因拿不到溯源 JSON 自然降级，A2UI 卡片
走既有 CUSTOM data part 渲染。
"""

from app.agents.base import BaseAgent, register_agent
from app.agents.builtin.support.prompts import SYSTEM_PROMPT
from app.agents.builtin.support.transformer import SupportToolsTransformer


@register_agent
class SupportAgent(BaseAgent):
    """内置客服智能体：内部知识检索作答但不暴露出处，携带长期记忆。"""

    agentic_id = "builtin:support"
    display_name = "智能客服"
    description = "面向最终用户的客服助手：检索内部知识库解答疑问，不暴露资料出处，携带长期记忆"

    def build_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_tools(self) -> list:
        # 知识检索两件（清单走 {knowledge_bases} 片段）+ 记忆三件套 + a2ui
        # 生成通道（转接卡片由 LLM 自主构造）；human_agent_list 已由
        # {human_agents} 片段取代
        return [
            *self.toolbox.tools(self.agentic_id, only={"knowledge"}, tool_names={"knowledge_search", "knowledge_context"}),
            *self.toolbox.tools(self.agentic_id, only={"memory"}),
            *self.toolbox.tools(self.agentic_id, only={"a2ui"}),
        ]

    def build_transformers(self) -> list:
        return [SupportToolsTransformer]
