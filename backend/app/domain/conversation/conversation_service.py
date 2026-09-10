from dataclasses import dataclass
from time import monotonic
from uuid import UUID, uuid4
from typing import List, Tuple

from wireup import injectable
from langchain.messages import AIMessage, ToolMessage
from langchain_core.messages import BaseMessage

from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.exceptions import ConversationNotFoundError, DuplicateRunError, TurnNotAtTipError
from app.packages.signal.signal_store import SignalStore
from app.domain.conversation.ports import ConversationRepositoryPort
from app.domain.ports import RepositoryConflictError
from app.domain.conversation.branching import (
    ancestor_chain,
    detect_retry_of_latest,
    next_attempt_no,
    snapshot_of,
)
from app.domain.conversation.multimodal import image_parts_of, text_of, user_message_from_content
from app.domain.conversation.signals import CANCEL_FLAG_TTL_SECONDS, cancel_flag_key

from app.models.domain.agentic import (
    AgenticConversation,
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticMessageRole,
    AgenticMessageType,
    AgenticTurnStatus,
    ContentPartType,
)

# 计入轮次聚合的标准用量键（其余如 cached_tokens 不计）。
# input/output_tokens 是存量消息行的原生键族（归一化改造前的历史数据），保留
# 以兼容；新写入行在 StorageTranslator.read_usage 已统一为 prompt/completion 族
USAGE_KEYS = (
    "prompt_tokens", "completion_tokens", "total_tokens",
    "input_tokens", "output_tokens",
)

# 取消标志读失败警告的节流窗口（存储持续不可用时避免每 0.5s 刷一条日志）
_WARN_THROTTLE_SECONDS = 30.0


@injectable
@dataclass
class ConversationService:
    """会话聚合领域服务：持久化与业务规则的唯一门面。

    消费方按受众分流：run 用户侧行程经编排层（AgenticService /
    TurnFinalizer）调用本服务；管理侧（会话增删查）与响应信封组装直接
    在此完成。存在性校验在服务内抛 ConversationNotFoundError——对齐
    知识域惯例，端点不再做 None 判断，HTTP 与后台拿到一致错误语义。
    """

    conversation_repo: ConversationRepositoryPort
    app_config: AppConfig
    signal_store: SignalStore
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)
        self._last_warn_at = -_WARN_THROTTLE_SECONDS

    # ---- run 行程所需写路径（编排层准备段 / 收尾段下沉）----

    def open_turn(
        self,
        user_id: UUID,
        thread_id: UUID,
        run_id: str,
        content: List[dict],
        agent_id: str | None = None,
        payload: List[tuple[str, List[dict]]] | None = None,
        branch: dict | None = None,
    ) -> Tuple[AgenticConversation, AgenticConversationTurn]:
        """get-or-create 会话（默认智能体兜底）+ 创建轮次（含分支定位）+ 落库本轮用户消息。

        全部写收口在仓储的单事务（``open_turn``）：会话/绑定/轮次/用户消息
        要么同时落库要么同时不可见——中途失败不留「有轮次无用户消息」的半截
        状态。事务只保写集原子性，不提供隔离：上面的归属判定与分支定位读都
        发生在事务外（各自独立读、无锁），同一会话并发开轮的仲裁不在保证
        范围内（并发边界详见仓储 ``open_turn`` 注释）。归属规则：会话已存在
        且属于他人时按不存在处理（404，不泄露
        存在性），不能变成新建——客户端 threadId 自行生成，必须防止借他人
        thread_id 续聊或注入消息。
        ``content`` 为存储形态的内容数组（端点已从 ag-ui 载荷归一，含
        text/image part；见 multimodal.py），原样落库。
        ``agent_id``：前端显式指定的智能体（编排层已校验注册）——新建会话
        用它绑定，已有会话显式不同则切换绑定（本轮即生效）；缺省沿用
        会话现有绑定/全局默认，不做任何改写。
        ``payload``：本次 run 请求的消息投影 (role, 存储形态 content)，供
        "重试最新一轮"自动检测；缺省跳过检测。
        ``branch``：forwardedProps.branch 显式分支信号（baseMessageId 等），
        优先于自动检测。
        """
        # 防御性清理遗留取消标志（上轮取消后 TTL 内残留会误杀本轮；正常收尾
        # 后标志本就应不存在，此处是无条件兜底）。清理失败不阻断开轮。
        try:
            self.signal_store.reset(cancel_flag_key(thread_id))
        except Exception as exc:
            self.logger.warning("取消标志清理失败：%s", exc)

        # 归属前置判定（纯读）：他人会话统一 404，先于任何写
        conversation = self.conversation_repo.find_conversation(thread_id)
        if conversation is not None and conversation.user_id != user_id:
            raise ConversationNotFoundError("conversation not found")

        if conversation is None:
            # 新会话：无历史可分支，parent=None / attempt=1（分支定位的平凡情形）
            parent_turn_id: UUID | None = None
            attempt_no = 1
            rebind = False
        else:
            turns = self.conversation_repo.list_thread_turns(thread_id)
            user_contents = self.conversation_repo.map_turn_user_contents(thread_id) if turns else {}
            snapshots = [snapshot_of(t, user_contents.get(t.turn_id)) for t in turns]
            by_id = {s.turn_id: s for s in snapshots}

            # 分支定位：重试/编辑 → 基点的 parent 为分支点，兄弟间 attempt+1；
            # 普通提问 → parent = 活跃叶子（线性续聊），attempt = 1
            base = self._resolve_branch_base(conversation, payload, branch, by_id)
            if base is not None:
                # 末梢守卫：节点沿任一变体续聊后即定型，历史节点不再开分支
                self._ensure_base_at_tip(conversation, base, by_id)
                parent_turn_id = base.parent_turn_id
                attempt_no = next_attempt_no(parent_turn_id, snapshots)
            else:
                parent_turn_id = conversation.current_turn_id
                attempt_no = 1
            rebind = agent_id is not None and conversation.agentic_id != agent_id

        # run_id 幂等的并发窗口兜底：uq_turn_thread_run 唯一约束冲突（同一 run
        # 的重复提交，如传输层重试/双击重放）按业务错误拒绝，不产生重复轮次
        try:
            return self.conversation_repo.open_turn(
                user_id=user_id,
                thread_id=thread_id,
                agentic_id=agent_id or self.app_config.default_agentic_id,
                rebind=rebind,
                run_id=run_id,
                turn_id=uuid4(),
                parent_turn_id=parent_turn_id,
                attempt_no=attempt_no,
                content=content,
            )
        except RepositoryConflictError:
            raise DuplicateRunError("请勿重复提交：相同请求已在本会话中提交过") from None

    def _ensure_base_at_tip(self, conversation: AgenticConversation, base, by_id: dict) -> None:
        """末梢守卫：分支只允许发生在最新问答（"主干 + 末梢一层扇形"）。

        判定"已定型槽位"：活跃路径（叶子自身除外）上任一轮次所在的位置。
        base 落在已定型槽位——无论是历史轮次本身，还是其槽位在路径后方——
        都说明该问答之后已有续聊，再开分支违反约束，抛业务错误。放行：
        base == 叶子（末梢重试/编辑）、base 为叶子的兄弟变体（扇内定位）、
        base 为叶子的失败子轮（重试最新失败提问）、无活跃叶子。
        """
        leaf = by_id.get(conversation.current_turn_id) if conversation.current_turn_id is not None else None
        if leaf is None or base.turn_id == leaf.turn_id:
            return
        chain = ancestor_chain(leaf.turn_id, by_id)
        settled_slot_parents = {s.parent_turn_id for s in chain[:-1]}
        if base.parent_turn_id in settled_slot_parents:
            raise TurnNotAtTipError("仅最新问答支持重新生成/编辑")

    def _resolve_branch_base(
        self,
        conversation: AgenticConversation,
        payload: List[tuple[str, List[dict]]] | None,
        branch: dict | None,
        by_id: dict,
    ):
        """两级定位重试/编辑的基点轮次（兄弟语义的参照），未命中返回 None。

        1. 显式信号 ``branch.baseMessageId``：目标问题前一条 assistant 消息的
           id——assistant 行的 id 与流式/落库共用，live 与历史重载两种客户端
           形态下都能精确命中；跨会话/非法 id 一律降级，不抛错。
        2. 自动检测"重试最新一轮"：payload 用户序列与活跃路径逐位内容等值
           （branching.detect_retry_of_latest，前端零改动即可命中）。
        """
        if isinstance(branch, dict):
            raw = branch.get("baseMessageId")
            if isinstance(raw, str) and raw:
                try:
                    message_uuid = UUID(raw)
                except ValueError:
                    message_uuid = None
                if message_uuid is not None:
                    turn_id = self.conversation_repo.find_turn_id_by_message_id(conversation.thread_id, message_uuid)
                    if turn_id is not None:
                        return by_id.get(turn_id)
        if payload and conversation.current_turn_id is not None:
            active_chain = ancestor_chain(conversation.current_turn_id, by_id)
            matched = detect_retry_of_latest(payload, active_chain)
            if matched is not None:
                return by_id.get(matched)
        return None

    def replay_history(
        self,
        thread_id: UUID,
        base_turn_id: UUID,
        supports_vision: bool = False,
        image_resolver=None,
    ) -> List[BaseMessage]:
        """从库如实组装多轮回放消息：活跃路径（当前轮次的 parent 链）∩ COMPLETED。

        沿当前轮次回溯 parent 链得活跃路径——普通提问即线性历史；重试/编辑
        时被替换的旧答案是基点的兄弟（不在链上），自然不进上下文，同轮次
        自身 RUNNING 也会被 COMPLETED 过滤排除。

        按厂商建议携带完整工具调用链：TOOL_CALL → AIMessage(tool_calls)、
        TOOL_RESULT → ToolMessage，两者成对回放——OpenAI 兼容 API 要求
        tool_calls 消息后必须紧跟对应 tool 消息，故未配对的调用/结果行跳过
        （工具出错时不落 TOOL_RESULT 行，孤儿调用直接回放会 400）。
        THOUGHT（reasoning）不回放：厂商 API 不接受历史 reasoning_content 注入。
        CUSTOM（A2UI 卡片载荷）不回放：纯前端表现层，天气数据已随
        TOOL_RESULT 行回放，UI 消息数组回灌只会污染上下文。

        用户消息经 ``user_message_from_content`` 还原多模态内容：
        ``supports_vision`` 为假或附件读取失败时图片降级丢弃（历史回放静默
        不注明，保持 prompt 稳定）；``image_resolver`` 由编排层用附件
        store + 消息属主身份构造（本域 url 引用 → 字节 → base64 块）。
        """
        turns = self.conversation_repo.list_thread_turns(thread_id)
        by_id = {t.turn_id: snapshot_of(t, None) for t in turns}
        active_turn_ids = [s.turn_id for s in ancestor_chain(base_turn_id, by_id)]
        rows = self.conversation_repo.list_replay_messages(thread_id=thread_id, active_turn_ids=active_turn_ids)

        history: List[BaseMessage] = []
        pending_calls: dict[str, AIMessage] = {}
        for row in rows:
            # THOUGHT（reasoning）行不回放（见上）：思考文本以普通 assistant
            # content 回灌会污染上下文
            if row.message_type == AgenticMessageType.THOUGHT:
                continue

            part = row.content[0] if row.content else {}
            part_type = part.get("type")

            # 用户消息（MESSAGE）整行走多模态翻译——content 数组可能以图片
            # 开头，不能按 content[0].type 分派；纯空内容跳过（对齐旧行为）
            if row.role == AgenticMessageRole.USER and row.message_type == AgenticMessageType.MESSAGE:
                if not text_of(row.content) and not image_parts_of(row.content):
                    continue
                history.append(user_message_from_content(
                    row.content, supports_vision=supports_vision,
                    image_resolver=image_resolver, note_on_degrade=False,
                ))
                continue

            if part_type == ContentPartType.TEXT:
                text = part.get("text", "")
                if not text:
                    continue
                history.append(AIMessage(content=text))
            elif part_type == ContentPartType.TOOL_CALL:
                tool_call_id = part.get("tool_call_id", "")
                if not tool_call_id:
                    continue
                # 先缓冲，待配对的 TOOL_RESULT 到达时成对落列（保持与前后消息的相对顺序）
                pending_calls[tool_call_id] = AIMessage(content="", tool_calls=[{
                    "name": part.get("name", ""),
                    "args": part.get("args", {}),
                    "id": tool_call_id,
                    "type": "tool_call",
                }])
            elif part_type == ContentPartType.TOOL_RESULT:
                tool_call_id = part.get("tool_call_id", "")
                call = pending_calls.pop(tool_call_id, None)
                if call is None:
                    continue
                history.append(call)
                history.append(ToolMessage(content=part.get("content", ""), tool_call_id=tool_call_id))
            elif part_type == ContentPartType.CUSTOM:
                # A2UI 卡片载荷（message_type=CUSTOM）：UI 表现层，不回灌模型上下文
                continue

        return history

    def store_assistant_messages(self, messages: List[AgenticConversationMessage]) -> None:
        """流结束后批量落库 assistant 消息。"""
        if len(messages) > 0:
            self.conversation_repo.store_conversation_messages(messages)

    def complete_turn(self, turn: AgenticConversationTurn) -> None:
        """完成收口：置 COMPLETED 并把会话活跃叶子推进到本轮次（仓储单事务）。"""
        self.conversation_repo.complete_turn(turn)

    def fail_turn(self, turn: AgenticConversationTurn) -> None:
        turn.status = AgenticTurnStatus.FAILED
        self.save_turn(turn)

    def cancel_turn(self, turn: AgenticConversationTurn) -> None:
        turn.status = AgenticTurnStatus.CANCELED
        self.save_turn(turn)

    # ---- 显式取消通道（REST cancel 接口写、流式循环/工具入口读）----

    def cancel_run_flag(self, thread_id: UUID) -> None:
        """置 thread 作用域取消标志（存储失败异常上抛，由端点反馈调用方）。"""
        self.signal_store.fire(cancel_flag_key(thread_id), ttl_seconds=CANCEL_FLAG_TTL_SECONDS)

    def is_run_canceled(self, thread_id: UUID) -> bool:
        """读取消标志。宽容策略归领域：存储不可用绝不阻断聊天主链路——
        警告（节流）+ 视为未取消，降级为仅断链取消。"""
        try:
            return self.signal_store.is_fired(cancel_flag_key(thread_id))
        except Exception as exc:
            now = monotonic()
            if now - self._last_warn_at >= _WARN_THROTTLE_SECONDS:
                self._last_warn_at = now
                self.logger.warning("取消标志读取失败（视为未取消）：%s", exc)
            return False

    def save_turn(self, turn: AgenticConversationTurn) -> None:
        self.conversation_repo.store_conversation_turn(turn)

    def record_title(self, conversation: AgenticConversation, title: str) -> None:
        """标题生成成功后的回写（收尾链路专用）。"""
        conversation.conversation_title = title
        self.conversation_repo.store_conversation(conversation)

    def record_turn_usage(self, turn: AgenticConversationTurn, turn_messages: List[AgenticConversationMessage]) -> None:
        """聚合本轮各条消息的用量并落库（只收标准键，见 USAGE_KEYS）。"""
        usage = dict(turn.token_usage)
        for msg in turn_messages:
            for key, value in msg.token_usage.items():
                if key in USAGE_KEYS:
                    usage[key] = usage.get(key, 0) + value
        turn.token_usage = usage
        self.save_turn(turn)

    # ---- 管理侧读路径（/agentic/conversation* 端点的直接消费面）----

    def list_conversations(self, user_id: UUID, page: int, page_size: int):
        conversations, total = self.conversation_repo.list_conversations(user_id=user_id, page=page, page_size=page_size)
        return {"items": conversations, "total": total, "page": page, "pageSize": page_size}

    def describe_conversation(self, user_id: UUID, thread_id: UUID) -> AgenticConversation:
        conversation = self.conversation_repo.get_conversation(thread_id=thread_id, user_id=user_id)
        if conversation is None:
            raise ConversationNotFoundError("conversation not found")
        return conversation

    def delete_conversation(self, user_id: UUID, thread_id: UUID) -> AgenticConversation:
        # 硬删除：会话+轮次+消息单事务清空，返回删除前快照。
        # 长期记忆（components/memory 双层图谱模型）是跨会话的用户级数据，不随会话删除。
        conversation = self.conversation_repo.delete_conversation(thread_id=thread_id, user_id=user_id)
        if conversation is None:
            raise ConversationNotFoundError("conversation not found")
        return conversation

    def activate_turn(self, user_id: UUID, thread_id: UUID, turn_id: UUID) -> AgenticConversation:
        """切换活跃叶子到指定轮次（末梢扇形内的变体切换）。

        末梢规则：目标必须已完成，且是当前叶子本身或其兄弟——沿任一变体
        续聊后节点即定型，历史节点不支持切换。切换只移动活跃叶子、不产生
        新轮次；后续 run 的 parent 与 replay 均从新叶子派生（所见即上下文）。
        """
        conversation = self.describe_conversation(user_id=user_id, thread_id=thread_id)
        turns = self.conversation_repo.list_thread_turns(thread_id)
        by_id = {t.turn_id: snapshot_of(t, None) for t in turns}
        target = by_id.get(turn_id)
        if target is None:
            raise ConversationNotFoundError("conversation not found")
        if target.status != AgenticTurnStatus.COMPLETED:
            raise TurnNotAtTipError("仅已完成的问答可切换分支")
        leaf = by_id.get(conversation.current_turn_id) if conversation.current_turn_id is not None else None
        if leaf is None:
            raise TurnNotAtTipError("当前没有可切换的分支")
        if target.turn_id != leaf.turn_id and target.parent_turn_id != leaf.parent_turn_id:
            raise TurnNotAtTipError("仅最新问答可切换分支")
        if target.turn_id == conversation.current_turn_id:
            return conversation  # 幂等：叶子已在目标上
        return self.conversation_repo.activate_turn(thread_id=thread_id, turn_id=turn_id)

    def list_history_messages(self, user_id: UUID, thread_id: UUID, offset: int | None = None, limit: int = 20):
        """UI 历史回放集合：活跃路径 + 末梢扇形，且仅限 COMPLETED 轮次。

        末梢扇形 = 活跃叶子的兄弟变体（同一问答的其他尝试）——刷新后仍可
        对比与切换（激活接口只允许在末梢移动叶子）。被续聊定型的历史槽位
        不返回。作废轮次（失败/取消/悬挂 RUNNING）**不是活跃节点**，一律
        不下发——前端历史是线性回放，混入作废轮次会让主干出现"无回答的
        悬空提问"（断流/主动取消后尤甚）；轮次行本身保留在库中供审计，
        最新问答的重试对比由会话内实时分支承担。响应携带
        ``active_turn_id``（活跃叶子）。offset/limit 沿用"取最新一页"语义，
        items 旧→新排序。

        items 是轮次的**前端展示视图**（序列化后的 dict）：TOOL_RESULT 行带
        ``display_content``（工具结果双内容契约的展示版，见 StorageTranslator）
        时 content 被替换为展示版、display_content 键不外泄——真实结果只
        入库审计，不经本接口外发；无 display_content 的行（其他 agent /
        存量数据）原样。``replay_history`` 的 LLM 回放不受影响，仍用真实
        content（与模型当时所见一致）。
        """
        conversation = self.describe_conversation(user_id=user_id, thread_id=thread_id)
        turns = self.conversation_repo.list_thread_turns(thread_id)
        by_id = {t.turn_id: snapshot_of(t, None) for t in turns}
        leaf = by_id.get(conversation.current_turn_id) if conversation.current_turn_id is not None else None
        active_ids = {s.turn_id for s in ancestor_chain(leaf.turn_id, by_id)} if leaf is not None else set()
        # 末梢槽位取"最深"：叶子有子轮（进行中/失败的下一问）时，末梢是其子槽位，
        # 叶子自身的变体扇形已被定型收回；否则末梢即叶子所在的变体扇形
        leaf_has_children = any(t.parent_turn_id == leaf.turn_id for t in turns) if leaf is not None else False
        tip_slot_parent = leaf.turn_id if leaf is not None and leaf_has_children else leaf.parent_turn_id if leaf is not None else None
        fan_ids = {
            t.turn_id
            for t in turns
            if leaf is not None and t.parent_turn_id == tip_slot_parent and t.turn_id != leaf.turn_id
        } if leaf is not None else set()
        # 活跃路径全由 COMPLETED 组成（指针仅完成时推进）；扇形变体同样只保留
        # 已完成的——作废轮次（失败/取消/悬挂）在下发面整体剔除
        visible = [
            t for t in turns
            if AgenticTurnStatus(t.status) == AgenticTurnStatus.COMPLETED
            and (t.turn_id in active_ids or t.turn_id in fan_ids)
        ]
        total = len(visible)
        start = offset or 0
        # desc 开窗再反转：与旧"turn_num desc 分页取最新一页"的契约一致
        newest_first = list(reversed(visible))
        page = newest_first[start:start + limit]
        return {
            "items": [self._frontend_turn(t) for t in reversed(page)],
            "total": total,
            "offset": start,
            "limit": limit,
            "active_turn_id": conversation.current_turn_id,
        }

    @staticmethod
    def _frontend_turn(turn: AgenticConversationTurn) -> dict:
        """轮次的 UI 展示视图：预序列化 + TOOL_RESULT 行换成展示版内容。

        返回 model_dump 的 dict（端点信封对其再编码，最终 JSON 形态不变），
        替换只发生在新造的 dict 上，仓储返回的领域对象不被改动。
        """
        data = turn.model_dump()
        for message in data["messages"]:
            content = message.get("content") or []
            part = content[0] if content else None
            if (
                message.get("message_type") == AgenticMessageType.TOOL_RESULT
                and isinstance(part, dict)
                and "display_content" in part
            ):
                message["content"] = [{
                    "type": "tool_result",
                    "tool_call_id": part.get("tool_call_id", ""),
                    "content": part["display_content"],
                }]
        return data
