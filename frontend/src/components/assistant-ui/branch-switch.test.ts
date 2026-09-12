import { describe, expect, it } from "vitest";

import {
  type BranchSwitchInput,
  BranchSwitchController,
  type BranchSwitchPorts,
} from "@/components/assistant-ui/branch-switch";
import { BizError } from "@/lib/http";

/**
 * 分支切换编排单测（纯逻辑，端口全桩）：乐观切换 → 服务端激活；
 * 成功静默；失败（业务错/网络错/映射不到轮次）toast + 回滚到切换前分支；
 * 连点时旧切换的迟到失败不回滚（seq 最新性抑制）。
 */
describe("BranchSwitchController", () => {
  /** 可编程端口桩：记录调用；failureByTurn 按轮次注入激活失败 */
  const makeHarness = () => {
    const calls = {
      switchLocal: [] as string[],
      switchLocalTo: [] as string[],
      activateTurn: [] as [string, string][],
      toasts: [] as string[],
    };
    const failureByTurn = new Map<string, unknown>();
    const deferred: Array<() => void> = [];

    const ports: BranchSwitchPorts = {
      switchLocal: (position) => calls.switchLocal.push(position),
      switchLocalTo: (messageId) => calls.switchLocalTo.push(messageId),
      activateTurn: async (threadId, turnId) => {
        calls.activateTurn.push([threadId, turnId]);
        if (failureByTurn.has(turnId)) throw failureByTurn.get(turnId);
      },
      toastError: (message) => calls.toasts.push(message),
      defer: (fn) => deferred.push(fn),
    };
    // 冲刷推迟的宏任务；让激活 promise 链落定（catch 回调在微任务里执行）
    const flush = async () => {
      while (deferred.length) {
        deferred.shift()!();
        await new Promise<void>((resolve) => setTimeout(resolve, 0));
      }
    };
    const input = (overrides?: Partial<BranchSwitchInput>): BranchSwitchInput => ({
      position: "next",
      previousMessageId: "msg-old",
      threadId: "thread-1",
      turnByMessageId: new Map([["msg-new", "turn-2"]]),
      getTailMessageId: () => "msg-new",
      ...overrides,
    });
    return { calls, ports, flush, input, failActivation: failureByTurn };
  };

  it("成功路径静默：激活目标轮次，无 toast 无回滚", async () => {
    const h = makeHarness();
    const ctl = new BranchSwitchController();

    ctl.switch(h.ports, h.input());
    expect(h.calls.switchLocal).toEqual(["next"]); // 本地乐观切换即时生效
    await h.flush();

    expect(h.calls.activateTurn).toEqual([["thread-1", "turn-2"]]);
    expect(h.calls.toasts).toEqual([]);
    expect(h.calls.switchLocalTo).toEqual([]);
  });

  it("激活业务失败：toast 带业务文案并回滚到切换前消息", async () => {
    const h = makeHarness();
    h.failActivation.set("turn-2", new BizError(4003, "目标轮次不在末梢"));
    const ctl = new BranchSwitchController();

    ctl.switch(h.ports, h.input());
    await h.flush();

    expect(h.calls.toasts).toEqual([
      "分支切换失败：目标轮次不在末梢，已回滚到原分支",
    ]);
    expect(h.calls.switchLocalTo).toEqual(["msg-old"]);
  });

  it("激活网络失败：通用文案 + 回滚", async () => {
    const h = makeHarness();
    h.failActivation.set("turn-2", new Error("boom"));
    const ctl = new BranchSwitchController();

    ctl.switch(h.ports, h.input());
    await h.flush();

    expect(h.calls.toasts).toEqual(["分支切换失败：网络异常，已回滚到原分支"]);
    expect(h.calls.switchLocalTo).toEqual(["msg-old"]);
  });

  it("末梢映射不到轮次：不发起激活，toast 并回滚", async () => {
    const h = makeHarness();
    const ctl = new BranchSwitchController();

    ctl.switch(
      h.ports,
      h.input({ turnByMessageId: new Map() }), // 切换后的末梢不在映射中
    );
    await h.flush();

    expect(h.calls.activateTurn).toEqual([]);
    expect(h.calls.toasts).toEqual([
      "分支切换失败：无法定位目标轮次，已回滚到原分支",
    ]);
    expect(h.calls.switchLocalTo).toEqual(["msg-old"]);
  });

  it("threadId 缺失：与映射失败同路，不发起激活", async () => {
    const h = makeHarness();
    const ctl = new BranchSwitchController();

    ctl.switch(h.ports, h.input({ threadId: null }));
    await h.flush();

    expect(h.calls.activateTurn).toEqual([]);
    expect(h.calls.toasts).toEqual([
      "分支切换失败：无法定位目标轮次，已回滚到原分支",
    ]);
    expect(h.calls.switchLocalTo).toEqual(["msg-old"]);
  });

  it("连点抑制：旧切换的迟到失败不回滚不提示，最新一次失败正常回滚", async () => {
    const h = makeHarness();
    h.failActivation.set("turn-2", new Error("stale 500")); // 旧切换会失败
    const ctl = new BranchSwitchController();

    // 连点两次：A(→turn-2，锚点 a) 后紧跟 B(→turn-3，锚点 b)
    ctl.switch(
      h.ports,
      h.input({
        previousMessageId: "a",
        turnByMessageId: new Map([
          ["msg-new", "turn-2"],
          ["msg-newer", "turn-3"],
        ]),
        getTailMessageId: () => "msg-new",
      }),
    );
    ctl.switch(
      h.ports,
      h.input({
        previousMessageId: "b",
        turnByMessageId: new Map([
          ["msg-new", "turn-2"],
          ["msg-newer", "turn-3"],
        ]),
        getTailMessageId: () => "msg-newer",
      }),
    );
    await h.flush();

    // 两次激活都发出；A 的失败被 seq 抑制（B 已是最新），B 成功静默
    expect(h.calls.activateTurn).toEqual([
      ["thread-1", "turn-2"],
      ["thread-1", "turn-3"],
    ]);
    expect(h.calls.toasts).toEqual([]);
    expect(h.calls.switchLocalTo).toEqual([]);
  });

  it("连点后最新一次失败：按最新锚点回滚一次", async () => {
    const h = makeHarness();
    h.failActivation.set("turn-3", new BizError(0, "轮次未完成"));
    const ctl = new BranchSwitchController();
    const sharedMap = new Map([
      ["msg-new", "turn-2"],
      ["msg-newer", "turn-3"],
    ]);

    ctl.switch(
      h.ports,
      h.input({
        previousMessageId: "a",
        turnByMessageId: sharedMap,
        getTailMessageId: () => "msg-new",
      }),
    );
    ctl.switch(
      h.ports,
      h.input({
        previousMessageId: "b",
        turnByMessageId: sharedMap,
        getTailMessageId: () => "msg-newer",
      }),
    );
    await h.flush();

    expect(h.calls.toasts).toEqual(["分支切换失败：轮次未完成，已回滚到原分支"]);
    expect(h.calls.switchLocalTo).toEqual(["b"]); // 只回滚最新一次，锚点是 b
  });
});
