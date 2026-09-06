"""agent 中间件机制面（动态 system prompt + 上下文压缩）。

## 动态 system prompt（DynamicSystemPromptMiddleware）

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

## 上下文压缩（ContextSummarizationMiddleware + 消费侧过滤契约）

历史全量回放（图无 checkpointer）+ 工具结果大块入流，长会话迟早越过模型
上下文窗口。``ContextSummarizationMiddleware`` 在 token 数达到阈值（模型
profile ``max_input_tokens`` × fraction）时把较旧消息压缩成一条摘要消息、
保留尾部消息——窗口声明来自 llm entry 的 ``context_window``（builder 透传为
模型 profile，见 ``ModelBuilder.apply_context_window``），模型类缺 profile
（未声明窗口）则不装配。

摘要机制与流式消费链有一个必须显式处理的交互：摘要会（a）触发一次内部
摘要模型调用、（b）把摘要消息写进图状态——两者都会以 ``ChatModelStream``
条目形态出现在 ``interleave("messages")`` 投影里，被 AgUiTranslator 流成
前端文本、被 StorageTranslator 落库污染历史。防泄漏组合拳（已实证）：

- ``CallbackIsolatedChatModel`` 包住摘要模型：invoke 时显式置空 callbacks，
  内部摘要调用不进投影（协议事件路径靠回调捕获，隔离即消失）——摘要是
  平台侧机制调用，不进用户可见流、不进用量统计；
- 子类覆写 ``_build_new_messages`` 给摘要消息盖 ``SUMMARY_MESSAGE_ID_PREFIX``
  前缀 id（整条消息重放保留 id，langgraph 把消息 role 硬编码为 ai、无法按
  类型过滤），消费侧以 ``is_internal_stream_item`` 过滤。

⚠️ 依赖面：id 盖章覆写的是 langchain 私有 staticmethod，升级若变更该实现，
过滤静默失效——失效模式是摘要文案漏到前端（可见故障），非静默数据损坏。
消费侧过滤点：application.AgenticService 的流式循环（messages 投影条目
在翻译/落库前跳过）。
"""

import functools
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, SummarizationMiddleware
from langchain.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from app.agents.context import AgentRunContext

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面（惯例同 api/exception_handlers）
logger = logging.getLogger(__name__)


# ==================== 上下文压缩 ====================

# 摘要消息的 id 前缀：消费侧据此把摘要条目从流/落库中滤除（见模块 docstring）
SUMMARY_MESSAGE_ID_PREFIX = "agentic-internal-summary-"

# 压缩触发阈值：token 数达到 max_input_tokens × 0.8 时摘要（留 20% 给摘要
# 产物与当轮回复）；keep：摘要后保留尾部消息条数（AI/工具配对的完整性由
# langchain 中间件保证）
CONTEXT_TRIGGER_FRACTION = 0.8
CONTEXT_KEEP_MESSAGES = 20


def model_max_input_tokens(model: Any) -> int | None:
    """读模型 profile 的 max_input_tokens（langchain 机制同款判据）。

    非 Mapping / 缺字段 / 非整数一律视为未声明（None）——桩模型与裸模型类
    安全。``BaseAgent`` 据此决定是否装配压缩中间件。
    """
    profile = getattr(model, "profile", None)
    if not isinstance(profile, Mapping):
        return None
    value = profile.get("max_input_tokens")
    return value if isinstance(value, int) else None


def is_internal_stream_item(item: Any) -> bool:
    """消费侧判据：interleave("messages") 条目是否为内部摘要机制产物。

    按 ``SUMMARY_MESSAGE_ID_PREFIX`` 前缀识别（id 在整条消息重放中保留）；
    不读 item 的 text/reasoning 投影——访问会驱动图 pump（阻塞）。tools 投影
    条目（dict）与普通消息条目无该前缀，恒为 False，对调用方零副作用。
    """
    message_id = getattr(item, "message_id", None)
    return isinstance(message_id, str) and message_id.startswith(SUMMARY_MESSAGE_ID_PREFIX)


class CallbackIsolatedChatModel:
    """摘要模型包装：调用时显式置空 callbacks，调用不进 langgraph 投影。

    langgraph 的 messages 投影经回调捕获模型调用（含非流式 invoke 的完整
    消息）；langchain Runnable 会从 ContextVar 继承父级 callbacks，图内调用
    默认全被投影。摘要调用是平台侧机制，泄漏形态 = 摘要文本流到前端 + 落库
    污染历史——包装在 invoke/ainvoke 的 config 上显式传空 callbacks（实证：
    显式值替换而非合并父级）切断传播。

    鸭子类型满足 ``SummarizationMiddleware`` 的消费面（invoke + ``_llm_type``
    的 token counter 选择 + ``profile`` 的阈值读取）；不继承 BaseChatModel，
    避免把包装实例误当可绑定/可计量的真实模型。
    """

    def __init__(self, inner: BaseChatModel):
        self._inner = inner

    @property
    def _llm_type(self) -> str:
        return self._inner._llm_type

    @property
    def profile(self):
        return self._inner.profile

    def _isolated_config(self, config: dict | None) -> dict:
        return {**(config or {}), "callbacks": []}

    def invoke(self, messages, config=None, **kwargs):
        return self._inner.invoke(messages, config=self._isolated_config(config), **kwargs)

    async def ainvoke(self, messages, config=None, **kwargs):
        return await self._inner.ainvoke(messages, config=self._isolated_config(config), **kwargs)


class ContextSummarizationMiddleware(SummarizationMiddleware):
    """上下文压缩中间件：token 越阈值即把旧消息折叠成一条摘要消息。

    相对 langchain 原类的两点适配：触发/保留参数以本仓库口径给缺省
    （fraction 0.8 / keep 20 条）；摘要消息盖内部 id 前缀（见模块 docstring
    的防泄漏契约）。摘要模型必须经 ``CallbackIsolatedChatModel`` 包装传入，
    传裸模型会把摘要调用泄漏进投影。
    """

    def __init__(self, model, *, trigger=None, keep=None, **kwargs):
        super().__init__(
            model,
            trigger=trigger if trigger is not None else ("fraction", CONTEXT_TRIGGER_FRACTION),
            keep=keep if keep is not None else ("messages", CONTEXT_KEEP_MESSAGES),
            **kwargs,
        )

    @staticmethod
    def _build_new_messages(summary: str) -> list[HumanMessage]:
        return [HumanMessage(
            id=f"{SUMMARY_MESSAGE_ID_PREFIX}{uuid4().hex}",
            content=f"Here is a summary of the conversation to date:\n\n{summary}",
        )]


# ==================== 动态 system prompt ====================


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
