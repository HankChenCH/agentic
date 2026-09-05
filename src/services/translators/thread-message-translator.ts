import type {
  ReasoningMessagePart,
  TextMessagePart,
  ThreadMessageLike,
  ToolCallMessagePart,
} from "@assistant-ui/core";

import { REST_BASE } from "@/lib/config";
import type {
  BackendConversationTurn,
  BackendMessage,
  BackendMessageContent,
} from "@/services/types";
/**
 * 后端历史（turn 两层结构）→ assistant-ui ThreadMessageLike[]（一维）的翻译器。
 *
 * 与 server 的 StorageTranslator 对称：后端把一条 assistant ChatModelStream
 * 拆成 [THOUGHT, MESSAGE, TOOL_CALL, TOOL_RESULT] 多条 AgenticConversationMessage；
 * 这里把它们重新组装回 assistant-ui 的 parts 模型。
 *
 * 合并粒度对齐实时路径：RunAggregator 在一个 run 内只维护一条 assistant
 * 消息的 parts，而落库时每次 LLM 调用（ChatModelStream）是独立的
 * parent 链根 —— 所以不能按 parent 根分组（那会把一个多步 turn 拆成
 * N 条独立 assistant 消息），必须整 turn 合并。
 *
 * 规则：
 *   - user MESSAGE          → 1 条 user ThreadMessage（content: [text]）
 *   - 一个 turn 内 user 行之后的所有 [THOUGHT, MESSAGE, TOOL_CALL...] 按序
 *     合并成 1 条 assistant ThreadMessage（parts 按行序排列，多步 LLM
 *     调用会产生多段 reasoning/text/tool-call part）
 *   - TOOL_RESULT           → 不单独成条，并回同 tool_call_id 的 tool-call part
 *     的 result 字段
 *   - 轮次状态随消息下发：FAILED/CANCELED（及悬挂 RUNNING）轮次的 assistant
 *     消息标注 incomplete（error/cancelled）；无任何 assistant 内容时合成
 *     失败/已停止占位，避免悬空提问
 *
 * 注意：assistant-ui 没有 turn 概念，必须把 turn 拍扁成一维消息流。
 * 本模块是纯数据转换，不依赖 React。
 */

function toUserThreadMessage(m: BackendMessage): ThreadMessageLike {
  // 多模态：text parts 聚合为文本内容；image parts（稳定附件引用或 base64
  // 内联）还原为附件卡片展示。引用 url 直接指向后端 302 路由（渲染时由
  // 后端换发预签名地址，浏览器直拉对象存储），无需鉴权头也无需 blob 中转。
  const text = m.content
    .filter((c): c is Extract<BackendMessageContent, { type: "text" }> => c.type === "text")
    .map((c) => c.text)
    .filter(Boolean)
    .join("\n");
  const attachments = m.content
    .filter((c): c is Extract<BackendMessageContent, { type: "image" }> => c.type === "image")
    .map((part, index) => {
      const mime = part.source.mimeType ?? "image/png";
      // url source 两种形态并存：当前发送链路存「API 根 + 相对路径」的绝对
      // URL（浏览器 <img> 渲染需要），直接用；裸相对引用拼 REST_BASE。
      const image =
        part.source.type === "data"
          ? `data:${mime};base64,${part.source.value}`
          : /^https?:\/\//i.test(part.source.value)
            ? part.source.value
            : `${REST_BASE}${part.source.value}`;
      return {
        id: `${m.message_id}-${index}`,
        type: "image" as const,
        name: "image",
        contentType: mime,
        status: { type: "complete" as const },
        content: [{ type: "image" as const, image }],
      };
    });
  return {
    id: m.message_id,
    role: "user",
    content: [{ type: "text", text }],
    ...(attachments.length > 0 ? { attachments } : {}),
    createdAt: new Date(m.created_at),
  };
}

/**
 * 轮次状态 → assistant 消息状态：非 COMPLETED 轮次如实标注为 incomplete
 * （FAILED/悬挂 RUNNING → error，CANCELED → cancelled），不再伪装成正常完成。
 * 返回 undefined 视为完成（complete）。
 */
function turnAssistantStatus(
  status: BackendConversationTurn["status"],
): NonNullable<ThreadMessageLike["status"]> {
  switch (status) {
    case "failed":
    case "running": // 悬挂 RUNNING（进程崩溃等未收口轮次）按失败口径呈现
      return { type: "incomplete", reason: "error" };
    case "canceled":
      return { type: "incomplete", reason: "cancelled" };
    default:
      return { type: "complete", reason: "stop" };
  }
}

/** 失败/取消轮次无任何可渲染 assistant 内容时的占位消息（消除悬空提问）。 */
function placeholderAssistantMessage(
  turn: BackendConversationTurn,
  status: NonNullable<ThreadMessageLike["status"]> & { type: "incomplete" },
): ThreadMessageLike {
  const text = status.reason === "cancelled" ? "（已停止生成）" : "（生成失败）";
  return {
    id: `${turn.turn_id}-placeholder`,
    role: "assistant",
    content: [{ type: "text", text }],
    status,
    createdAt: new Date(turn.created_at),
  };
}

function toAssistantThreadMessage(
  group: BackendMessage[],
  toolResults: Map<string, unknown>,
  status: ThreadMessageLike["status"],
): ThreadMessageLike | null {
  type AssistantPart =
    | TextMessagePart
    | ReasoningMessagePart
    | ToolCallMessagePart;
  const parts: AssistantPart[] = [];

  // thought → message → tool_call，按 group 内出现顺序（后端按序 append）
  let lastId = "";
  for (const m of group) {
    lastId = m.message_id;
    if (m.message_type === "thought") {
      const text = m.content.find((c) => c.type === "text")?.text ?? "";
      parts.push({ type: "reasoning", text });
    } else if (m.message_type === "message") {
      const text = m.content.find((c) => c.type === "text")?.text ?? "";
      parts.push({ type: "text", text });
    } else if (m.message_type === "tool_call") {
      const c = m.content.find((c) => c.type === "tool_call");
      if (!c || c.type !== "tool_call") continue;
      // 后端 args 是 Record<string, unknown>，assistant-ui 要求 ReadonlyJSONObject。
      const args = (c.args ?? {}) as ToolCallMessagePart["args"];
      parts.push({
        type: "tool-call",
        toolCallId: c.tool_call_id,
        toolName: c.name,
        args,
        argsText: JSON.stringify(args),
        result: toolResults.get(c.tool_call_id),
      });
    }
  }

  // 失败 turn 可能只剩 error 行，没有可渲染的 part —— 不产出空消息
  if (parts.length === 0) return null;

  return {
    id: lastId || group[0].message_id,
    role: "assistant",
    content: parts,
    status,
    createdAt: new Date(group[0].created_at),
  };
}

/**
 * 收集「更新过的轮次」在 UI 消息流中的消息 id 集合。
 *
 * attempt_no > 1 = 该问答存在更早的尝试（被重新生成/编辑过），当前展示的是
 * 新变体 —— UserMessage 组件据此渲染「已更新」标记。id 取值规则与
 * toThreadMessages 一致：user 行取自身 message_id，assistant 组取末行
 * message_id，刷新后与 ThreadMessage id 精确对上。
 */
export function collectUpdatedMessageIds(
  turns: BackendConversationTurn[],
): Set<string> {
  const ids = new Set<string>();
  for (const turn of turns) {
    if ((turn.attempt_no ?? 1) <= 1) continue;
    let lastAssistantId = "";
    for (const m of [...turn.messages].sort(
      (a, b) => a.sequence_num - b.sequence_num,
    )) {
      if (m.message_type === "tool_result") continue;
      if (m.role === "user") ids.add(m.message_id);
      else lastAssistantId = m.message_id;
    }
    if (lastAssistantId) ids.add(lastAssistantId);
  }
  return ids;
}

/**
 * 收集「消息 id → 轮次 id」映射：分支切换的 activate 需要把 UI 消息流里的
 * 消息映射回所属轮次。消息 id（user 行与 assistant 行的 message_id）与
 * ThreadMessage id 同源，任意一行都能定位到轮次。
 */
export function collectTurnIdByMessageId(
  turns: BackendConversationTurn[],
): Map<string, string> {
  const map = new Map<string, string>();
  for (const turn of turns) {
    for (const m of turn.messages) {
      map.set(m.message_id, turn.turn_id);
    }
  }
  return map;
}

/**
 * 末梢扇形树构建：活跃路径走 head，叶子的兄弟变体（同一问答的其他尝试）
 * 作为分支节点挂在同槽位上，供 aui.thread.import 种入运行时的消息仓库 ——
 * 刷新后 BranchPicker 依旧可对比/切换（切换经 activate-turn 同步服务端）。
 *
 * 节点模型：每个轮次展开为 user 节点 + assistant 节点；扇形内用户文本相同
 * 的变体（重新生成场景）合并为一个 user 节点、各自的 assistant 成兄弟
 * （"1/2" 出现在回答下方，对齐 ChatGPT），文本不同（编辑场景）各自成
 * user 兄弟。items 父先于子，可直接喂 ExportedMessageRepository。
 */
export interface ThreadBranchNode {
  parentId: string | null;
  message: ThreadMessageLike;
}

export interface ThreadBranchTree {
  /** 活跃路径的线性消息流（applyExternalMessages 契约） */
  headMessages: ThreadMessageLike[];
  /** 全量节点（父先于子），含扇形变体 */
  branchItems: ThreadBranchNode[];
  /** head 路径末尾（活跃叶子）的 assistant 消息 id */
  headId: string | null;
  /** 是否存在末梢扇形（branchItems 多于 headMessages） */
  hasFan: boolean;
}

function userTextOf(turn: BackendConversationTurn): string {
  const row = [...turn.messages]
    .sort((a, b) => a.sequence_num - b.sequence_num)
    .find((m) => m.role === "user");
  return (
    row?.content
      .filter((c): c is Extract<BackendMessageContent, { type: "text" }> => c.type === "text")
      .map((c) => c.text)
      .join("\n") ?? ""
  );
}

function buildTurnNodes(
  turn: BackendConversationTurn,
): { user: ThreadMessageLike; assistant: ThreadMessageLike | null } | null {
  const rows = [...turn.messages].sort((a, b) => a.sequence_num - b.sequence_num);
  const userRow = rows.find((m) => m.role === "user");
  const assistantRows = rows.filter(
    (m) => m.role !== "user" && m.message_type !== "tool_result",
  );
  const toolResults = new Map<string, unknown>();
  for (const m of rows) {
    if (m.message_type !== "tool_result") continue;
    const c = m.content.find((c) => c.type === "tool_result");
    if (c && c.type === "tool_result") toolResults.set(c.tool_call_id, c.content);
  }
  const status = turnAssistantStatus(turn.status);
  const assistant =
    toAssistantThreadMessage(assistantRows, toolResults, status) ??
    (status.type === "incomplete" ? placeholderAssistantMessage(turn, status) : null);
  if (!userRow) return assistant ? { user: { id: `turn-${turn.turn_id}`, role: "user", content: [] }, assistant } : null;
  return { user: toUserThreadMessage(userRow), assistant };
}

export function toThreadBranchTree(
  turns: BackendConversationTurn[],
  activeTurnId: string | null | undefined,
): ThreadBranchTree {
  const ordered = [...turns].sort((a, b) => a.turn_num - b.turn_num);
  const byId = new Map(ordered.map((t) => [t.turn_id, t]));

  // 活跃路径：从活跃叶子沿 parent 链回溯（仅限本载荷内的轮次）
  const chainIds: string[] = [];
  const seen = new Set<string>();
  let cursor = activeTurnId != null ? byId.get(activeTurnId) : undefined;
  while (cursor && !seen.has(cursor.turn_id)) {
    seen.add(cursor.turn_id);
    chainIds.push(cursor.turn_id);
    cursor = cursor.parent_turn_id != null ? byId.get(cursor.parent_turn_id) : undefined;
  }
  chainIds.reverse();
  // 载荷里找不到活跃叶子（旧数据/异常）：整体按旧→新线性退化
  const chain = chainIds.length > 0 ? chainIds : ordered.map((t) => t.turn_id);

  const nodes = new Map<string, { user: ThreadMessageLike; assistant: ThreadMessageLike | null }>();
  for (const t of ordered) {
    const built = buildTurnNodes(t);
    if (built) nodes.set(t.turn_id, built);
  }

  const branchItems: ThreadBranchNode[] = [];
  const headMessages: ThreadMessageLike[] = [];
  let prevAssistantId: string | null = null;
  for (const id of chain) {
    const n = nodes.get(id);
    if (!n) continue;
    headMessages.push(n.user);
    branchItems.push({ parentId: prevAssistantId, message: n.user });
    if (n.assistant) {
      headMessages.push(n.assistant);
      branchItems.push({ parentId: n.user.id ?? null, message: n.assistant });
      prevAssistantId = n.assistant.id ?? prevAssistantId;
    }
  }

  // 末梢扇形变体：非链上轮次按（锚点 assistant 节点, 用户文本）分组挂载
  const chainSet = new Set(chain);
  const fan = ordered.filter((t) => !chainSet.has(t.turn_id));
  const groups = new Map<string, { anchorId: string | null; turns: string[] }>();
  for (const t of fan) {
    const anchorTurn = t.parent_turn_id != null ? byId.get(t.parent_turn_id) : undefined;
    const anchor = anchorTurn ? nodes.get(anchorTurn.turn_id) : undefined;
    if (!anchor?.assistant) continue; // 锚点不在载荷/无回答 → 放弃该变体
    const key = `${anchor.assistant.id}|${userTextOf(t)}`;
    const group: { anchorId: string | null; turns: string[] } =
      groups.get(key) ?? { anchorId: anchor.assistant.id ?? null, turns: [] };
    group.turns.push(t.turn_id);
    groups.set(key, group);
  }
  for (const group of groups.values()) {
    const head = nodes.get(group.turns[0]);
    if (!head) continue;
    branchItems.push({ parentId: group.anchorId, message: head.user });
    for (const tid of group.turns) {
      const n = nodes.get(tid);
      if (n?.assistant) {
        branchItems.push({ parentId: n.user.id ?? null, message: n.assistant });
      }
    }
  }

  return {
    headMessages,
    branchItems,
    headId: prevAssistantId,
    hasFan: fan.length > 0,
  };
}

/**
 * 把后端 history（turn 数组）拍扁成 assistant-ui 的 ThreadMessageLike[]。
 * 返回顺序：旧 → 新（与渲染顺序一致）。
 */
export function toThreadMessages(
  turns: BackendConversationTurn[],
): ThreadMessageLike[] {
  const out: ThreadMessageLike[] = [];

  for (const turn of turns) {
    // 后端 relationship 未声明 order_by，显式按 sequence_num 排序保证行序稳定
    const rows = [...turn.messages].sort(
      (a, b) => a.sequence_num - b.sequence_num,
    );
    const assistantStatus = turnAssistantStatus(turn.status);

    // 1) 先收集 TOOL_RESULT：tool_call_id → result，待会并入 tool-call part
    const toolResults = new Map<string, unknown>();
    for (const m of rows) {
      if (m.message_type !== "tool_result") continue;
      const c = m.content.find((c) => c.type === "tool_result");
      if (c && c.type === "tool_result") {
        toolResults.set(c.tool_call_id, c.content);
      }
    }

    // 2) user 单独成条；一个 turn 内 user 行之后的 assistant 系行累积进
    //    同一组，遇到下一个 user 行或 turn 结束时 flush 成一条消息
    let assistantRows: BackendMessage[] = [];
    let assistantEmitted = false;
    const flushAssistant = () => {
      if (assistantRows.length === 0) return;
      const message = toAssistantThreadMessage(assistantRows, toolResults, assistantStatus);
      if (message) {
        out.push(message);
        assistantEmitted = true;
      }
      assistantRows = [];
    };

    for (const m of rows) {
      if (m.message_type === "tool_result") continue; // 已并入 tool-call
      if (m.role === "user") {
        flushAssistant();
        out.push(toUserThreadMessage(m));
        continue;
      }
      assistantRows.push(m);
    }
    flushAssistant();

    // 失败/取消轮次若没有任何 assistant 内容（如服务端异常时只落了用户行），
    // 合成占位消息如实标注，避免历史里出现没有回答的悬空提问
    if (!assistantEmitted && assistantStatus.type === "incomplete") {
      out.push(placeholderAssistantMessage(turn, assistantStatus));
    }
  }

  return out;
}
