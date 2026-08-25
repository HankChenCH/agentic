from dataclasses import dataclass
from datetime import datetime

from langchain_core.messages import BaseMessage


@dataclass
class AgentRunContext:
    """一次智能体运行所需的上下文。

    ``messages`` 携带多轮对话（user/assistant 文本，旧→新），末条必须为当前轮
    用户消息——BaseAgent 会把运行期动态信息（当前时间）注入末条后整体回放，
    使无 checkpointer 的图也能跨运行携带多轮上下文。
    """

    messages: list[BaseMessage]
    thread_id: str
    run_id: str
    now: datetime
