import { describe, expect, it } from "vitest";

import { isTailTurnMessage } from "@/components/assistant-ui/branch-picker-gate";

/**
 * BranchPicker 末梢锁判定：仅末梢轮次（末梢 assistant / 末梢 user）放行。
 * index 与 messageCount 对应 aui 线程消息数组的可见位置（乐观占位计入）。
 */
describe("isTailTurnMessage", () => {
  it("末梢 assistant（isLast）放行：重新生成型扇形的挂点", () => {
    // [user, assistant] 末梢回答
    expect(isTailTurnMessage(true, 1, 2)).toBe(true);
  });

  it("末梢 user（倒数第二）放行：编辑型扇形的挂点", () => {
    // [user(分支), assistant] 末梢轮次的问题行
    expect(isTailTurnMessage(false, 0, 2)).toBe(true);
    // [user, assistant, user(分支), assistant] 末梢轮次的问题行
    expect(isTailTurnMessage(false, 2, 4)).toBe(true);
  });

  it("中途消息一律锁死（续聊即定型）", () => {
    // [user(旧分支), assistant, user, assistant]：旧轮次两行都在中途
    expect(isTailTurnMessage(false, 0, 4)).toBe(false);
    expect(isTailTurnMessage(false, 1, 4)).toBe(false);
  });

  it("单消息会话（仅 user，回答未产生）放行", () => {
    expect(isTailTurnMessage(true, 0, 1)).toBe(true);
  });

  it("运行中新轮次开跑后，原末梢 user 退居中途不再放行", () => {
    // [user, assistant, user(旧末梢), assistant, user(新消息), optimistic]
    expect(isTailTurnMessage(false, 2, 6)).toBe(false);
  });

  it("新发送的 user（倒数第二，末尾是乐观占位）过门控，但 bc=1 由 hideWhenSingleBranch 压制", () => {
    // [user, assistant, user(新消息), optimistic]
    expect(isTailTurnMessage(false, 2, 4)).toBe(true);
  });
});
