import logging
from abc import ABC, abstractmethod
from typing import ClassVar
from uuid import UUID

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from app.agents.cancel import CancelGuardMiddleware
from app.agents.context import AgentRunContext
from app.agents.middleware import (
    DynamicSystemPromptMiddleware,
    PromptContext,
    PromptFragment,
    render_system_prompt,
)
from app.agents.toolbox import AgentToolbox
from app.agents.tools_transformer import ToolsTransformer

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面（惯例同 api/exception_handlers）
logger = logging.getLogger(__name__)


def _content_text(content) -> str:
    """消息内容的纯文本提取（agents 层本地实现，不越层依赖 domain 的
    multimodal 翻译器）：str 原样；块列表取 text 块拼接；其余转 str。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text")
        )
    return str(content)


class BaseAgent(ABC):
    """智能体基类（模板方法）：标准 ReAct 型智能体只声明差异，机制归基类。

    声明面（子类要写的全部）：
    - ``build_system_prompt``（必须实现）：PromptTemplate 兼容的模板字符串
      （f-string 槽位）。静态槽（如 ``{tools}``）构建期由基类填充并烘进图；
      动态槽（如 ``{memory}``）必须以 ``<slot>``/``</slot>`` 信封包裹，每轮由
      动态 prompt 中间件渲染，产出为空则整节移除——模板没有的片段静默失配
      （自动退役），模板加了槽没有片段则构建期报错；
    - ``prompt_fragments``（默认提供 memory 快注 + 知识库清单 + 坐席清单片段，
      模板加槽即 opt-in）：动态槽渲染字典（占位符名 → ``(PromptContext) ->
      str``，异常/空串由中间件统一降级）；
    - ``build_tools``（默认无工具）：以组合方式装配能力（如记忆深度回忆、
      知识库检索），能力依赖经构造注入的 ``toolbox`` 获取；
    - ``build_middleware``（默认挂动态 prompt 中间件）：中间件装配扩展点，
      需要叠加其他内置中间件（摘要/重试/调用限额等）的子类覆写并以
      ``super()`` 起始；
    - ``build_transformers``（默认仅 ``ToolsTransformer``）：流 transformer
      装配扩展点，需改写前端透传内容的子类覆写；
    - ``preferred_provider``（默认走全局配置）：智能体级模型路由，取值为
      ``LLMConfig.providers`` 的 entry key。

    特殊拓扑的智能体可覆写 ``build_graph`` 并按需覆写
    ``stream/invoke/_input``——中间件与模板机制只服务默认 ReAct 路径（当前
    内置智能体均走默认路径）。

    每轮运行事实（``AgentRunContext``）经 langgraph ``context=`` 参数传入，
    中间件经 ``request.runtime.context`` 取回——取消守卫（``CancelGuardMiddleware``，
    本类 ``build_graph`` 强制前置，不经 ``build_middleware`` 装配面）据此检查
    ``ctx.cancel_check``；``thread_id/user_id`` 仍经 ``_config`` 的 configurable
    透传给声明了 config 形参的工具。图在实例化时编译一次（``self._graph``），
    stream/invoke 复用，不重复 build。

    仅当"身份"不同（人设、pipeline、模型路由）才需要新子类；工具增减通过
    ``build_tools`` 传参即可，不必新建智能体类。
    """

    # 展示元数据：目录端点（GET /agentic/agents）消费的纯展示字段，
    # 不进 LLM schema、不参与运行时行为（title 口径对齐组件 manifest）
    display_name: ClassVar[str]
    description: ClassVar[str]

    agentic_id: ClassVar[str]  # <agent_type:agent_name>，eg "builtin:demo"
    preferred_provider: ClassVar[str | None] = None

    _graph: CompiledStateGraph

    def __init__(self, model: BaseChatModel, toolbox: AgentToolbox):
        self.model = model
        # toolbox 必须先于 build_graph() 赋值：后者在图编译时即被调用
        self.toolbox = toolbox
        # 快速回忆门面：默认 memory 片段（与 RAG 折叠路径）消费（agents ──► components 合法边）
        self.recall = toolbox.memory.recall
        self._graph = self.build_graph()

    @property
    def supports_vision(self) -> bool:
        """模型是否声明了图片输入能力（capabilities.multimodal 含 ``vision``）。

        声明随模型实例走（``ThinkingAwareChatDeepSeek`` 由 builder 透传 entry
        的 ``capabilities.multimodal``）；未包装的裸模型类无该属性，视为未
        声明——编排层据此把图片输入降级为纯文本，宁可丢图不发模型不认的载荷。
        """
        return "vision" in getattr(self.model, "multimodal", ())

    def build_graph(self) -> CompiledStateGraph:
        """编译智能体图（实例化时调用一次，stream/invoke 复用）。

        默认实现 = langchain 预置 model↔tools ReAct 循环 + 声明式 system
        prompt 装配：模板槽位经 ``PromptTemplate`` 自动推导并校验覆盖（占位符
        无片段提供者 → ValueError，含缺失槽名清单），静态槽实渲后烘进图作
        基线，动态槽由中间件在每次模型调用时渲染并覆写 ``system_message``
        （单趟渲染，片段产出不被二次扫描）。取消守卫（``CancelGuardMiddleware``）
        在此强制前置——工具入口的协作式取消是编排层契约，不进 ``build_middleware``
        装配面。需要自建 StateGraph 的子类覆写本方法，并按需覆写 stream/invoke
        以适配自定义图的状态形状与流式语义。
        """
        template = PromptTemplate.from_template(self.build_system_prompt())
        static_values = self._static_prompt_values()
        fragments = self.prompt_fragments()
        missing = sorted(set(template.input_variables) - set(static_values) - set(fragments))
        if missing:
            raise ValueError(
                f"{self.agentic_id} 的 system prompt 模板占位符缺少提供者: {missing}"
                f"（静态槽: {sorted(static_values)}；已声明片段: {sorted(fragments)}）"
            )
        return create_agent(
            name=self.agentic_id,
            model=self.model,
            system_prompt=self._baseline_prompt(template, static_values, fragments),
            tools=self.build_tools(),
            transformers=self.build_transformers(),
            middleware=[CancelGuardMiddleware(), *self.build_middleware(template, static_values, fragments)],
        )

    def _baseline_prompt(self, template: PromptTemplate, static_values: dict, fragments: dict) -> str:
        """基线 system prompt：静态槽实渲 + 动态槽取空（整节移除）。

        正常路径下动态 prompt 中间件每次模型调用以完整渲染结果覆写基线；
        仅当子类整体覆写 ``build_middleware`` 未挂该中间件时基线生效（片段
        声明随之失效，属显式豁免）。
        """
        dynamic_slots = [s for s in fragments if s in template.input_variables]
        values = {**static_values, **{slot: "" for slot in dynamic_slots}}
        return render_system_prompt(template, values, dynamic_slots)

    def build_middleware(self, template: PromptTemplate, static_values: dict, fragments: dict) -> list:
        """中间件装配（扩展点）。模板存在动态槽时默认挂动态 system prompt
        中间件；无动态槽（或片段全部失配模板）则不挂——每轮重渲静态串纯属
        浪费。需要叠加其他中间件的子类覆写并以 ``super()`` 起始。

        取消守卫不在此装配：``build_graph`` 已强制前置 ``CancelGuardMiddleware``
        （列表首位 = wrap_tool_call 链最外层），覆写本方法不影响取消语义。"""
        if not any(slot in template.input_variables for slot in fragments):
            return []
        return [DynamicSystemPromptMiddleware(template, static_values, fragments)]

    def prompt_fragments(self) -> dict[str, PromptFragment]:
        """动态槽片段字典（扩展点）：占位符名 → 渲染函数。默认提供 memory 快注、
        知识库清单、坐席清单三条片段；子类增删条目即可增减动态节，模板没有
        对应占位符的条目自动失效（不渲染、零开销）——模板加槽即 opt-in。"""
        return {
            "memory": self._memory_fragment,
            "knowledge_bases": self._knowledge_fragment,
            "human_agents": self._human_agents_fragment,
        }

    def _memory_fragment(self, pctx: PromptContext) -> str:
        if pctx.ctx is None:
            return ""
        return self._fast_memory_block(pctx.ctx)

    def _knowledge_fragment(self, pctx: PromptContext) -> str:
        """知识库清单片段（``{knowledge_bases}`` 槽）：当前用户可见库清单，检索
        直接从清单取 kb_ids，省去先调 knowledge_list 的一轮工具往返。数据每轮
        现取（含可见性过滤），渲染失败降级为空节。"""
        if pctx.ctx is None:
            return ""
        try:
            return self.toolbox.knowledge.retrieval.list_visible_knowledge_digest(UUID(pctx.ctx.user_id))
        except Exception:
            logger.warning("build knowledge digest fragment failed", exc_info=True)
            return ""

    def _human_agents_fragment(self, pctx: PromptContext) -> str:
        """人工客服坐席清单片段（``{human_agents}`` 槽）：在线优先的坐席目录，
        转人工场景直接从清单选人，省去 human_agent_list 工具往返。"""
        del pctx  # 坐席是全局资源，无需用户身份
        try:
            return self.toolbox.human_agent.directory.list_digest()
        except Exception:
            logger.warning("build human agent digest fragment failed", exc_info=True)
            return ""

    def _static_prompt_values(self) -> dict[str, str]:
        """静态槽值（构建期一次）。``{tools}`` = 实际装配工具的「名称 + 首行
        用途」索引——从 ``build_tools()`` 装配结果生成而非全局注册表，经装配
        过滤后的工具集才是该 agent 的真相，模板索引在机制上不会与真实工具集
        漂移（工具增删只改本索引与 schema，人设模板零维护）。"""
        lines = []
        for tool in self.build_tools():
            name = getattr(tool, "name", None) or getattr(tool, "__name__", "")
            # BaseTool 走 description；裸函数（langchain 转工具前）只有 __doc__
            desc = getattr(tool, "description", None) or getattr(tool, "__doc__", None) or ""
            first_line = str(desc).strip().splitlines()[0].strip() if str(desc).strip() else ""
            lines.append(f"- {name}: {first_line}" if first_line else f"- {name}")
        return {"tools": "\n".join(lines)}

    def stream(self, ctx: AgentRunContext):
        """启动流式运行，返回 langgraph 运行句柄（消费方用 interleave 遍历）。

        ``context=ctx`` 使 ``AgentRunContext`` 经 langgraph runtime 可达
        （动态 prompt 中间件经 ``request.runtime.context`` 读取）——v3 的
        ``stream_events`` 把该参数透传给底层 stream。
        """
        return self._graph.stream_events(
            version="v3",
            input=self._input(ctx),
            config=self._config(ctx),
            context=ctx,
        )

    def invoke(self, ctx: AgentRunContext) -> str:
        """非流式单次调用：走完整智能体流程（含工具循环），返回最终 assistant 文本。

        供非流式单次调用场景使用，不产出 ag-ui 事件。``context=`` 透传同
        ``stream``。
        """
        result = self._graph.invoke(
            input=self._input(ctx),
            config=self._config(ctx),
            context=ctx,
        )
        content = result["messages"][-1].content
        return content if isinstance(content, str) else str(content)

    def _input(self, ctx: AgentRunContext) -> dict:
        """图输入：历史消息原样回放（图无 checkpointer，多轮上下文靠输入携带），
        当前时间注入末条用户消息。人设与动态上下文不再进输入消息——人设基线
        烘在图上、动态槽由中间件每次模型调用渲染，任一时刻模型只收一条
        system 消息。

        时间前缀兼容两种末条内容形态：字符串拼接（纯文本，历史兼容）与
        块列表（多模态，前缀作为首个 text 块插入，图片块原样保留）。"""
        messages = list(ctx.messages)
        if messages:
            prefix = f"[当前时间：{ctx.now}]\n\n"
            last_content = messages[-1].content
            if isinstance(last_content, str):
                messages[-1] = HumanMessage(prefix + last_content)
            else:
                messages[-1] = HumanMessage(content=[{"type": "text", "text": prefix}, *last_content])
        return {"messages": messages}

    def _fast_memory_block(self, ctx: AgentRunContext) -> str:
        """快速记忆块（用户级常驻摘要）；当前轮用户 query 仅作寒暄短路判别。

        检索失败只记日志不阻断（口径同原编排层注入），降级为空块。当前唯一
        消费方是默认 memory 片段（进模板 ``{memory}`` 槽，每次模型调用渲染）。
        """
        if not ctx.messages:
            return ""
        try:
            return self.recall.build_fast_context(
                query=_content_text(ctx.messages[-1].content),
                user_id=UUID(ctx.user_id),
                thread_id=UUID(ctx.thread_id),
            )
        except Exception:
            logger.warning("build fast memory context failed", exc_info=True)
            return ""

    def _config(self, ctx: AgentRunContext) -> RunnableConfig:
        configurable: dict = {"thread_id": ctx.thread_id, "run_id": ctx.run_id, "user_id": ctx.user_id}
        return {"configurable": configurable}

    @abstractmethod
    def build_system_prompt(self) -> str:
        """返回该智能体的 system prompt 模板（PromptTemplate 兼容的 f-string：
        静态槽如 ``{tools}`` 构建期由基类填充；动态槽如 ``{memory}`` 须以
        ``<slot>`` 信封包裹，每轮由 ``prompt_fragments`` 的片段渲染）。"""

    def build_transformers(self) -> list:
        """返回该智能体的流 transformer 类列表（构建期静态，扩展点）。

        默认仅 ``ToolsTransformer``（tools stream mode → 中间契约投影）；
        需要改写/过滤透传给前端的工具事件（如客服场景裁剪溯源内容）的
        子类覆写本方法。只影响流帧投影，不影响进 LLM 的 ToolMessage。
        """
        return [ToolsTransformer]

    def build_tools(self) -> list:
        """返回该智能体携带的工具列表（构建期静态），默认无工具。"""
        return []


# 智能体注册表：@register_agent 按 agentic_id 自动登记，新增智能体不改工厂
AGENT_REGISTRY: dict[str, type[BaseAgent]] = {}


def register_agent(agent_cls: type[BaseAgent]) -> type[BaseAgent]:
    # 展示元数据 fail-fast（对齐组件 manifest 的注册期校验风格）：
    # 目录端点的消费方是前端选择 UI，缺名/缺描述直接漏展示
    if not (agent_cls.display_name or "").strip() or not (agent_cls.description or "").strip():
        raise ValueError(
            f"agent {agent_cls.agentic_id} must declare non-empty display_name and description"
        )
    AGENT_REGISTRY[agent_cls.agentic_id] = agent_cls
    return agent_cls
