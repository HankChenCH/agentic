import { describe, expect, it } from "vitest";

import {
  toThreadBranchTree,
  toThreadMessages,
} from "@/services/translators/thread-message-translator";
import type {
  BackendConversationTurn,
  BackendMessage,
  BackendMessageContent,
} from "@/services/types";

// ---------------------------------------------------------------------------
// fixture 构造：按后端 history 的 snake_case 契约造 turn / message 行
// ---------------------------------------------------------------------------

const T0 = "2026-01-01T00:00:00Z";
let nextRowId = 1;

function makeMessage(
  turnId: string,
  overrides: Partial<BackendMessage> & Pick<BackendMessage, "message_id" | "role">,
): BackendMessage {
  return {
    id: nextRowId++,
    thread_id: "thread-1",
    turn_id: turnId,
    parent_message_id: null,
    sequence_num: 0,
    message_type: "message",
    content: [],
    token_usage: {},
    latency_ms: 0,
    created_at: T0,
    updated_at: T0,
    ...overrides,
  };
}

function userMsg(turnId: string, messageId: string, text: string, sequenceNum = 0): BackendMessage {
  return makeMessage(turnId, {
    message_id: messageId,
    role: "user",
    sequence_num: sequenceNum,
    content: [{ type: "text", text }],
  });
}

function assistantMsg(turnId: string, messageId: string, text: string, sequenceNum = 1): BackendMessage {
  return makeMessage(turnId, {
    message_id: messageId,
    role: "assistant",
    sequence_num: sequenceNum,
    content: [{ type: "text", text }],
  });
}

function makeTurn(
  overrides: Partial<BackendConversationTurn> & Pick<BackendConversationTurn, "turn_id" | "messages">,
): BackendConversationTurn {
  return {
    id: nextRowId++,
    thread_id: "thread-1",
    run_id: "run-1",
    turn_num: 0,
    status: "completed",
    parent_turn_id: null,
    attempt_no: 1,
    token_usage: {},
    created_at: T0,
    updated_at: T0,
    ...overrides,
  };
}

/** 一轮最简问答（user + assistant 各一行） */
function qaTurn(
  turnId: string,
  turnNum: number,
  parentId: string | null,
  question: string,
  answer: string,
  overrides: Partial<BackendConversationTurn> = {},
): BackendConversationTurn {
  return makeTurn({
    turn_id: turnId,
    turn_num: turnNum,
    parent_turn_id: parentId,
    messages: [userMsg(turnId, `u-${turnId}`, question), assistantMsg(turnId, `a-${turnId}`, answer)],
    ...overrides,
  });
}

// ---------------------------------------------------------------------------
// 分支树构建（toThreadBranchTree）
// ---------------------------------------------------------------------------

describe("toThreadBranchTree", () => {
  it("空输入产出全空树", () => {
    expect(toThreadBranchTree([], "t-any")).toEqual({
      headMessages: [],
      branchItems: [],
      headId: null,
      hasFan: false,
    });
  });

  it("线性会话按 turn_num 旧→新展开，父链接逐级衔接", () => {
    const t0 = qaTurn("t0", 0, null, "问题一", "答案一");
    const t1 = qaTurn("t1", 1, "t0", "问题二", "答案二");
    // 故意乱序传入：树构建必须自行按 turn_num 排序
    const tree = toThreadBranchTree([t1, t0], "t1");

    expect(tree.headMessages.map((m) => m.id)).toEqual(["u-t0", "a-t0", "u-t1", "a-t1"]);
    expect(tree.headId).toBe("a-t1");
    expect(tree.hasFan).toBe(false);
    // user 节点挂在上一 assistant 之后，首个 user 为根
    expect(tree.branchItems.map((n) => n.message.id)).toEqual(["u-t0", "a-t0", "u-t1", "a-t1"]);
    expect(tree.branchItems.map((n) => n.parentId)).toEqual([null, "u-t0", "a-t0", "u-t1"]);
  });

  it("重试兄弟成末梢扇形：head 只走活跃叶子，变体挂锚点 assistant 下", () => {
    const t0 = qaTurn("t0", 0, null, "问题一", "答案一");
    const t1 = qaTurn("t1", 1, "t0", "问题二", "旧答案");
    const t2 = qaTurn("t2", 2, "t0", "问题二", "新答案", { attempt_no: 2 });

    const tree = toThreadBranchTree([t0, t1, t2], "t1");
    expect(tree.headMessages.map((m) => m.id)).toEqual(["u-t0", "a-t0", "u-t1", "a-t1"]);
    expect(tree.headId).toBe("a-t1");
    expect(tree.hasFan).toBe(true);
    // 变体重新生成（用户文本相同、各自 user 节点）：user 挂锚点 a-t0，assistant 挂自身 user
    const fanUser = tree.branchItems.find((n) => n.message.id === "u-t2");
    expect(fanUser?.parentId).toBe("a-t0");
    const fanAssistant = tree.branchItems.find((n) => n.message.id === "a-t2");
    expect(fanAssistant?.parentId).toBe("u-t2");
  });

  it("激活切换：head 链跟随新叶子，旧叶子沦为扇形变体", () => {
    const t0 = qaTurn("t0", 0, null, "问题一", "答案一");
    const t1 = qaTurn("t1", 1, "t0", "问题二", "旧答案");
    const t2 = qaTurn("t2", 2, "t0", "问题二", "新答案", { attempt_no: 2 });

    const tree = toThreadBranchTree([t0, t1, t2], "t2");
    expect(tree.headMessages.map((m) => m.id)).toEqual(["u-t0", "a-t0", "u-t2", "a-t2"]);
    expect(tree.headId).toBe("a-t2");
    const oldFanAssistant = tree.branchItems.find((n) => n.message.id === "a-t1");
    expect(oldFanAssistant?.parentId).toBe("u-t1");
  });

  it("活跃叶子为 null 或不在载荷：整体按 turn_num 线性退化，无扇形", () => {
    const t0 = qaTurn("t0", 0, null, "问题一", "答案一");
    const t1 = qaTurn("t1", 1, "t0", "问题二", "答案二");

    for (const activeTurnId of [null, "ghost-turn"]) {
      const tree = toThreadBranchTree([t1, t0], activeTurnId);
      expect(tree.headMessages.map((m) => m.id)).toEqual(["u-t0", "a-t0", "u-t1", "a-t1"]);
      expect(tree.hasFan).toBe(false);
    }
  });

  it("根轮次的重试变体无锚点可挂：变体被放弃但 hasFan 如实为 true（现状口径）", () => {
    const t1 = qaTurn("t1", 0, null, "问题一", "旧答案");
    const t2 = qaTurn("t2", 1, null, "问题一", "新答案", { attempt_no: 2 });

    const tree = toThreadBranchTree([t1, t2], "t1");
    expect(tree.headMessages.map((m) => m.id)).toEqual(["u-t1", "a-t1"]);
    // parent 为 null 的变体找不到锚点 assistant，树里不重复挂载（文档化现状）
    expect(tree.branchItems.map((n) => n.message.id)).toEqual(["u-t1", "a-t1"]);
    expect(tree.hasFan).toBe(true);
  });

  it("失败轮次无 assistant 行：合成（生成失败）占位并标注 incomplete/error", () => {
    const t0 = qaTurn("t0", 0, null, "问题一", "答案一");
    const failed = makeTurn({
      turn_id: "tf",
      turn_num: 1,
      parent_turn_id: "t0",
      status: "failed",
      messages: [userMsg("tf", "u-tf", "问题二")],
    });

    const tree = toThreadBranchTree([t0, failed], "tf");
    const placeholder = tree.headMessages.at(-1);
    expect(placeholder?.role).toBe("assistant");
    expect(placeholder?.status).toEqual({ type: "incomplete", reason: "error" });
    expect(placeholder?.content).toEqual([{ type: "text", text: "（生成失败）" }]);
    expect(tree.headId).toBe(`${"tf"}-placeholder`);
  });

  it("取消轮次合成（已停止生成）占位并标注 incomplete/cancelled", () => {
    const canceled = makeTurn({
      turn_id: "tc",
      turn_num: 0,
      status: "canceled",
      messages: [userMsg("tc", "u-tc", "问题")],
    });

    const messages = toThreadMessages([canceled]);
    expect(messages.at(-1)?.status).toEqual({ type: "incomplete", reason: "cancelled" });
    expect(messages.at(-1)?.content).toEqual([{ type: "text", text: "（已停止生成）" }]);
  });
});

// ---------------------------------------------------------------------------
// 附件解析（user 消息 image part → attachments，经 toThreadMessages 触达）
// ---------------------------------------------------------------------------

type ImageAttachment = {
  id: string;
  type: "image";
  name: string;
  contentType: string;
  status: { type: "complete" };
  content: { type: "image"; image: string }[];
};

function userTurnWithContent(content: BackendMessageContent[]): BackendConversationTurn {
  return makeTurn({
    turn_id: "t-img",
    messages: [makeMessage("t-img", { message_id: "u-img", role: "user", content })],
  });
}

function attachmentsOf(content: BackendMessageContent[]): ImageAttachment[] {
  const messages = toThreadMessages([userTurnWithContent(content)]);
  const user = messages[0];
  expect(user?.role).toBe("user");
  return (user?.attachments ?? []) as ImageAttachment[];
}

describe("附件解析", () => {
  it("data 源转 base64 data URL", () => {
    const atts = attachmentsOf([
      { type: "image", source: { type: "data", value: "QUJD", mimeType: "image/jpeg" } },
    ]);
    expect(atts).toHaveLength(1);
    expect(atts[0]?.contentType).toBe("image/jpeg");
    expect(atts[0]?.content[0]?.image).toBe("data:image/jpeg;base64,QUJD");
  });

  it("url 源：绝对 http(s) 与裸相对引用均原样保留（不拼 REST_BASE）", () => {
    const atts = attachmentsOf([
      { type: "image", source: { type: "url", value: "/agentic/attachments/a.png" } },
      { type: "image", source: { type: "url", value: "https://example.com/b.png" } },
      { type: "image", source: { type: "url", value: "HTTP://example.com/c.png" } },
    ]);
    expect(atts.map((a) => a.content[0]?.image)).toEqual([
      "/agentic/attachments/a.png",
      "https://example.com/b.png",
      "HTTP://example.com/c.png",
    ]);
  });

  it("缺失 mimeType 默认 image/png；多图 id 按序复合", () => {
    const atts = attachmentsOf([
      { type: "image", source: { type: "url", value: "/agentic/attachments/a.png" } },
      { type: "image", source: { type: "url", value: "/agentic/attachments/b.png", mimeType: "image/webp" } },
    ]);
    expect(atts[0]?.contentType).toBe("image/png");
    expect(atts[0]?.id).toBe("u-img-0");
    expect(atts[1]?.contentType).toBe("image/webp");
    expect(atts[1]?.id).toBe("u-img-1");
  });

  it("纯文本消息不产出 attachments 键", () => {
    const messages = toThreadMessages([userTurnWithContent([{ type: "text", text: "没有图片" }])]);
    expect(messages[0]?.role).toBe("user");
    expect(messages[0]).not.toHaveProperty("attachments");
  });
});

// ---------------------------------------------------------------------------
// CUSTOM 行（A2UI 卡片等表现层载荷）→ data part
// ---------------------------------------------------------------------------

describe("toThreadMessages 的 CUSTOM 行", () => {
  const a2uiPayload = [
    {
      version: "v0.9",
      createSurface: { surfaceId: "weather-x", catalogId: "https://a2ui.org/x" },
    },
    {
      version: "v0.9",
      updateComponents: {
        surfaceId: "weather-x",
        components: [{ id: "root", component: "Card", child: "t" }],
      },
    },
  ];

  it("custom 行翻译为 data part（name/value 原样透传），排在 tool-call part 之后", () => {
    const turnId = "turn-a2ui";
    const turn = makeTurn({
      turn_id: turnId,
      turn_num: 0,
      parent_turn_id: null,
      messages: [
        userMsg(turnId, "u-1", "中山天气如何", 0),
        makeMessage(turnId, {
          message_id: "a-1",
          role: "assistant",
          sequence_num: 1,
          message_type: "tool_call",
          content: [{ type: "tool_call", tool_call_id: "c1", name: "get_weather", args: { city: "中山" } }],
        }),
        makeMessage(turnId, {
          message_id: "a-2",
          role: "tool",
          sequence_num: 2,
          message_type: "tool_result",
          content: [{ type: "tool_result", tool_call_id: "c1", content: '{"city": "中山"}' }],
        }),
        makeMessage(turnId, {
          message_id: "a-3",
          role: "assistant",
          sequence_num: 3,
          message_type: "custom",
          content: [{ type: "custom", name: "a2ui", value: a2uiPayload }],
        }),
        makeMessage(turnId, {
          message_id: "a-4",
          role: "assistant",
          sequence_num: 4,
          message_type: "message",
          content: [{ type: "text", text: "中山今天晴朗。" }],
        }),
      ],
    });

    const messages = toThreadMessages([turn]);
    expect(messages).toHaveLength(2);
    const assistant = messages[1]!;
    const parts = assistant.content as unknown as Array<Record<string, unknown>>;
    expect(parts.map((p) => p.type)).toEqual(["tool-call", "data", "text"]);
    expect(parts[1]).toEqual({ type: "data", name: "a2ui", data: a2uiPayload });
    // tool_result 仍并入 tool-call part 的 result
    expect(parts[0]).toMatchObject({ toolCallId: "c1", result: '{"city": "中山"}' });
  });

  it("坏 custom content（无 custom part）静默跳过不产 part", () => {
    const turnId = "turn-a2ui-bad";
    const turn = makeTurn({
      turn_id: turnId,
      turn_num: 0,
      parent_turn_id: null,
      messages: [
        userMsg(turnId, "u-1", "q", 0),
        makeMessage(turnId, {
          message_id: "a-1",
          role: "assistant",
          sequence_num: 1,
          message_type: "custom",
          content: [],
        }),
      ],
    });
    const messages = toThreadMessages([turn]);
    // assistant 组无可渲染 part → 不产出消息（user 行照常）
    expect(messages).toHaveLength(1);
    expect(messages[0]?.role).toBe("user");
  });
});
