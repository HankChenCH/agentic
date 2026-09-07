from dataclasses import dataclass, field
from typing import Any, Dict, List
from uuid import UUID, uuid4

from app.application.translator.matrix import a2ui_messages
from app.domain.usage.extract import normalize_usage
from app.models.domain.agentic import (
    AgenticMessageRole,
    AgenticMessageType,
    ContentPartType,
    AgenticConversationMessage,
)
from app.models.domain.usage import UsageRecord, UsageScene
from app.packages.a2ui import A2UI_CUSTOM_EVENT_NAME


@dataclass
class UsageContext:
    """chat 场景用量流水的归属上下文（编排层注入，translator 不感知来源）。"""

    user_id: UUID
    model: str
    agentic_id: str | None = None


@dataclass(frozen=True)
class StorageContext:
    """落库归属上下文（每 run 一个，薄驱动器持有，fold 函数只读）。"""

    thread_id: UUID
    turn_id: UUID
    usage: UsageContext | None = None


@dataclass
class StorageState:
    """ag2store 边的显式折叠状态：整条 run 流式翻译累积出的中间产物，
    流毕由编排层一次性读取（messages 落库、usage_records flush）。"""

    messages: List[AgenticConversationMessage] = field(default_factory=list)
    usage_records: List[UsageRecord] = field(default_factory=list)
    sequence_num: int = 0
    # tool_call_id -> 该 TOOL_CALL 记录的 message_id，供 TOOL_RESULT 做父关联
    tool_call_msg_ids: Dict[str, UUID] = field(default_factory=dict)


# LangChain usage_metadata（input_tokens 族）→ 落库口径（prompt_tokens 族，
# 与轮次聚合白名单 USAGE_KEYS 及历史数据同键）。details 等其余键保持原名。
_USAGE_KEY_MAP = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens"}


# ----------------------------------------------------------------------
# 纯转换内核：状态经参数显式传递（无隐藏状态），模块级可直接单测
# ----------------------------------------------------------------------
def _next_seq(state: StorageState) -> int:
    state.sequence_num += 1
    return state.sequence_num


def _append_row(
    state: StorageState,
    ctx: StorageContext,
    *,
    message_id: UUID,
    parent_message_id: UUID | None,
    role: AgenticMessageRole,
    message_type: AgenticMessageType,
    content: list,
    token_usage: dict,
) -> None:
    state.messages.append(AgenticConversationMessage(
        thread_id=ctx.thread_id,
        turn_id=ctx.turn_id,
        message_id=message_id,
        parent_message_id=parent_message_id,
        sequence_num=_next_seq(state),
        role=role,
        message_type=message_type,
        content=content,
        token_usage=token_usage,
        latency_ms=0,
    ))


def read_usage(stream: Any) -> dict:
    """从 ChatModelStream 读 usage_metadata，键名归一成可落库的 dict。

    归一（input_tokens→prompt_tokens 族）修掉了与轮次聚合白名单的键名
    错位——此前轮级聚合只能收到 total_tokens，输入/输出全丢。
    """
    msg = getattr(stream, "output_message", None)
    usage = getattr(msg, "usage_metadata", None) if msg is not None else None
    if not usage:
        return {}
    # usage_metadata 是 TypedDict（UsageInfo），转成普通 dict 存储
    return {_USAGE_KEY_MAP.get(key, key): value for key, value in dict(usage).items()}


def collect_usage(state: StorageState, ctx: StorageContext, usage: dict) -> None:
    """chat 场景用量流水累积：每次模型调用一行，编排层在流收尾（含取消/
    断连）统一 flush——半截消息不落库，但已消耗的 token 计入统计。"""
    if ctx.usage is None:
        return
    normalized = normalize_usage(usage)
    if not normalized:
        return
    state.usage_records.append(UsageRecord(
        user_id=ctx.usage.user_id,
        scene=UsageScene.CHAT,
        model=ctx.usage.model,
        input_tokens=normalized["prompt_tokens"],
        output_tokens=normalized["completion_tokens"],
        total_tokens=normalized["total_tokens"],
        thread_id=ctx.thread_id,
        turn_id=ctx.turn_id,
        agentic_id=ctx.usage.agentic_id,
    ))


def fold_messages_frame(
    state: StorageState, ctx: StorageContext, message_id: UUID, stream: Any
) -> None:
    """一条 ChatModelStream 拆成一组行（纯转换，状态经参数显式传递）。

    ChatModelStream 在 message-finish 后所有投影都是非阻塞读，无需边遍历
    边累积，直接读终值。拆条规则（sequence_num 在整条流内严格递增）：
    reasoning 非空 → 1 条 THOUGHT（无 text 时持有注入 id）；text 非空 →
    1 条 MESSAGE（token_usage 填这里，持有注入 id）；每个 tool_call →
    1 条 TOOL_CALL（parent 指向本条最近的 thought/message，纯 tool_call
    无文本时无父）。

    行内 id 串：thought/message/tool_call 之间用 parent_message_id 串起来；
    注入的 message_id 与流式侧（ag-ui 事件）保持一致，按 text 行优先、
    无 text 时 thought 行兜底归属。
    """
    reasoning = str(stream.reasoning) if stream.reasoning else ""
    text = str(stream.text) if stream.text else ""
    tool_calls = stream.tool_calls.get() if stream.tool_calls else []
    usage = read_usage(stream)
    collect_usage(state, ctx, usage)

    message_msg_id: UUID | None = None

    if reasoning:
        # 无 text 的消息（纯 reasoning/中断）由 thought 行持有注入 id，
        # 与 ag-ui 只发 ReasoningMessage 事件的形态对齐
        thought_id = message_id if not text else uuid4()
        _append_row(
            state, ctx,
            message_id=thought_id, parent_message_id=None,
            role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.THOUGHT,
            content=[{"type": ContentPartType.TEXT, "text": reasoning}],
            token_usage={},
        )
        # 后续 message/tool_call 的 parent 指向 thought
        message_msg_id = thought_id

    if text:
        # text 行是对客户端可见的主体消息，持有与流式侧一致的注入 id
        _append_row(
            state, ctx,
            message_id=message_id, parent_message_id=message_msg_id,
            role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.MESSAGE,
            content=[{"type": ContentPartType.TEXT, "text": text}],
            token_usage=usage,
        )
        message_msg_id = message_id

    for tool_call in tool_calls:
        tc_id = uuid4()
        tool_call_id = tool_call.get("id", "") or ""
        if tool_call_id:
            state.tool_call_msg_ids[tool_call_id] = tc_id
        _append_row(
            state, ctx,
            message_id=tc_id, parent_message_id=message_msg_id,
            role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.TOOL_CALL,
            content=[{
                "type": ContentPartType.TOOL_CALL,
                "tool_call_id": tool_call_id,
                "name": tool_call.get("name", ""),
                "args": tool_call.get("args", {}),
            }],
            token_usage={},
        )


def fold_tools_frame(state: StorageState, ctx: StorageContext, payload: dict[str, Any]) -> None:
    """一个工具通道条目拆条（纯转换，状态经参数显式传递）。

    TOOL_CALL 行的落库来源有二：模型发起的调用由 messages 路径按
    ``stream.tool_calls`` 落库（先于 tool-started 处理，按 tool_call_id
    去重）；无模型 tool_call 的自造工具步骤（如有）由 tool-started 在此
    补齐，args 取事件携带的可选字段。tool-result 只落 TOOL_RESULT 行并
    关联父 TOOL_CALL；双内容契约：content 恒为真实结果（审计），display
    是前端展示版（如客服检索脱溯源摘要），同行落 display_content 供历史
    出口替换。tool-result 携带 UI 载荷（``ui.a2ui``）时追加一条 CUSTOM 行
    供前端历史还原卡片；tool-finished / tool-error 不落库（结果在
    tool-result，错误无结果可存）。
    """
    event_type = payload.get("event")
    if event_type == "tool-started":
        tool_call_id = payload.get("tool_call_id", "")
        if not tool_call_id or tool_call_id in state.tool_call_msg_ids:
            return
        tc_id = uuid4()
        state.tool_call_msg_ids[tool_call_id] = tc_id
        _append_row(
            state, ctx,
            message_id=tc_id, parent_message_id=None,
            role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.TOOL_CALL,
            content=[{
                "type": ContentPartType.TOOL_CALL,
                "tool_call_id": tool_call_id,
                "name": payload.get("tool_name", ""),
                "args": payload.get("args", {}),
            }],
            token_usage={},
        )
        return
    if event_type != "tool-result":
        return

    tool_call_id = payload.get("tool_call_id", "")
    content = payload.get("content", "")
    parent_id = state.tool_call_msg_ids.get(tool_call_id)

    result_part: dict[str, Any] = {
        "type": ContentPartType.TOOL_RESULT,
        "tool_call_id": tool_call_id,
        "content": content,
    }
    display = payload.get("display")
    if display is not None:
        result_part["display_content"] = display

    _append_row(
        state, ctx,
        message_id=uuid4(), parent_message_id=parent_id,
        role=AgenticMessageRole.TOOL,
        message_type=AgenticMessageType.TOOL_RESULT,
        content=[result_part],
        token_usage={},
    )

    a2ui = a2ui_messages(payload)
    if a2ui is not None:
        _append_row(
            state, ctx,
            message_id=uuid4(), parent_message_id=parent_id,
            role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.CUSTOM,
            content=[{
                "type": ContentPartType.CUSTOM,
                "name": A2UI_CUSTOM_EVENT_NAME,
                "value": a2ui,
            }],
            token_usage={},
        )


class StorageTranslator:
    """ag2store 边的薄驱动器：逐帧分派到模块级 fold 函数，累积成可落库的行。

    与 ``AgUiTranslator`` 同源数据（原生 messages 投影 + ToolsTransformer 的
    tools 投影），但方向不同：累积成离散的、可落库的完整消息，而非逐 delta
    的 ag-ui 事件。两者在一次 ``interleave`` 遍历里并存，共用消费方注入的
    message_id（每条 assistant 消息一次），保证流式与落库可对账。纯转换内核
    在模块级 ``fold_messages_frame`` / ``fold_tools_frame``，累积状态显式为
    ``StorageState``；本类只持上下文与状态，无隐藏状态。

    数据来源：messages 投影的 ``ChatModelStream`` 在 ``message-finish`` 后所有
    字段（``str(stream.reasoning)`` / ``str(stream.text)`` / ``stream.tool_calls.get()``
    / ``stream.output_message``）均非阻塞可读；tools 投影给出 tool 的执行结果。
    """

    def __init__(self, thread_id: UUID, turn_id: UUID, usage: UsageContext | None = None):
        self._ctx = StorageContext(thread_id=thread_id, turn_id=turn_id, usage=usage)
        self._state = StorageState()

    @property
    def messages(self) -> List[AgenticConversationMessage]:
        """累积出的完整消息列表（调用方在流结束后读取并落库）。"""
        return self._state.messages

    @property
    def usage_records(self) -> List[UsageRecord]:
        """chat 场景用量流水（每次模型调用一行；调用方在流收尾统一 flush）。"""
        return self._state.usage_records

    def translate(self, message_id: UUID, name: str, item: Any) -> None:
        """消费 interleave 产出的 (name, item)，折叠进显式状态。

        ``message_id`` 由消费方（service 遍历循环）按「每条 assistant 消息一次」
        生成并注入，与 AgUiTranslator 的流式事件共用同一 id：text 行优先持有，
        无 text 时由 thought 行兜底；tools 事件收到的 id 被忽略。
        """
        if name == "messages":
            fold_messages_frame(self._state, self._ctx, message_id, item)
        elif name == "tools":
            fold_tools_frame(self._state, self._ctx, item)
