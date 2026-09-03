"""轮次分支解析：重试/编辑的分支定位、兄弟序号与活跃路径计算（纯函数，无 I/O）。

模型语义（对齐 ChatGPT 节点树 / LibreChat parentMessageId / assistant-ui 分支）：
  - ``AgenticConversationTurn.parent_turn_id`` 为分支基点，兄弟语义——与其共享
    同一 parent 的轮次互为同一问答的重试/编辑变体；
  - ``AgenticConversation.current_turn_id`` 是活跃叶子（仅 COMPLETED 轮次推进），
    沿 parent 链回溯即活跃路径，活跃路径天然不含被重试替换的旧答案。

本模块只做计算：仓储负责按 thread 取回 ``TurnBranchSnapshot`` 骨架，编排层负责
把显式信号（forwardedProps.branch）/ 自动检测的结果落成建轮次参数。
"""

from dataclasses import dataclass
from uuid import UUID

from app.models.domain.agentic import AgenticConversationTurn, AgenticTurnStatus


@dataclass
class TurnBranchSnapshot:
    """分支计算所需的轮次骨架（thread 内一次查询取全量，数量有界）。"""

    turn_id: UUID
    parent_turn_id: UUID | None
    attempt_no: int
    status: AgenticTurnStatus
    # 轮内 sequence_num=0 的用户消息（存储形态 content 数组）；无用户行的轮次为 None
    user_content: list | None


def snapshot_of(turn: AgenticConversationTurn, user_content: list | None) -> TurnBranchSnapshot:
    """从轮次 ORM 行构造骨架（user_content 由调用方随查随传）。"""
    return TurnBranchSnapshot(
        turn_id=turn.turn_id,
        parent_turn_id=turn.parent_turn_id,
        attempt_no=turn.attempt_no,
        status=AgenticTurnStatus(turn.status),
        user_content=user_content,
    )


def ancestor_chain(turn_id: UUID | None, by_id: dict[UUID, TurnBranchSnapshot]) -> list[TurnBranchSnapshot]:
    """沿 parent 链从指定轮次回溯到根，返回旧→新的链上骨架（含自身）。

    悬挂引用（parent 指向不存在的轮次）按根截断，不抛错——分支字段是
    逐步迁移进来的，存量数据 parent 全 NULL，链条长度即轮次数，有界。
    """
    chain: list[TurnBranchSnapshot] = []
    seen: set[UUID] = set()
    current = by_id.get(turn_id) if turn_id is not None else None
    while current is not None and current.turn_id not in seen:
        seen.add(current.turn_id)
        chain.append(current)
        current = by_id.get(current.parent_turn_id) if current.parent_turn_id is not None else None
    chain.reverse()  # 根 → 叶
    return chain


def next_attempt_no(parent_turn_id: UUID | None, snapshots: list[TurnBranchSnapshot]) -> int:
    """兄弟轮次（同一 parent）间的下一个尝试序号：max(attempt_no) + 1，无兄弟为 1。"""
    siblings = [s for s in snapshots if s.parent_turn_id == parent_turn_id]
    return max((s.attempt_no for s in siblings), default=0) + 1


def detect_retry_of_latest(
    payload: list[tuple[str, list[dict]]],
    active_chain: list[TurnBranchSnapshot],
) -> UUID | None:
    """自动检测"重试最新一轮"：payload 用户序列与活跃路径逐位内容等值时命中。

    ``payload`` 为本次 run 请求的 (role, 存储形态 content) 投影。客户端"重新生成"
    会携带截断到目标问题的本地消息列表（user 开头 user 结尾，assistant 居中），
    且该问题就是活跃路径最后一个已回答的问答——因此：

      1. payload 长度为奇数（2N-1），角色 user/assistant 严格交替；
      2. 奇数位（user）恰有 N 个，与活跃路径 N 个轮次一一对应；
      3. 每个 user 的存储形态 content 与对应轮次的用户行完全等值
         （两侧同经 RunMessage.storage_content 归一，纯文本会话逐字节一致；
         多模态消息经前端 round-trip 可能变形——等值失败即放弃检测，退化为
         普通续聊，不劣于无分支模型时的行为）。

    命中返回活跃叶子（被重试轮次）的 turn_id，否则 None。位置对齐保证"稍后
    重新问一个旧问题"（payload 与活跃路径错位）不会误判。残余歧义：紧邻两次
    发送完全相同的问题文本——语义上视为重试（旧答案被替换），前端接上显式
    branch 信号后即消除。
    """
    if not payload or not active_chain:
        return None
    if len(payload) % 2 == 0:
        return None
    for index, (role, _content) in enumerate(payload):
        expected = "user" if index % 2 == 0 else "assistant"
        if role.strip().lower() != expected:
            return None
    users = [content for _role, content in payload[0::2]]
    if len(users) != len(active_chain):
        return None
    for content, snapshot in zip(users, active_chain):
        if content != snapshot.user_content:
            return None
    return active_chain[-1].turn_id
