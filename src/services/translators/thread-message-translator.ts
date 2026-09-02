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
      const image =
        part.source.type === "data"
          ? `data:${mime};base64,${part.source.value}`
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

function toAssistantThreadMessage(
  group: BackendMessage[],
  toolResults: Map<string, unknown>,
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
    status: { type: "complete", reason: "stop" },
    createdAt: new Date(group[0].created_at),
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
    const flushAssistant = () => {
      if (assistantRows.length === 0) return;
      const message = toAssistantThreadMessage(assistantRows, toolResults);
      if (message) out.push(message);
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
  }

  return out;
}
