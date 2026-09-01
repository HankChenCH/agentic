"""动态 system prompt：模板 + 静态槽 + 动态片段中间件（声明式装配的机制面）。

agent 子类只声明两样东西（见 ``BaseAgent`` 类注释）——``build_system_prompt``
返回 ``PromptTemplate`` 兼容的模板字符串（f-string 槽位：静态槽如 ``{tools}``，
动态槽如 ``{memory}``，动态槽必须以 ``<slot>``/``</slot>`` 信封包裹，节内文字
随槽值一起出现/消失）；``prompt_fragments`` 返回动态槽的渲染字典。

装配语义（``BaseAgent.build_graph`` 强制）：
- 模板有占位符、fragments 无提供者 → 构建期 ValueError（防「模板加了槽、
  片段没跟上」的漂移，与 toolbox 构造期校验同风格）；
- fragments 有、模板无占位符 → 静默丢弃（agent 换模板后片段自动退役）；
- 片段产出为空 → 整节移除（连信封一起，省 token 且无空壳节）；
- 片段异常 → 记 warning 降级为空块（同快速记忆的既有口径，不阻断运行）。

渲染在每次模型调用前执行（``wrap_model_call`` 同步钩子；编排层走同步
``stream_events(v3)``，异步钩子在本仓库不可用）：静态槽值构建期缓存、动态槽
值逐片段现算，``PromptTemplate.format`` 单趟完成——占位符扫描只发生在模板
本身（开发者所有），片段产出不被二次扫描。人设基线经
``create_agent(system_prompt=...)`` 烘进图，中间件以完整渲染结果覆写
``ModelRequest.system_message``，任一时刻模型只收到一条 system 消息。

每轮运行事实经 langgraph 的 ``context=`` 参数传入（``BaseAgent.stream/invoke``
传 ``AgentRunContext``），中间件经 ``request.runtime.context`` 取回——
configurable 不进 runtime.context，ContextVar 在本仓库被证明跨不过编排层与
langgraph 的线程边界，context= 是唯一干净通道。
"""

import functools
import logging
import re
from dataclasses import dataclass
from typing import Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.messages import SystemMessage
from langchain_core.prompts import PromptTemplate

from app.agents.context import AgentRunContext

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面（惯例同 api/exception_handlers）
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptContext:
    """片段渲染可用的每轮运行事实。

    ``ctx`` 为整轮上下文（user_id/thread_id/now/messages）；中间件取不到
    runtime context（直调/异常环境）时为 None，片段应自行降级为空串。
    ``query`` 是当前轮用户消息文本（末条、未经时间前缀加工），供寒暄短路等
    判别——与 ``BaseAgent._fast_memory_block`` 的既有口径一致。
    """

    ctx: AgentRunContext | None
    query: str


# 片段契约：返回注入文本，空串 = 本轮不注入（整节移除）
PromptFragment = Callable[[PromptContext], str]


@functools.lru_cache(maxsize=None)
def _empty_section_re(slot: str) -> re.Pattern[str]:
    """空节移除模式：``<slot>…</slot>`` 信封连同行尾空白整体删除。

    槽位名来自 f-string 解析的标识符，可安全入正则。
    """
    return re.compile(rf"[ \t]*<{slot}>.*?</{slot}>[ \t]*\n?", re.DOTALL)


_BLANK_LINES_RE = re.compile(r"\n{3,}")


def render_system_prompt(
    template: PromptTemplate, values: dict[str, str], dynamic_slots: list[str]
) -> str:
    """单趟渲染 + 空节移除（烘基线与每轮渲染共用的唯一实现）。

    槽位覆盖已由 ``BaseAgent.build_graph`` 构建期校验，此处缺键即编程错误
    （format 的 KeyError 不再容忍第三种漂移来源）。
    """
    rendered = template.format(**values)
    for slot in dynamic_slots:
        if not values.get(slot):
            rendered = _empty_section_re(slot).sub("", rendered)
    return _BLANK_LINES_RE.sub("\n\n", rendered).strip()


class DynamicSystemPromptMiddleware(AgentMiddleware):
    """每次模型调用前把完整模板渲染为 system prompt（只覆写同步钩子）。

    静态槽构建期已由 ``BaseAgent`` 烘入图（system_prompt 基线）；本中间件以
    完整渲染结果覆写 ``ModelRequest.system_message``，正常路径下基线即被
    替换，仅作中间件缺位时的兜底。片段逐个渲染、失败降级为空（节移除），
    单片段故障不放大为整轮失败。
    """

    def __init__(
        self,
        template: PromptTemplate,
        static_values: dict[str, str],
        fragments: dict[str, PromptFragment],
    ):
        self.template = template
        self.static_values = dict(static_values)
        self.fragments = dict(fragments)
        # 实际生效的动态槽 = 片段 ∩ 模板占位符（模板没有的片段静默丢弃）
        self.dynamic_slots = [s for s in self.fragments if s in template.input_variables]

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        values = dict(self.static_values)
        if self.dynamic_slots:
            pctx = self._prompt_context(request)
            for slot in self.dynamic_slots:
                values[slot] = self._render_fragment(slot, pctx)
        rendered = render_system_prompt(self.template, values, self.dynamic_slots)
        return handler(request.override(system_message=SystemMessage(content=rendered)))

    def _prompt_context(self, request: ModelRequest) -> PromptContext:
        """从 langgraph runtime 取每轮上下文（``BaseAgent.stream/invoke`` 经
        ``context=`` 传入）。非 ``AgentRunContext``（直调/测试桩）按缺失降级。"""
        ctx = getattr(request.runtime, "context", None)
        if not isinstance(ctx, AgentRunContext):
            ctx = None
        query = str(ctx.messages[-1].content) if ctx is not None and ctx.messages else ""
        return PromptContext(ctx=ctx, query=query)

    def _render_fragment(self, slot: str, pctx: PromptContext) -> str:
        try:
            return self.fragments[slot](pctx) or ""
        except Exception:
            logger.warning("prompt fragment %r 渲染失败，降级为空节", slot, exc_info=True)
            return ""
