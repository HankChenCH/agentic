from app.agents.base import BaseAgent, register_agent
from app.components.knowledge import build_knowledge_tools
from app.components.memory import build_memory_tools


def get_weather(city: str, date: str):
    """
    查询对应城市的天气情况
    city: 城市名称, eg: 中山
    date: 日期，eg: 2026-01-01
    """
    return f"{city} {date} 天气晴朗，气温33度，湿度60%"


@register_agent
class DemoAgent(BaseAgent):
    """内置演示智能体：通用助手 + 天气查询 + 记忆召回 + 知识库检索工具。"""

    agentic_id = "builtin:demo"

    def build_system_prompt(self) -> str:
        return """
            你是个善于帮助用户解决问题的智能助手，请善用工具为用户解决问题。
            跨会话记忆分两级：若本轮开头已附【快速记忆上下文】，优先直接利用；
            不足以还原事情来龙去脉时再调用记忆工具深挖——timeline 按线索查
            过去的事件情节、expand 展开某个人或物的关联事实与经历、state_at
            回放某日期当时的状态。工具检索一般一轮即可，确有信息缺口才继续，
            不要反复空查。
            回答事实性/资料性问题前，先用 knowledge_list 查看可用知识库，再用 knowledge_search
            检索相关内容，依据检索结果的 sources 作答，引用处以 [index] 角标注明出处
            （如 [1]，对应 sources 里的编号与文档名/页码）；未检索到时如实说明。
            """

    def build_tools(self) -> list:
        return [
            get_weather,
            *build_memory_tools(self.toolbox.memory),
            *build_knowledge_tools(self.toolbox.knowledge, self.agentic_id),
        ]
