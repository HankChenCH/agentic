import { describe, expect, it } from "vitest";

import {
  applyRunInputInjections,
  type RunAgentInputLike,
} from "@/services/run-input";

function makeInput(overrides: Partial<RunAgentInputLike> = {}): RunAgentInputLike {
  return {
    threadId: "new-thread-uuid",
    runId: "run-1",
    messages: [],
    ...overrides,
  };
}

describe("applyRunInputInjections（agent 选择注入 + 分支信号提升）", () => {
  it("新会话（未知 threadId、非 resume）注入选中的 agentId", () => {
    const out = applyRunInputInjections(makeInput(), {
      selectedAgentId: "builtin:rag_agent",
      knownThread: false,
    });
    expect(out.forwardedProps?.agentId).toBe("builtin:rag_agent");
  });

  it("已知会话不注入 agentId（绑定不可被续聊改写）", () => {
    const out = applyRunInputInjections(makeInput(), {
      selectedAgentId: "builtin:rag_agent",
      knownThread: true,
    });
    expect(out.forwardedProps).not.toHaveProperty("agentId");
  });

  it("resume 运行不注入 agentId", () => {
    const out = applyRunInputInjections(makeInput({ resume: true }), {
      selectedAgentId: "builtin:rag_agent",
      knownThread: false,
    });
    expect(out.forwardedProps).not.toHaveProperty("agentId");
  });

  it("未选择智能体不注入 agentId", () => {
    for (const selectedAgentId of [null, ""]) {
      const out = applyRunInputInjections(makeInput(), { selectedAgentId, knownThread: false });
      expect(out.forwardedProps).not.toHaveProperty("agentId");
    }
  });

  it("分支信号提升为 forwardedProps.branch，runConfig 载体删除，且不受新会话门控", () => {
    const out = applyRunInputInjections(
      makeInput({ forwardedProps: { runConfig: { branchBaseMessageId: "m-1" } } }),
      { selectedAgentId: "builtin:rag_agent", knownThread: true },
    );
    expect(out.forwardedProps?.branch).toEqual({ baseMessageId: "m-1" });
    expect(out.forwardedProps).not.toHaveProperty("runConfig");
    // 已知会话：branch 提升照常生效，agentId 不注入——两者互不门控
    expect(out.forwardedProps).not.toHaveProperty("agentId");
  });

  it("runConfig 无有效 branchBaseMessageId（缺失/空串/非字符串）只删载体不提升", () => {
    for (const runConfig of [undefined, {}, { branchBaseMessageId: "" }, { branchBaseMessageId: 7 }]) {
      const out = applyRunInputInjections(
        makeInput({ forwardedProps: runConfig as Record<string, unknown> }),
        { selectedAgentId: null, knownThread: false },
      );
      expect(out.forwardedProps).not.toHaveProperty("runConfig");
      expect(out.forwardedProps).not.toHaveProperty("branch");
    }
  });

  it("注入产出新对象，不原地改动输入（含 forwardedProps）", () => {
    const originalForwarded: Record<string, unknown> = { runConfig: { branchBaseMessageId: "m-1" } };
    const input = makeInput({ forwardedProps: originalForwarded });
    applyRunInputInjections(input, { selectedAgentId: "builtin:rag_agent", knownThread: false });
    expect(originalForwarded).toEqual({ runConfig: { branchBaseMessageId: "m-1" } });
  });

  it("输入的其余字段原样透传", () => {
    const out = applyRunInputInjections(makeInput(), {
      selectedAgentId: null,
      knownThread: false,
    });
    expect(out.threadId).toBe("new-thread-uuid");
    expect(out.runId).toBe("run-1");
    expect(out.messages).toEqual([]);
  });
});
