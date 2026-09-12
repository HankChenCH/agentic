// @vitest-environment jsdom
import { renderHook, waitFor, act, cleanup } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import type { RefObject } from "react";
import type { AssistantRuntime } from "@assistant-ui/core";
import type { HttpAgent } from "@ag-ui/client";

import { useConversationList } from "@/hooks/use-conversation-list";
import { conversationService } from "@/services/conversation-service";
import { toast } from "sonner";
import type { BackendConversation, BackendConversationTurn } from "@/services/types";
import { useAgentStore } from "@/stores/agent-store";
import { useAuthStore } from "@/stores/auth-store";

vi.mock("@/services/conversation-service", () => ({
  conversationService: {
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    listConversationHistory: vi.fn(),
    deleteConversation: vi.fn(),
    cancelRun: vi.fn(),
  },
}));
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

const listConversationsMock = vi.mocked(conversationService.listConversations);
const getConversationMock = vi.mocked(conversationService.getConversation);
const listHistoryMock = vi.mocked(conversationService.listConversationHistory);
const deleteConvMock = vi.mocked(conversationService.deleteConversation);
const toastMock = vi.mocked(toast);

// ---------------------------------------------------------------------------
// fixture 与 fake
// ---------------------------------------------------------------------------

let convSeq = 0;
const makeConversation = (
  overrides: Partial<BackendConversation> = {},
): BackendConversation => ({
  id: (convSeq += 1),
  agentic_id: `agent-${convSeq}`,
  user_id: "u-1",
  thread_id: `thread-${convSeq}`,
  current_turn_id: null,
  conversation_title: "",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  ...overrides,
});

const makeTurn = (
  overrides: Partial<BackendConversationTurn> = {},
): BackendConversationTurn => ({
  id: 1,
  thread_id: "t",
  run_id: "r",
  turn_id: "turn-1",
  turn_num: 1,
  status: "completed",
  parent_turn_id: null,
  attempt_no: 1,
  token_usage: {},
  messages: [
    {
      id: 1,
      thread_id: "t",
      turn_id: "turn-1",
      message_id: "m-1",
      parent_message_id: null,
      sequence_num: 0,
      role: "user",
      message_type: "message",
      content: [{ type: "text", text: "你好" }],
      token_usage: {},
      latency_ms: 0,
      created_at: "",
      updated_at: "",
    },
  ],
  created_at: "",
  updated_at: "",
  ...overrides,
});

/** fake HttpAgent：hook 只用 threadId + subscribe */
const makeAgent = () => {
  let handlers: {
    onRunStartedEvent?: () => void;
    onRunFinishedEvent?: (e: {
      outcome: string;
      input: { threadId: string };
    }) => void;
  } = {};
  const subscribe = vi.fn((h: typeof handlers) => {
    handlers = h;
    return { unsubscribe: vi.fn() };
  });
  const agent = { threadId: "thread-old", subscribe };
  return {
    agent: agent as unknown as HttpAgent,
    subscribe,
    setThreadId: (id: string) => {
      agent.threadId = id;
    },
    emitRunStarted: () => handlers.onRunStartedEvent?.(),
    emitRunFinished: (threadId: string, outcome = "success") =>
      handlers.onRunFinishedEvent?.({ outcome, input: { threadId } }),
  };
};

/** fake runtime 桥：鸭子类型 threads.main / threads.switchToNewThread */
const makeRuntimeRef = (isRunning = false) => {
  const cancelRun = vi.fn();
  const switchToNewThread = vi.fn(async () => {});
  const ref = {
    current: {
      threads: {
        main: { getState: () => ({ isRunning }), cancelRun },
        switchToNewThread,
      },
    },
  } as unknown as RefObject<AssistantRuntime | null>;
  return { ref, cancelRun, switchToNewThread };
};

const resetStores = () => {
  useAuthStore.setState({
    token: null,
    refreshToken: null,
    expiresAt: null,
    refreshExpiresAt: null,
    user: null,
  });
  useAgentStore.setState({
    selectedAgentId: null,
    agents: [],
    defaultAgentId: null,
    knownThreads: {},
    loaded: false,
  });
};

const renderList = (
  agent: HttpAgent,
  options?: { pageSize?: number; runtimeRef?: RefObject<AssistantRuntime | null> },
) =>
  renderHook(() => useConversationList(agent, options), {
    initialProps: undefined,
  });

beforeEach(resetStores);
afterEach(() => {
  // vitest 未开 globals，RTL 自动 cleanup 不生效——不卸载的 hook 会残留
  // 订阅 zustand store，跨测试串扰（token 一变所有旧实例都发请求）
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// 列表加载：登录门控 / 翻译映射 / 错误态
// ---------------------------------------------------------------------------

describe("会话列表加载（登录态门控）", () => {
  it("未登录挂载不发列表请求（必 401 的空枪）", async () => {
    const { agent } = makeAgent();
    renderList(agent);
    await act(async () => {});
    expect(listConversationsMock).not.toHaveBeenCalled();
  });

  it("登录后（含先挂载后登录）触发首次加载", async () => {
    const { agent } = makeAgent();
    listConversationsMock.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    const { rerender } = renderList(agent);
    await act(async () => {});
    expect(listConversationsMock).not.toHaveBeenCalled();

    await act(async () => {
      useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
      rerender();
    });
    await waitFor(() => expect(listConversationsMock).toHaveBeenCalledTimes(1));
    expect(listConversationsMock).toHaveBeenCalledWith(1, 20);
  });

  it("加载成功：threads 映射（无标题回退 New Chat）+ knownThreads 回填 agent-store", async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent } = makeAgent();
    listConversationsMock.mockResolvedValue({
      items: [
        makeConversation({ thread_id: "t-a", conversation_title: "标题 A" }),
        makeConversation({ thread_id: "t-b" }),
      ],
      total: 2,
      page: 1,
      pageSize: 20,
    });

    const { result } = renderList(agent);
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.threads).toEqual([
      { id: "t-a", title: "标题 A", status: "regular" },
      { id: "t-b", title: "New Chat", status: "regular" },
    ]);
    expect(result.current.hasMore).toBe(false);
    expect(useAgentStore.getState().knownThreads).toEqual({
      "t-a": expect.any(String),
      "t-b": expect.any(String),
    });
  });

  it("加载失败：error 记录、isLoading 复位", async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent } = makeAgent();
    listConversationsMock.mockRejectedValue(new Error("boom"));

    const { result } = renderList(agent);
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe("boom");
  });
});

// ---------------------------------------------------------------------------
// agent 订阅：RUN_STARTED 刷新 + RUN_FINISHED 标题轮询
// ---------------------------------------------------------------------------

describe("agent 订阅（RUN_STARTED / RUN_FINISHED）", () => {
  it("RUN_STARTED：新会话 → 镜像推进 + 刷新列表插入侧栏", async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent, setThreadId, emitRunStarted } = makeAgent();
    listConversationsMock.mockResolvedValue({
      items: [makeConversation({ thread_id: "t-known" })],
      total: 1,
      page: 1,
      pageSize: 20,
    });

    const { result } = renderList(agent);
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(listConversationsMock).toHaveBeenCalledTimes(1);

    setThreadId("t-brand-new");
    await act(async () => {
      emitRunStarted();
    });
    // threadId 不在列表 → 再刷一次；镜像推进到具体 id
    expect(listConversationsMock).toHaveBeenCalledTimes(2);
    expect(result.current.currentThreadId).toBe("t-brand-new");
  });

  it("RUN_STARTED：既有会话 → 零开销跳过刷新，镜像照常推进", async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent, setThreadId, emitRunStarted } = makeAgent();
    listConversationsMock.mockResolvedValue({
      items: [makeConversation({ thread_id: "t-known" })],
      total: 1,
      page: 1,
      pageSize: 20,
    });

    const { result } = renderList(agent);
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    setThreadId("t-known");
    await act(async () => {
      emitRunStarted();
    });
    expect(listConversationsMock).toHaveBeenCalledTimes(1);
    expect(result.current.currentThreadId).toBe("t-known");
  });

  it("RUN_FINISHED success：标题轮询 1.5s 后查单会话，拿到标题刷新列表", async () => {
    vi.useFakeTimers();
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent, emitRunFinished } = makeAgent();
    listConversationsMock.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    getConversationMock.mockResolvedValue(
      makeConversation({ thread_id: "t-1", conversation_title: "新生标题" }),
    );

    const { result } = renderList(agent);
    await act(async () => {}); // 初始加载落地
    expect(listConversationsMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      emitRunFinished("t-1");
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(getConversationMock).toHaveBeenCalledWith("t-1");
    // 拿到标题 → 列表刷新回填
    expect(listConversationsMock).toHaveBeenCalledTimes(2);
    expect(result.current.isLoading).toBe(false);
  });

  it("标题轮询：列表已有非空标题则不轮询；无标题最多 5 次后放弃", async () => {
    vi.useFakeTimers();
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent, emitRunFinished } = makeAgent();
    const known = makeConversation({ thread_id: "t-1", conversation_title: "" });
    listConversationsMock.mockResolvedValue({ items: [known], total: 1, page: 1, pageSize: 20 });
    getConversationMock.mockResolvedValue(
      makeConversation({ thread_id: "t-1", conversation_title: "" }),
    );

    const { result } = renderList(agent);
    await act(async () => {});

    await act(async () => {
      emitRunFinished("t-1");
      // 标题始终为空：5 次 × 1.5s 后停止
      await vi.advanceTimersByTimeAsync(5 * 1500 + 100);
    });
    expect(getConversationMock).toHaveBeenCalledTimes(5);

    // 第 2+ 轮结束且列表已有标题：不再轮询（先真刷新让标题进入状态）
    getConversationMock.mockClear();
    listConversationsMock.mockResolvedValue({
      items: [makeConversation({ thread_id: "t-1", conversation_title: "已回填" })],
      total: 1,
      page: 1,
      pageSize: 20,
    });
    await act(async () => {
      await result.current.refreshConversations();
    });
    await act(async () => {
      emitRunFinished("t-1");
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(getConversationMock).not.toHaveBeenCalled();
    expect(result.current.currentThreadId).toBeUndefined(); // 从未 RUN_STARTED
  });

  it("RUN_FINISHED 非 success（interrupt）：不轮询标题", async () => {
    vi.useFakeTimers();
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent, emitRunFinished } = makeAgent();
    listConversationsMock.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });

    renderList(agent);
    await act(async () => {});
    await act(async () => {
      emitRunFinished("t-1", "interrupt");
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(getConversationMock).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// 切换 / 删除 / 取消
// ---------------------------------------------------------------------------

describe("会话切换与历史回放", () => {
  it("onSwitchToThread：先取消进行中轮次，换 threadId、推进镜像、加载历史", async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent } = makeAgent();
    const { ref, cancelRun } = makeRuntimeRef(true);
    listConversationsMock.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    listHistoryMock.mockResolvedValue({
      items: [makeTurn()],
      total: 1,
      offset: 0,
      limit: 100,
    });

    const { result } = renderList(agent, { runtimeRef: ref });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let messages: readonly unknown[] = [];
    await act(async () => {
      const out = await result.current.adapter.onSwitchToThread!("t-target");
      messages = out.messages;
    });
    expect(cancelRun).toHaveBeenCalledTimes(1);
    expect(agent.threadId).toBe("t-target");
    expect(result.current.currentThreadId).toBe("t-target");
    expect(listHistoryMock).toHaveBeenCalledWith("t-target", 0, 100);
    expect(messages).toHaveLength(1); // translator 真跑：user 消息还原
    expect(listConversationsMock).toHaveBeenCalledTimes(1); // 切换不刷列表
  });

  it("历史加载失败：返回空消息不阻塞 runtime，分支元数据清空", async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent } = makeAgent();
    listConversationsMock.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    listHistoryMock.mockRejectedValue(new Error("history down"));

    const { result } = renderList(agent);
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      const out = await result.current.adapter.onSwitchToThread!("t-x");
      expect(out.messages).toEqual([]);
    });
    expect(result.current.updatedMessageIds.size).toBe(0);
    expect(result.current.turnByMessageId.size).toBe(0);
  });
});

describe("删除会话", () => {
  it("删除成功（当前会话）：先取消运行、runtime 清空视图、刷新列表、成功 toast", async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent, setThreadId } = makeAgent();
    const { ref, cancelRun, switchToNewThread } = makeRuntimeRef(true);
    listConversationsMock.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    deleteConvMock.mockResolvedValue(makeConversation());

    const { result } = renderList(agent, { runtimeRef: ref });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    setThreadId("t-current");
    let ok = false;
    await act(async () => {
      ok = await result.current.deleteConversation("t-current");
    });
    expect(ok).toBe(true);
    expect(cancelRun).toHaveBeenCalledTimes(1);
    expect(switchToNewThread).toHaveBeenCalledTimes(1);
    expect(listConversationsMock).toHaveBeenCalledTimes(2); // 初始 + 删除后刷新
    expect(toastMock.success).toHaveBeenCalledWith("会话已删除");
  });

  it("删除失败：错误 toast 并返回 false，不刷新不清视图", async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent } = makeAgent();
    const { ref } = makeRuntimeRef(false);
    listConversationsMock.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    deleteConvMock.mockRejectedValue(new Error("down"));

    const { result } = renderList(agent, { runtimeRef: ref });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    let ok = true;
    await act(async () => {
      ok = await result.current.deleteConversation("t-1");
    });
    expect(ok).toBe(false);
    expect(toastMock.error).toHaveBeenCalledWith("删除失败，请稍后重试");
    expect(listConversationsMock).toHaveBeenCalledTimes(1);
  });

  it("删除非当前会话：不动 runtime（不取消不清视图）", async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent, setThreadId } = makeAgent();
    const { ref, cancelRun, switchToNewThread } = makeRuntimeRef(false);
    listConversationsMock.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 20 });
    deleteConvMock.mockResolvedValue(makeConversation());

    const { result } = renderList(agent, { runtimeRef: ref });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    setThreadId("t-still-here");
    await act(async () => {
      await result.current.deleteConversation("t-other");
    });
    expect(cancelRun).not.toHaveBeenCalled();
    expect(switchToNewThread).not.toHaveBeenCalled();
  });
});

describe("加载更多（分页）", () => {
  const setupPaged = async () => {
    useAuthStore.setState((s) => ({ ...s, token: "t-1" }));
    const { agent } = makeAgent();
    listConversationsMock.mockResolvedValue({
      items: [makeConversation({ thread_id: "t-1" })],
      total: 2,
      page: 1,
      pageSize: 1,
    });
    const { result } = renderList(agent, { pageSize: 1 });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    return { result, agent };
  };

  it("hasMore 按页数 × pageSize 对 total 判断", async () => {
    const { result } = await setupPaged();
    expect(result.current.hasMore).toBe(true); // 1 × 1 < 2
  });

  it("追加下一页并按 thread_id 去重；落定后 hasMore 归 false", async () => {
    const { result } = await setupPaged();
    listConversationsMock.mockResolvedValue({
      items: [
        makeConversation({ thread_id: "t-1" }), // 跨页重复（新建会话前移）
        makeConversation({ thread_id: "t-2" }),
      ],
      total: 2,
      page: 2,
      pageSize: 1,
    });

    await act(async () => {
      await result.current.loadMoreConversations();
    });
    expect(listConversationsMock).toHaveBeenLastCalledWith(2, 1);
    expect(result.current.threads.map((t) => t.id)).toEqual(["t-1", "t-2"]);
    expect(result.current.hasMore).toBe(false);
    expect(result.current.isLoadingMore).toBe(false);
  });

  it("追加失败：toast 且不动既有列表；并发第二发被 ref 锁挡住", async () => {
    const { result } = await setupPaged();
    listConversationsMock.mockRejectedValue(new Error("page down"));

    await act(async () => {
      await Promise.all([
        result.current.loadMoreConversations(),
        result.current.loadMoreConversations(),
      ]);
    });
    expect(listConversationsMock).toHaveBeenCalledTimes(2); // 初始 + 仅 1 次追加
    expect(toastMock.error).toHaveBeenCalled();
    expect(result.current.threads).toHaveLength(1);
  });
});
