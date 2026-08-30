from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from langchain_core.messages import BaseMessage


@dataclass
class AgentRunContext:
    """一次智能体运行所需的上下文。

    ``messages`` 携带多轮对话（user/assistant 文本，旧→新），末条必须为当前轮
    用户消息——BaseAgent 会把运行期动态信息（当前时间）注入末条后整体回放，
    使无 checkpointer 的图也能跨运行携带多轮上下文。

    ``thread_id``/``user_id`` 经 ``_config`` 的 configurable 透传给需要会话/
    归属身份的工具（如记忆深度回忆三件套，经 ``_thread``/``_user`` 辅助读取）。

    ``cancel_check`` 是显式取消通道的检查闭包（编排层绑定当前 thread），经
    ``_config`` 的 configurable 透传给工具入口守卫——闭包即数据，agents 层
    不因此依赖编排/领域层。None 表示本运行不接取消通道（直调/测试场景）。
    """

    messages: list[BaseMessage]
    thread_id: str
    run_id: str
    user_id: str
    now: datetime
    cancel_check: Callable[[], bool] | None = None
