"""记忆工具集：深度回忆三件套的薄封装。

快速回忆不走工具——会话开始前由 ChatOrchestrator 自动注入（见
build_fast_context）。三件套均需当前会话 thread 做去重登记与访问强化。

thread 的传递方式：不能闭包捕获（agent 实例按 agentic_id 跨会话缓存），
也不能用 ContextVar——编排层是 sync 生成器，Starlette 每次恢复都在不同
context 副本里执行，langgraph 又把工具放进自己的线程池线程执行，ContextVar
的 set/reset 跨不过这些边界（工具拿到 None、token reset 直接 ValueError
并打断 SSE 流）。故走 langchain 的 config 注入：工具声明 ``config:
RunnableConfig`` 参数（不进 LLM 的工具 schema），运行时由 langgraph 把
``BaseAgent._config`` 的 ``configurable.thread_id`` 透传进来，与执行
线程/context 无关。
"""

from uuid import UUID

from langchain_core.runnables import RunnableConfig

from app.components.memory.service import MemoryService


def _thread(config: RunnableConfig) -> UUID | None:
    """从注入的运行配置取当前会话 thread；缺失（如单测直调）返回 None。"""
    raw = (config or {}).get("configurable", {}).get("thread_id")
    if raw is None:
        return None
    try:
        return raw if isinstance(raw, UUID) else UUID(str(raw))
    except ValueError:
        return None


def build_memory_tools(memory_service: MemoryService) -> list:
    """构造记忆工具集（供智能体 build_tools 装配），闭包绑定 MemoryService。"""

    def timeline(query: str, config: RunnableConfig) -> str:
        """
        按线索检索过去发生的事件情节（何时何地发生了什么），返回带时间锚与
        溯源编号的记忆片段。涉及用户经历、项目来龙去脉、事件背景时使用；
        多数问题检索一次即可，确有缺口再补充不同线索继续查。
        """
        return memory_service.timeline(query=query, thread_id=_thread(config))

    def expand(entity: str, config: RunnableConfig) -> str:
        """
        展开某个人/物/机构/概念的关联记忆网：它相关的既有事实与经历事件。
        当 timeline 或对话中出现关键对象、需要摸清其关系脉络时使用；
        entity 传规范名称（人名/物名等）。
        """
        return memory_service.expand(entity_ref=entity, thread_id=_thread(config))

    def state_at(time: str, config: RunnableConfig) -> str:
        """
        时点回放：查询某个过去日期当时仍在生效的事实状态（含此后被新值取代
        的历史情况）。适合“那时候/当时他在做什么”类问题；time 用
        YYYY-MM-DD 格式。
        """
        return memory_service.state_at(moment_hint=time, thread_id=_thread(config))

    return [timeline, expand, state_at]
