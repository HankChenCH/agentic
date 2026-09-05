import { beforeEach, describe, expect, it } from "vitest";

import { getSelectedAgentId, isKnownThread, useAgentStore } from "@/stores/agent-store";

beforeEach(() => {
  // zustand 状态跨用例共享，逐项复位（persist 在 node 环境静默跳过 hydration）
  useAgentStore.setState({ selectedAgentId: null, knownThreads: {} });
});

describe("agent-store（注入决策的数据面）", () => {
  it("select 写入选中项，getSelectedAgentId 请求时现读", () => {
    expect(getSelectedAgentId()).toBeNull();
    useAgentStore.getState().select("builtin:rag_agent");
    expect(getSelectedAgentId()).toBe("builtin:rag_agent");
  });

  it("isKnownThread 只认会话列表回填的 threadId，undefined 恒为未知", () => {
    useAgentStore.setState({ knownThreads: { "thread-1": "builtin:demo" } });
    expect(isKnownThread("thread-1")).toBe(true);
    expect(isKnownThread("thread-2")).toBe(false);
    expect(isKnownThread(undefined)).toBe(false);
  });

  it("setKnownThreads 以最新回填整表替换（陈旧绑定随之失效）", () => {
    useAgentStore
      .getState()
      .setKnownThreads([
        { threadId: "a", agenticId: "builtin:demo" },
        { threadId: "b", agenticId: "builtin:rag_agent" },
      ]);
    expect(isKnownThread("a")).toBe(true);
    expect(isKnownThread("b")).toBe(true);

    useAgentStore.getState().setKnownThreads([{ threadId: "c", agenticId: "builtin:demo" }]);
    expect(useAgentStore.getState().knownThreads).toEqual({ c: "builtin:demo" });
  });
});
