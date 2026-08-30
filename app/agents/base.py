import functools
import inspect
from abc import ABC, abstractmethod
from typing import Callable, ClassVar

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph.state import CompiledStateGraph

from app.agents.context import AgentRunContext
from app.agents.toolbox import AgentToolbox
from app.agents.tools_transformer import ToolsTransformer


class RunCanceledError(Exception):
    """工具入口守卫检测到取消标志：跳过工具体（langgraph 记 tool-error），
    随后由编排层的边界检查终止本轮——不继承 BaseException，避免绕过
    langgraph 的常规错误处理路径。"""


def _cancel_checked(func: Callable):
    """为工具函数包上入口取消检查（_cancel_guard 的共用包装器）。

    检查闭包由编排层经 ``AgentRunContext.cancel_check`` → ``_config`` 的
    ``configurable.cancel_check`` 传入，包装器在每次工具被调用前读取：命中即
    抛 ``RunCanceledError`` 跳过工具体（省掉一次无谓的检索/LLM 外呼），随后
    编排层流式循环的边界检查终止本轮。

    无论原函数是否声明 ``config``，包装器都透出 ``config: RunnableConfig``
    形参（langchain 的运行时注入以函数签名为准）：已声明的原样透传，未声明
    的剥除后调用——取消检查本身需要 config，但 config 不得进工具的 LLM schema
    （StructuredTool 用显式 args_schema、裸函数靠 RunnableConfig 注解排除）。
    """
    sig_params = list(inspect.signature(func).parameters.values())
    has_config = any(p.name == "config" for p in sig_params)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        config = kwargs.get("config")
        check = (config or {}).get("configurable", {}).get("cancel_check")
        if check is not None and check():
            raise RunCanceledError("run canceled by client")
        if not has_config:
            kwargs.pop("config", None)
        return func(*args, **kwargs)

    if not has_config:
        params = [*sig_params, inspect.Parameter(
            "config", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None, annotation=RunnableConfig,
        )]
        wrapper.__signature__ = inspect.Signature(params)
        annotations = dict(getattr(func, "__annotations__", None) or {})
        annotations["config"] = RunnableConfig
        wrapper.__annotations__ = annotations
    return wrapper


def _cancel_guard(tool: Callable | BaseTool) -> Callable | BaseTool:
    """工具入口的协作式取消检查（显式取消通道的工具侧半边），支持两种工具形态：

    - StructuredTool（组件工具的声明式形态）：守卫下沉到底层 func 重建工具，
      args_schema 原样透传（config 注入形参在签名层保证，不进 schema）；
    - 裸函数（智能体自带工具，如 weather）：同款包装并合成签名，langchain
      据此把 config 判为注入参数。
    """
    if isinstance(tool, BaseTool):
        return StructuredTool.from_function(
            name=tool.name,
            description=tool.description,
            args_schema=tool.args_schema,
            func=_cancel_checked(tool.func),
            infer_schema=False,
        )
    return _cancel_checked(tool)


class BaseAgent(ABC):
    """智能体基类（模板方法）。

    智能体图在实例化时编译一次（``self._graph``），stream/invoke 复用，不重复
    build。因此 ``build_system_prompt`` / ``build_tools`` 是构建期静态的——只依赖
    智能体身份，不依赖单次运行；多轮历史与运行期动态信息（当前时间等）通过
    ``AgentRunContext`` 在每次调用时随输入消息注入，使图可跨运行复用。

    子类供给差异点：
    - ``build_system_prompt``（必须实现）：智能体人设；
    - ``build_tools``（默认无工具）：以组合方式装配能力（如记忆召回、知识库检索），
      能力依赖经构造注入的 ``toolbox`` 获取；
    - ``preferred_provider``（默认走全局配置）：智能体级模型路由，取值为
      ``LLMConfig.providers`` 的 entry key。

    仅当"身份"不同（人设、pipeline、模型路由）才需要新子类；工具增减通过
    ``build_tools`` 传参即可，不必新建智能体类。
    """

    agentic_id: ClassVar[str]  # <agent_type:agent_name>，eg "builtin:demo"
    preferred_provider: ClassVar[str | None] = None

    _graph: CompiledStateGraph

    def __init__(self, model: BaseChatModel, toolbox: AgentToolbox):
        self.model = model
        # toolbox 必须先于 build_tools() 赋值：后者在图编译时即被调用
        self.toolbox = toolbox
        self._graph = create_agent(
            name=self.agentic_id,
            system_prompt=self.build_system_prompt(),
            model=self.model,
            tools=[_cancel_guard(t) for t in self.build_tools()],
            transformers=[ToolsTransformer],
        )

    def stream(self, ctx: AgentRunContext):
        """启动流式运行，返回 langgraph 运行句柄（消费方用 interleave 遍历）。"""
        return self._graph.stream_events(
            version="v3",
            input=self._input(ctx),
            config=self._config(ctx),
        )

    def invoke(self, ctx: AgentRunContext) -> str:
        """非流式单次调用：走完整智能体流程（含工具循环），返回最终 assistant 文本。

        供非流式单次调用场景使用，不产出 ag-ui 事件。
        """
        result = self._graph.invoke(
            input=self._input(ctx),
            config=self._config(ctx),
        )
        content = result["messages"][-1].content
        return content if isinstance(content, str) else str(content)

    def _input(self, ctx: AgentRunContext) -> dict:
        # 历史消息原样回放（图无 checkpointer，多轮上下文靠输入携带）；
        # 运行期动态信息（当前时间）注入末条用户消息（当前轮），图本身不随运行变化
        messages = list(ctx.messages)
        if messages:
            messages[-1] = HumanMessage(f"[当前时间：{ctx.now}]\n\n{messages[-1].content}")
        return {"messages": messages}

    def _config(self, ctx: AgentRunContext) -> RunnableConfig:
        configurable: dict = {"thread_id": ctx.thread_id, "run_id": ctx.run_id, "user_id": ctx.user_id}
        if ctx.cancel_check is not None:
            configurable["cancel_check"] = ctx.cancel_check
        return {"configurable": configurable}

    @abstractmethod
    def build_system_prompt(self) -> str:
        """返回该智能体的 system prompt（构建期静态，不含单次运行信息）。"""

    def build_tools(self) -> list:
        """返回该智能体携带的工具列表（构建期静态），默认无工具。"""
        return []


# 智能体注册表：@register_agent 按 agentic_id 自动登记，新增智能体不改工厂
AGENT_REGISTRY: dict[str, type[BaseAgent]] = {}


def register_agent(agent_cls: type[BaseAgent]) -> type[BaseAgent]:
    AGENT_REGISTRY[agent_cls.agentic_id] = agent_cls
    return agent_cls
