"""工具入口的协作式取消检查中间件（显式取消通道的工具侧半边）。

编排层把取消检查闭包绑进 ``AgentRunContext.cancel_check``，经 langgraph
``context=`` 参数传入（``BaseAgent.stream/invoke``）；本中间件在每次工具
执行前经 ``request.runtime.context`` 取回并检查：命中即抛
``RunCanceledError`` 跳过工具体（省掉一次无谓的检索/LLM 外呼）。异常沿
langgraph 工具错误的默认处理原样上抛、冲出图运行（默认 handler 只消化
参数校验类 ToolInvocationError），由编排层流式循环的异常兜底收口；常规
时序下帧级取消检查先于工具入口感知取消，本守卫兜住两者之间的窗口。
``cancel_check`` 为 None（未启用取消）或 runtime.context 非
``AgentRunContext``（直调/测试桩）时放行——与动态 prompt 中间件对
runtime 的防御性降级同款口径。

中间件形态（而非逐工具包装）的收益：一处 ``wrap_tool_call`` 覆盖全部工具
（含未来 middleware 自带工具），工具签名保持干净——langchain 只对声明了
``config`` 形参的工具注入 config，无需守卫代为合成签名。挂载点在
``BaseAgent.build_graph`` 强制前置（不进 ``build_middleware`` 装配面）：
取消是编排层契约，不因子类覆写 ``build_middleware`` 而静默丢失，且位于
wrap_tool_call 链最外层——取消先于任何其他工具中间件（如重试）生效，
不会被重试放大。

只覆写同步钩子：编排层走同步 ``stream_events(v3)``，异步钩子在本仓库
不可用（惯例同 ``DynamicSystemPromptMiddleware``）。
"""

from typing import Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from app.agents.context import AgentRunContext


class RunCanceledError(Exception):
    """工具入口守卫检测到取消标志：跳过工具体，异常原样冲出图运行，由
    编排层流式循环的异常兜底收口——不继承 BaseException，保持走 langgraph
    的常规错误处理路径。"""


class CancelGuardMiddleware(AgentMiddleware):
    """每次工具执行前检查取消标志（wrap_tool_call，只覆写同步钩子）。"""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage],
    ) -> ToolMessage:
        ctx = getattr(request.runtime, "context", None)
        check = ctx.cancel_check if isinstance(ctx, AgentRunContext) else None
        if check is not None and check():
            raise RunCanceledError("run canceled by client")
        return handler(request)
