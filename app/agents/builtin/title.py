from app.agents.base import BaseAgent, register_agent


@register_agent
class TitleAgent(BaseAgent):
    """内置标题智能体：把对话内容浓缩为一句话会话标题（内部任务用）。"""

    agentic_id = "builtin:title"

    def build_system_prompt(self) -> str:
        return """
            你是一个会话标题生成器。根据用户提供的对话内容，生成一个简洁准确的中文标题。

            要求：
            - 不超过 15 个字，概括对话的核心主题；
            - 直接输出标题文本，不要引号、序号、标点前后缀或任何解释；
            - 优先从用户的提问提炼，不要编造对话中不存在的内容。
            """

    def build_tools(self) -> list:
        return []
