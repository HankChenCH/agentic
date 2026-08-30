"""memory 组件清单：能力声明（spec）+ 工具构造 + 装配器。

能力导出 = 深度回忆三件套（timeline/expand/state_at）；快速回忆不走
工具——会话开始前由 AgenticService 自动注入（见 ability.recall 的
build_fast_context）。三件套均需当前会话 thread 做去重登记与访问强化。

thread 的传递方式：不能闭包捕获（agent 实例按 agentic_id 跨会话缓存），
也不能用 ContextVar——编排层是 sync 生成器，Starlette 每次恢复都在不同
context 副本里执行，langgraph 又把工具放进自己的线程池线程执行，ContextVar
的 set/reset 跨不过这些边界（工具拿到 None、token reset 直接 ValueError
并打断 SSE 流）。故走 langchain 的 config 注入：工具函数声明 ``config:
RunnableConfig`` 参数（StructuredTool 按显式 args_schema 构造，该参数
不进 LLM 工具 schema、运行时注入），langgraph 把 ``BaseAgent._config``
的 ``configurable.thread_id / user_id`` 透传进来，与执行线程/context 无关。
user_id 决定记忆作用域（用户级隔离），thread_id 仅用于会话内投喂去重。
"""

from dataclasses import dataclass

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from wireup import injectable

from app.components.base import (
    ComponentSpec,
    ToolSpec,
    configurable_thread,
    configurable_user,
    register_component,
)
from app.components.memory.ability.recall import MemoryRecallService


class TimelineArgs(BaseModel):
    """timeline 工具参数。"""

    query: str = Field(description="检索线索：人物、事件、时间等关键词")


class ExpandArgs(BaseModel):
    """expand 工具参数。"""

    entity: str = Field(description="要展开的规范名称（人名/物名等）")


class StateAtArgs(BaseModel):
    """state_at 工具参数。"""

    time: str = Field(description="要回放的日期，YYYY-MM-DD 格式")


def _build_timeline_tool(memory_service: MemoryRecallService) -> StructuredTool:
    def timeline(query: str, config: RunnableConfig) -> str:
        return memory_service.timeline(
            query=query, user_id=configurable_user(config), thread_id=configurable_thread(config)
        )

    return StructuredTool.from_function(
        name="timeline",
        description=(
            "按线索检索过去发生的事件情节（何时何地发生了什么），返回带时间锚与"
            "溯源编号的记忆片段。涉及用户经历、项目来龙去脉、事件背景时使用；"
            "多数问题检索一次即可，确有缺口再补充不同线索继续查。"
        ),
        args_schema=TimelineArgs,
        func=timeline,
        infer_schema=False,
    )


def _build_expand_tool(memory_service: MemoryRecallService) -> StructuredTool:
    def expand(entity: str, config: RunnableConfig) -> str:
        return memory_service.expand(
            entity_ref=entity, user_id=configurable_user(config), thread_id=configurable_thread(config)
        )

    return StructuredTool.from_function(
        name="expand",
        description=(
            "展开某个人/物/机构/概念的关联记忆网：它相关的既有事实与经历事件。"
            "当 timeline 或对话中出现关键对象、需要摸清其关系脉络时使用；"
            "entity 传规范名称（人名/物名等）。"
        ),
        args_schema=ExpandArgs,
        func=expand,
        infer_schema=False,
    )


def _build_state_at_tool(memory_service: MemoryRecallService) -> StructuredTool:
    def state_at(time: str, config: RunnableConfig) -> str:
        return memory_service.state_at(
            moment_hint=time, user_id=configurable_user(config), thread_id=configurable_thread(config)
        )

    return StructuredTool.from_function(
        name="state_at",
        description=(
            "时点回放：查询某个过去日期当时仍在生效的事实状态（含此后被新值取代"
            "的历史情况）。适合“那时候/当时他在做什么”类问题；time 用 "
            "YYYY-MM-DD 格式。"
        ),
        args_schema=StateAtArgs,
        func=state_at,
        infer_schema=False,
    )


_SPEC = register_component(ComponentSpec(
    name="memory",
    title="长期记忆",
    description=(
        "跨会话长期记忆：会话前自动注入快速回忆上下文（非工具），并提供"
        "深度回忆三件套供 agent 按需检索历史事件、人物关联与时点状态。"
    ),
    tools=(
        ToolSpec(
            name="timeline",
            description="深度·情节检索：按线索查过去发生的事件情节（何时何地发生了什么）。",
            args_model=TimelineArgs,
            build=_build_timeline_tool,
        ),
        ToolSpec(
            name="expand",
            description="深度·图扩散：展开某个人/物/机构的关联记忆网（既有事实与经历事件）。",
            args_model=ExpandArgs,
            build=_build_expand_tool,
        ),
        ToolSpec(
            name="state_at",
            description="深度·时点回放：查询某过去日期当时仍在生效的事实状态。",
            args_model=StateAtArgs,
            build=_build_state_at_tool,
        ),
    ),
))


@injectable
@dataclass
class MemoryComponent:
    """memory 组件装配器：持能力门面服务，把 spec 声明实例化为可运行工具。

    ``agentic_id`` 形参是与 ``KnowledgeComponent.tools`` 的统一装配接口；
    记忆工具不绑定 agent 身份（thread 会话身份经 langgraph config 注入）。
    """

    recall: MemoryRecallService

    @property
    def spec(self) -> ComponentSpec:
        return _SPEC

    def tools(self, agentic_id: str) -> list[StructuredTool]:
        return [tool.build(self.recall) for tool in _SPEC.tools]
