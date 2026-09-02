"""builtin:demo 智能体：与 ``create_agent`` 预置 ReAct 循环直接组合。

自身无自建图与额外装配：人设取 prompts.SYSTEM_PROMPT，工具经 toolbox 全量
装配，快速记忆上下文走 BaseAgent 默认的 system prompt 组装。
"""

from app.agents.base import BaseAgent, register_agent
from app.agents.builtin.demo.prompts import SYSTEM_PROMPT


@register_agent
class DemoAgent(BaseAgent):
    """内置演示智能体：通用助手 + 天气查询 + 记忆召回 + 知识库检索工具。"""

    agentic_id = "builtin:demo"
    display_name = "演示助手"
    description = "通用对话助手，携带天气查询、长期记忆与知识库检索工具"

    def build_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_tools(self) -> list:
        # 全部工具（记忆三件套 + 知识双工具 + 演示工具 get_weather）经 toolbox 按注册表装配
        return self.toolbox.tools(self.agentic_id)
