import functools
import inspect
from abc import ABC, abstractmethod
from typing import Callable, ClassVar

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from app.agents.context import AgentRunContext
from app.agents.toolbox import AgentToolbox
from app.agents.tools_transformer import ToolsTransformer


class RunCanceledError(Exception):
    """工具入口守卫检测到取消标志：跳过工具体（langgraph 记 tool-error），
    随后由编排层的边界检查终止本轮——不继承 BaseException，避免绕过
    langgraph 的常规错误处理路径。"""


def _cancel_guard(tool: Callable):
    """工具入口的协作式取消检查（显式取消通道的工具侧半边）。

    检查闭包由编排层经 ``AgentRunContext.cancel_check`` → ``_config`` 的
    ``configurable.cancel_check`` 传入，本包装在每次工具被调用前读取：命中即
    抛 ``RunCanceledError`` 跳过工具体（省掉一次无谓的检索/LLM 外呼），随后
    编排层流式循环的边界检查终止本轮。

    包装细节：对未声明 ``config`` 参数的工具（weather/knowledge 系），显式
    合成带 ``config: RunnableConfig`` 的签名与注解——langchain 据此把它判为
    注入参数（不进 LLM 工具 schema、运行时以 kwargs 注入，同 memory 工具的
    既有模式）；已声明的（memory 系）经 ``wraps`` 的 ``__wrapped__`` 原样透出。
    注入的 config 不回传给不接收它的原函数。
    """
    sig_params = list(inspect.signature(tool).parameters.values())
    has_config = any(p.name == "config" for p in sig_params)

    @functools.wraps(tool)
    def wrapper(*args, **kwargs):
        config = kwargs.get("config")
        check = (config or {}).get("configurable", {}).get("cancel_check")
        if check is not None and check():
            raise RunCanceledError("run canceled by client")
        if not has_config:
            kwargs.pop("config", None)
        return tool(*args, **kwargs)

    if not has_config:
        params = [*sig_params, inspect.Parameter(
            "config", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=None, annotation=RunnableConfig,
        )]
        wrapper.__signature__ = inspect.Signature(params)
        annotations = dict(getattr(tool, "__annotations__", None) or {})
        annotations["config"] = RunnableConfig
        wrapper.__annotations__ = annotations
    return wrapper


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
        configurable: dict = {"thread_id": ctx.thread_id, "run_id": ctx.run_id}
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
