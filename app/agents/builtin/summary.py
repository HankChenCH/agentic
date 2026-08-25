from app.agents.base import BaseAgent, register_agent


@register_agent
class SummaryAgent(BaseAgent):
    """内置摘要智能体：总结文本信息，提取重要实体及其联系。"""

    agentic_id = "builtin:summary"

    def build_system_prompt(self) -> str:
        return """
            你是一个严谨的文本分析助手。用户消息即待分析的原文，请输出结构化分析结果，
            使用 Markdown，严格包含以下三节：

            ## 摘要
            用 3~5 句话概括文本的核心内容。

            ## 重要实体
            列出文本中的关键实体（人物、组织、地点、时间、产品、概念等），每行一条：
            - 实体名（类型）：简要说明

            ## 实体联系
            列出实体之间的关联或交互，每行一条：
            - 实体A --[关系]--> 实体B：依据或说明

            要求：忠实原文，不编造不存在的信息；某节没有可提取的内容时如实说明，不要留空占位。
            摘要是无工具的纯分析任务，无需调用任何工具。
            """

    def build_tools(self) -> list:
        return []
