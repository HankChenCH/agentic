"""builtin:demo 智能体：与 ``create_agent`` 预置 ReAct 循环直接组合。

自身无自建图与额外装配：人设取 prompts.SYSTEM_PROMPT，工具经 toolbox 装配
记忆三件套 + 演示工具（知识检索归 builtin:rag），快速记忆上下文走 BaseAgent
默认的 system prompt 组装。
"""

from app.agents.base import BaseAgent, register_agent
from app.agents.builtin.demo.prompts import SYSTEM_PROMPT


@register_agent
class DemoAgent(BaseAgent):
    """内置演示智能体：通用助手 + 天气查询 + 记忆召回。"""

    agentic_id = "builtin:demo"
    display_name = "演示助手"
    description = "通用对话助手，携带天气查询与长期记忆工具"

    def build_system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def build_tools(self) -> list:
        # 记忆三件套 + 演示工具 get_weather 经 toolbox 按组件名装配（知识检索归 builtin:rag）
        return self.toolbox.tools(self.agentic_id, only={"memory", "demo"})
