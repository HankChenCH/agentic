import functools
import inspect
import logging
from abc import ABC, abstractmethod
from typing import Callable, ClassVar
from uuid import UUID

from langchain.agents import create_agent
from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph.state import CompiledStateGraph

from app.agents.context import AgentRunContext
from app.agents.toolbox import AgentToolbox
from app.agents.tools_transformer import ToolsTransformer

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面（惯例同 api/exception_handlers）
logger = logging.getLogger(__name__)


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
    build。图本身构建期静态、不随运行变化：人设字符串虽也只依赖智能体身份，
    但其注入时机在每轮 ``_input``——与快速记忆块合并为头部 system 消息（快注
    是用户级常驻摘要，每轮内容不同，不能烘进图）；多轮历史与运行期动态信息
    （当前时间等）同样通过 ``AgentRunContext`` 在每次调用时随输入消息注入。

    子类供给差异点：
    - ``build_system_prompt``（必须实现）：智能体人设；
    - ``build_tools``（默认无工具）：以组合方式装配能力（如记忆召回、知识库检索），
      能力依赖经构造注入的 ``toolbox`` 获取；
    - ``_system_prompt``（默认人设 + 快速记忆块）：每轮 system prompt 组装，
      不想要快注的子类覆写之（RAG 因节点按文本渲染历史，整体覆写 ``_input``
      把块折进用户消息）；
    - ``build_graph``（默认预置 ReAct 循环）：自建 StateGraph 的智能体覆写，
      并按需覆写 stream/invoke；
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
        # toolbox 必须先于 build_graph() 赋值：后者在图编译时即被调用
        self.toolbox = toolbox
        # 快速回忆门面：每轮 _input 组装 system prompt 时消费（agents ──► components 合法边）
        self.recall = toolbox.memory.recall
        self._graph = self.build_graph()

    def build_graph(self) -> CompiledStateGraph:
        """编译智能体图（实例化时调用一次，stream/invoke 复用）。

        默认实现 = langchain 预置的 model↔tools ReAct 循环；人设不在编译期
        烘入图（system_prompt 参数不传），由 ``_input`` 每轮随输入消息注入。
        需要自建 StateGraph（自有节点与循环，如 RAG 智能体）的子类覆写本方法，
        并按需覆写 stream/invoke 以适配自定义图的状态形状与流式语义。
        """
        return create_agent(
            name=self.agentic_id,
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
        # 运行期动态信息注入两处：快速记忆块并入头部 system prompt（常驻
        # 语境，不侵入用户消息），当前时间注入末条用户消息（当前轮）
        messages = list(ctx.messages)
        if messages:
            messages[-1] = HumanMessage(f"[当前时间：{ctx.now}]\n\n{messages[-1].content}")
        return {"messages": [SystemMessage(self._system_prompt(ctx)), *messages]}

    def _system_prompt(self, ctx: AgentRunContext) -> str:
        """每轮 system prompt：人设 + 快速记忆块（常驻摘要，不侵入用户消息）。

        快注块不落库、每轮重算，历史回放不受影响。
        """
        persona = self.build_system_prompt()
        block = self._fast_memory_block(ctx)
        return f"{persona}\n\n{block}" if block else persona

    def _fast_memory_block(self, ctx: AgentRunContext) -> str:
        """快速记忆块（用户级常驻摘要）；当前轮用户 query 仅作寒暄短路判别。

        检索失败只记日志不阻断（口径同原编排层注入），降级为空块。
        """
        if not ctx.messages:
            return ""
        try:
            return self.recall.build_fast_context(
                query=str(ctx.messages[-1].content),
                user_id=UUID(ctx.user_id),
                thread_id=UUID(ctx.thread_id),
            )
        except Exception:
            logger.warning("build fast memory context failed", exc_info=True)
            return ""

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
