
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

import type { ThreadMessage } from "@assistant-ui/core";
import { fromThreadMessageLike } from "@assistant-ui/core";
import type { UseAgUiThreadListAdapter } from "@assistant-ui/react-ag-ui";
import type { HttpAgent } from "@ag-ui/client";
import { randomUUID } from "@ag-ui/client";

import { conversationService } from "@/services/conversation-service";
import {
  collectTurnIdByMessageId,
  collectUpdatedMessageIds,
  toThreadBranchTree,
  toThreadMessages,
} from "@/services/translators/thread-message-translator";
import type {
  BackendConversation,
  BackendConversationTurn,
} from "@/services/types";
import { BizError } from "@/lib/http";
import { useAuthStore } from "@/stores/auth-store";
import { useAgentStore } from "@/stores/agent-store";

// ===========================================================================
// Hook —— 会话列表的状态管理 + runtime adapter 装配
//
// 本 hook 只管 React 状态和 adapter 拼装，不做数据翻译（翻译归
// @/services/translators/thread-message-translator）也不直接发请求
// （网络归 @/services/conversation-service）。
// ===========================================================================

// 标题轮询：服务端在 RunFinished 之后才由后台线程生成标题（LLM 调用，
// 约 1-3s），SSE 流里带不出来；轮次成功收尾后按固定间隔查单会话接口，
// 直到 conversation_title 非空再刷新列表，总窗口约 7.5s。
const TITLE_POLL_INTERVAL_MS = 1500;
const TITLE_POLL_MAX_ATTEMPTS = 5;

const delay = (ms: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * useConversationList 的入参（一个大对象）。
 *
 * - `pageSize?`: 列表分页大小，默认 20
 * - `historyLimit?`: 切换会话时拉取的历史消息上限，默认 100
 */
export interface UseConversationListOptions {
  pageSize?: number;
  historyLimit?: number;
}

/**
 * 会话操作与列表分页能力，经 context 下发给 runtime 之外的组件（侧栏
 * 删除按钮 + 确认弹窗 + 加载更多按钮）。Provider 挂在 AgenticRuntimeProvider
 * （见 agentic-runtime.tsx），实现来自 useConversationList 的返回值。
 */
export interface ConversationActions {
  deleteConversation: (threadId: string) => Promise<boolean>;
  /** 追加加载下一页会话；hasMore/isLoadingMore 驱动按钮显隐与禁用态 */
  loadMoreConversations: () => Promise<void>;
  hasMore: boolean;
  isLoadingMore: boolean;
  /**
   * 会话列表加载失败（初始加载/手动刷新的网络与业务异常），null = 无错误。
   * 侧栏据此渲染失败横幅 + 重试入口；追加加载的失败已有 toast，不走这里。
   */
  listError: Error | null;
  /** 重新加载会话列表第 1 页（失败横幅的「重试」动作） */
  refreshConversations: () => Promise<void>;
  /**
   * 当前会话 id 镜像（agent.threadId 的 React 化影子），供路由同步消费：
   * `undefined` = 尚未同步（应用启动初态，不产生任何导航）；
   * `null` = 新会话（未发消息、后端未落库，无 id 可上 URL）；
   * `string` = 正在查看的会话 thread_id。
   */
  currentThreadId: string | null | undefined;
  /**
   * 「更新过的轮次」在 UI 消息流中的消息 id 集合（attempt_no > 1，即被重新
   * 生成/编辑过的问答），随历史加载计算。UserMessage 据此渲染「已更新」标记。
   */
  updatedMessageIds: ReadonlySet<string>;
  /**
   * 「消息 id → 轮次 id」映射，供 BranchPicker 切换分支时把目标消息映射回
   * 轮次（POST activate-turn 同步服务端活跃叶子）。
   */
  turnByMessageId: ReadonlyMap<string, string>;
  /**
   * 待种入运行时消息仓库的末梢扇形（历史加载发现叶子有兄弟变体时置位）。
   * BranchTreeHydrator 消费：aui.thread().import 种入分支仓库，使刷新后
   * BranchPicker 仍可对比/切换；消费后调用 clearPendingBranchTree 清除。
   */
  pendingBranchTree: PendingBranchTree | null;
  clearPendingBranchTree: () => void;
}

export const ConversationActionsContext =
  createContext<ConversationActions | null>(null);

export const useConversationActions = (): ConversationActions => {
  const actions = useContext(ConversationActionsContext);
  if (!actions) {
    throw new Error(
      "useConversationActions 必须在 ConversationActionsContext.Provider 内使用",
    );
  }
  return actions;
};

/**
 * useConversationList 的返回值。
 *
 * - `adapter`: 喂给 `useAgUiRuntime({ adapters: { threadList } })` 的 threadList
 *   adapter。引用随列表/回调变化而变化，从而驱动 runtime 刷新。
 * - `threads`: adapter.threads 的原始值（ExternalStoreThreadData<"regular">[]）
 * - `isLoading`: 列表首次加载态
 * - `error`: 列表加载错误（网络/业务异常），null 表示无错误
 * - `conversations`: 后端原始数据（含 id/title/current_turn_id 等）
 * - `refreshConversations`: 手动刷新列表（新建/删除会话后调用，重置回第 1 页）
 * - `hasMore`: 服务端还有更早的会话可加载（已加载页数 × pageSize < total）
 * - `isLoadingMore`: 加载更多进行中
 * - `loadMoreConversations`: 追加加载下一页会话（失败有 toast 提示）
 * - `deleteConversation`: 删除会话（硬删除）。删的是当前会话时自动切到全新
 *   空会话并刷新列表；返回是否删除成功（失败已有 toast 提示）
 */
export interface UseConversationListResult {
  adapter: UseAgUiThreadListAdapter;
  threads: NonNullable<UseAgUiThreadListAdapter["threads"]>;
  isLoading: boolean;
  error: Error | null;
  conversations: BackendConversation[];
  refreshConversations: () => Promise<void>;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreConversations: () => Promise<void>;
  deleteConversation: (threadId: string) => Promise<boolean>;
  /** 当前会话 id 镜像（语义见 ConversationActions.currentThreadId） */
  currentThreadId: string | null | undefined;
  /** 「更新过的轮次」消息 id 集合（语义见 ConversationActions.updatedMessageIds） */
  updatedMessageIds: ReadonlySet<string>;
  /** 「消息 id → 轮次 id」映射（语义见 ConversationActions.turnByMessageId） */
  turnByMessageId: ReadonlyMap<string, string>;
  /** 待种入运行时的末梢扇形（语义见 ConversationActions.pendingBranchTree） */
  pendingBranchTree: PendingBranchTree | null;
  /** 消费后清除待种树（BranchTreeHydrator 专用） */
  clearPendingBranchTree: () => void;
}

/** 待种入运行时消息仓库的末梢扇形（BranchPicker 切换 = activate-turn 同步）。 */
export interface PendingBranchTree {
  headId: string | null;
  items: { parentId: string | null; message: ThreadMessage }[];
}

/**
 * 管理会话列表的加载、切换、历史回放，以及轮次结束后的标题回填：
 * 订阅 agent 的 RunFinished，轮询单会话接口直到后端生成出
 * conversation_title，再刷新列表让侧边栏显示标题。
 *
 * 核心机制：adapter 是普通对象，要让 runtime 感知"列表变了"，必须让 adapter
 * 引用变化 → useAgUiRuntime 内部 useExternalStoreRuntime 检测到 store 变化 →
 * runtime.setAdapter → ThreadListRuntimeCore 重新读 adapter.threads → 通知
 * ThreadListPrimitive 重渲染。本 hook 用 useMemo 把列表 state 绑进 adapter 依赖，
 * 列表一变 adapter 自动重建。
 */
export function useConversationList(
  agent: HttpAgent,
  options: UseConversationListOptions = {},
): UseConversationListResult {
  const { pageSize = 20, historyLimit = 100 } = options;

  // ── 会话列表 state ──────────────────────────────────────────────
  const [conversations, setConversations] = useState<BackendConversation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  // 分页进度：page 是已加载到的页码（refresh 重置回 1），total 为后端总会话
  // 数。加载更多按 page+1 追加而非整表替换，侧栏可回看更早的会话。
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  // loadMore 并发锁：state 异步更新，双击按钮时闭包里读到的 isLoadingMore
  // 还是旧值，用 ref 才能挡住并发的第二发请求。
  const loadMoreLockRef = useRef(false);
  // 「已更新」标记集合 + 「消息 id → 轮次 id」映射：历史加载与每轮结束后
  // 从后端重建，经 context 下发——前者驱动 UserMessage 的「已更新」标记，
  // 后者供 BranchPicker 切换时把目标消息映射回轮次（activate-turn 同步）。
  const [updatedMessageIds, setUpdatedMessageIds] = useState<ReadonlySet<string>>(
    () => new Set<string>(),
  );
  const [turnByMessageId, setTurnByMessageId] = useState<ReadonlyMap<string, string>>(
    () => new Map<string, string>(),
  );
  // 待种入运行时消息仓库的末梢扇形：历史加载发现活跃叶子有兄弟变体时置位，
  // 由 BranchTreeHydrator（thread 树内）消费后清除
  const [pendingBranchTree, setPendingBranchTree] = useState<PendingBranchTree | null>(null);
  const clearPendingBranchTree = useCallback(() => setPendingBranchTree(null), []);

  const applyBranchMeta = useCallback(
    (items: BackendConversationTurn[]) => {
      setUpdatedMessageIds(collectUpdatedMessageIds(items));
      setTurnByMessageId(collectTurnIdByMessageId(items));
    },
    [],
  );

  /** 轮次结束后的静默元数据刷新：会话内新产生的变体（重试/编辑）补进映射
   * 与「已更新」集合。失败静默——切换时查不到映射只降级为视觉切换。 */
  const refreshBranchMeta = useCallback(
    async (threadId: string) => {
      try {
        const result = await conversationService.listConversationHistory(
          threadId,
          0,
          historyLimit,
        );
        applyBranchMeta(result.items ?? []);
      } catch {
        // 静默：主对话流不受影响
      }
    },
    [applyBranchMeta, historyLimit],
  );

  // runtime 主线程 id 的镜像（喂给 adapter.threadId）。平时保持 undefined，
  // 不干预既有切换路径；仅在删除当前激活会话后指向一个全新 id —— runtime
  // 检测到 adapter.threadId 变化会重建全新空主线程，聊天面板随之清空。
  const [activeThreadId, setActiveThreadId] = useState<string | undefined>(
    undefined,
  );

  // 会话身份镜像（见 ConversationActions.currentThreadId 注释）。agent 是
  // 普通对象、threadId 变更不触发渲染，这里在每个变更点同步推一份 state，
  // 供 thread-route-sync 做 URL ↔ runtime 双向绑定。
  const [currentThreadId, setCurrentThreadId] = useState<
    string | null | undefined
  >(undefined);

  const refreshConversations = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await conversationService.listConversations(1, pageSize);
      setPage(1);
      setTotal(result.total ?? 0);
      setConversations(result.items ?? []);
    } catch (err) {
      // 捕获后写入 error 状态，避免 useEffect 里的 unhandled rejection；
      // UI 可通过返回的 error 字段感知失败。
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setIsLoading(false);
    }
  }, [pageSize]);

  // 服务端还有更早的会话：以页数 × pageSize 对 total 判断，与追加时按
  // thread_id 去重无关，不会因去重漏算而卡在"永远差一条"。
  const hasMore = page * pageSize < total;

  // 追加加载下一页。两次请求之间若新建了会话，后页数据会整体前移造成
  // 跨页重复，按 thread_id 去重；失败 toast 提示且不动既有列表。
  const loadMoreConversations = useCallback(async () => {
    if (loadMoreLockRef.current) return;
    loadMoreLockRef.current = true;
    setIsLoadingMore(true);
    try {
      const result = await conversationService.listConversations(
        page + 1,
        pageSize,
      );
      setPage(page + 1);
      setTotal(result.total ?? 0);
      setConversations((prev) => {
        const seen = new Set(prev.map((c) => c.thread_id));
        return [
          ...prev,
          ...(result.items ?? []).filter((c) => !seen.has(c.thread_id)),
        ];
      });
    } catch (err) {
      toast.error(
        err instanceof BizError ? err.message : "加载更多会话失败，请稍后重试",
      );
    } finally {
      loadMoreLockRef.current = false;
      setIsLoadingMore(false);
    }
  }, [page, pageSize]);

  // 会话列表 → agent-store 的已知会话集合回填：prepareRunAgentInput 判定
  // 「新会话」（threadId 不在集合中才注入 agentId）的唯一事实源。列表数据
  // 变化即同步（初始加载/刷新/追加/删除后 refresh 都会更换 conversations）。
  useEffect(() => {
    useAgentStore.getState().setKnownThreads(
      conversations.map((c) => ({ threadId: c.thread_id, agenticId: c.agentic_id })),
    );
  }, [conversations]);

  // 初始加载按登录态门控：AgenticRuntimeProvider 包在路由外层（跨页保活），
  // 未登录挂载时也会走到这里——RequireAuth 会跳登录页，但不能先发一记必
  // 401 的列表请求；登录后 token 变化触发首次加载。
  const token = useAuthStore((s) => s.token);
  useEffect(() => {
    if (!token) return;
    void refreshConversations();
  }, [refreshConversations, token]);

  // ── 轮次结束后的标题回填 ────────────────────────────────────────
  // conversations 的 ref 镜像：订阅回调闭包里要读"最新"列表判断标题是否
  // 已知（闭包捕获的 state 是订阅建立时的旧值）。
  const conversationsRef = useRef(conversations);
  useEffect(() => {
    conversationsRef.current = conversations;
  }, [conversations]);

  // 正在轮询标题的 threadId 集合，防止同一会话并发起多个轮询循环
  const pollingThreadsRef = useRef<Set<string>>(new Set());

  const pollConversationTitle = useCallback(
    async (threadId: string, isStopped: () => boolean) => {
      if (pollingThreadsRef.current.has(threadId)) return;
      // 列表里已有非空标题（第 2+ 轮结束的场景）：无需轮询
      const known = conversationsRef.current.find(
        (c) => c.thread_id === threadId,
      );
      if (known?.conversation_title) return;

      pollingThreadsRef.current.add(threadId);
      try {
        for (let attempt = 0; attempt < TITLE_POLL_MAX_ATTEMPTS; attempt++) {
          // 标题在 RunFinished 后才异步生成，先等再查，跳过必然落空的首次请求
          await delay(TITLE_POLL_INTERVAL_MS);
          if (isStopped()) return;
          const conversation =
            await conversationService.getConversation(threadId);
          if (conversation?.conversation_title) {
            await refreshConversations();
            return;
          }
        }
      } catch {
        // 网络/服务端异常：静默放弃；标题生成失败时后端下轮会重试，
        // 前端下次刷新列表自然带出。
      } finally {
        pollingThreadsRef.current.delete(threadId);
      }
    },
    [refreshConversations],
  );

  useEffect(() => {
    // stopped 随订阅生命周期存在：卸载/重订阅后让进行中的轮询循环尽快退出
    const stopped = { current: false };
    const { unsubscribe } = agent.subscribe({
      onRunStartedEvent: () => {
        // 新会话（镜像为 null/undefined）首条消息发出后，agent.threadId 才是
        // 后端 get-or-create 落库的会话真身 —— 此刻把镜像推进到具体 id，
        // 驱动路由落到 /chat/:threadId；既有会话内发消息镜像已一致，不动。
        setCurrentThreadId((prev) =>
          prev === agent.threadId ? prev : agent.threadId,
        );
      },
      onRunFinishedEvent: ({ outcome, input }) => {
        if (outcome !== "success") return; // interrupt 不是完整轮次
        void pollConversationTitle(input.threadId, () => stopped.current);
        // 静默刷新当前会话的分支元数据：会话内新产生的变体（重试/编辑）
        // 补进「消息→轮次」映射与「已更新」集合；非当前会话切回时由
        // onSwitchToThread 重建，无需这里刷新
        if (input.threadId === agent.threadId) {
          void refreshBranchMeta(input.threadId);
        }
      },
    });
    return () => {
      stopped.current = true;
      unsubscribe();
    };
  }, [agent, pollConversationTitle, refreshBranchMeta]);

  // 后端 snake_case → adapter.threads 要的形状
  const threads = useMemo(
    () =>
      conversations.map((c) => ({
        id: c.thread_id,
        title: c.conversation_title || "New Chat",
        status: "regular" as const,
      })),
    [conversations],
  );

  // ── 回调 ─────────────────────────────────────────────────────────
  const onSwitchToNewThread = useCallback(async () => {
    // ag-ui runtime 发消息时用 agent.threadId 组装 RunAgentInput，且库不会在
    // 切换时更新它 —— 新建会话必须换一个新 threadId，否则第一条消息仍会
    // get-or-create 到旧会话（后端无显式建会话接口，id 由前端决定）。
    agent.threadId = randomUUID();
    setCurrentThreadId(null);
    // 新会话无历史，「已更新」标记与分支映射/待种树一并清空
    setUpdatedMessageIds(new Set<string>());
    setTurnByMessageId(new Map<string, string>());
    setPendingBranchTree(null);
    // 后端 init_conversation 是 chat 时 get-or-create，无需显式建会话。
    // 新会话在第一次发消息时才落库；切回列表时 refresh 即可看到它。
    // TODO(可选): 若后端将来支持 POST /conversation 显式建空会话，在此调用
    //   conversationService.xxx() 并 refreshConversations()。
  }, [agent]);

  const onSwitchToThread = useCallback(
    async (
      threadId: string,
    ): Promise<{ messages: readonly ThreadMessage[] }> => {
      // 同上：把选中会话的 thread_id 同步给 agent，后续消息才会发往该会话。
      agent.threadId = threadId;
      // 镜像在 await 之前同步推进：连续快速切换时最后点击者胜出，
      // URL 不会停留在先决会话上。
      setCurrentThreadId(threadId);
      // 切换会话 = 加载历史：
      //   conversationService.listConversationHistory → translator.toThreadMessages
      //   → fromThreadMessageLike 规整成 ThreadMessage[]（统一 complete）
      //   → return；useAgUiRuntime 会自动 applyExternalMessages
      try {
        const result = await conversationService.listConversationHistory(
          threadId,
          0,
          historyLimit,
        );
        const liked = toThreadMessages(result.items ?? []);
        const messages = liked.map((m, i) =>
          fromThreadMessageLike(m, m.id ?? `msg-${i}`, {
            type: "complete",
            reason: "stop",
          }),
        );
        // 「已更新」标记 + 分支映射：与消息流同源计算，刷新后与 ThreadMessage id 对上
        applyBranchMeta(result.items ?? []);
        // 末梢扇形种树：活跃叶子有兄弟变体时构建分支树（转换成 ThreadMessage），
        // 由 BranchTreeHydrator 在运行时空闲时 aui.thread().import 种入仓库
        const tree = toThreadBranchTree(
          result.items ?? [],
          result.active_turn_id ?? null,
        );
        setPendingBranchTree(
          tree.hasFan
            ? {
                headId: tree.headId,
                items: tree.branchItems.map(({ parentId, message }) => ({
                  parentId,
                  message: fromThreadMessageLike(
                    message,
                    message.id ?? `branch-${parentId}`,
                    { type: "complete", reason: "stop" },
                  ),
                })),
              }
            : null,
        );
        return { messages };
      } catch {
        // 切换失败时返回空，避免阻塞 runtime；列表加载错误另有 error 字段，
        // 这里若需要单独提示历史加载失败，可扩展返回值。
        setUpdatedMessageIds(new Set<string>());
        setTurnByMessageId(new Map<string, string>());
        setPendingBranchTree(null);
        return { messages: [] };
      }
    },
    [agent, applyBranchMeta, historyLimit],
  );

  // ── 删除会话 ─────────────────────────────────────────────────────
  const deleteConversation = useCallback(
    async (threadId: string): Promise<boolean> => {
      try {
        await conversationService.deleteConversation(threadId);
      } catch (err) {
        toast.error(
          err instanceof BizError ? err.message : "删除失败，请稍后重试",
        );
        return false;
      }
      // 删的是当前会话：换一个全新 threadId（后端按 threadId get-or-create，
      // 复用旧 id 会让下一条消息把已删会话"复活"），并镜像到 adapter.threadId
      // 触发 runtime 重建空主线程（面板清空，见 activeThreadId 注释）。
      if (agent.threadId === threadId) {
        agent.threadId = randomUUID();
        setActiveThreadId(agent.threadId);
        // 路由镜像回到"新会话"语义，thread-route-sync 会把地址栏送回 /
        setCurrentThreadId(null);
      }
      await refreshConversations();
      toast.success("会话已删除");
      return true;
    },
    [agent, refreshConversations],
  );

  // ── 组装 adapter。依赖列表/回调，任一变化都重建 → runtime 刷新 ──
  const adapter = useMemo<UseAgUiThreadListAdapter>(
    () => ({
      threads,
      isLoading,
      threadId: activeThreadId,
      onSwitchToNewThread,
      onSwitchToThread,
      // runtime 侧删除入口（如 ThreadListItemPrimitive.Delete）会调 onDelete；
      // 侧栏实际交互走确认弹窗（见 thread-list.tsx），但保持契约完整、两条路同款行为
      onDelete: async (threadId) => {
        await deleteConversation(threadId);
      },
      // 以下后端暂无接口，留作扩展点：
      // onRename: async (threadId, newTitle) => { ... refreshConversations(); },
      // onArchive: async (threadId) => { ... },
    }),
    [
      threads,
      isLoading,
      activeThreadId,
      onSwitchToNewThread,
      onSwitchToThread,
      deleteConversation,
    ],
  );

  return {
    adapter,
    threads,
    isLoading,
    error,
    conversations,
    refreshConversations,
    hasMore,
    isLoadingMore,
    loadMoreConversations,
    deleteConversation,
    currentThreadId,
    updatedMessageIds,
    turnByMessageId,
    pendingBranchTree,
    clearPendingBranchTree,
  };
}
