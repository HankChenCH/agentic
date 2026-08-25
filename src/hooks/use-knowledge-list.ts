import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { BizError } from "@/lib/http";
import { knowledgeService } from "@/services/knowledge-service";
import type {
  KnowledgeBaseCreateInput,
  KnowledgeBaseUpdateInput,
} from "@/services/knowledge-service";
import type { BackendKnowledgeBase, KnowledgeStatus } from "@/services/types";

// ===========================================================================
// Hook —— 知识库列表：分页加载 + 状态轮询 + 增删改动作
//
// 与 use-conversation-list 同一套分层约定：本 hook 只管 React 状态与编排，
// 网络全部走 @/services/knowledge-service。写操作成功后静默刷新当前页，
// 并用 sonner toast 反馈结果（失败时展示信封里的 error_message）。
// ===========================================================================

/** 过渡态：后端异步流水线（入库/向量化/删除清理）尚未落定，需要轮询跟进 */
const TRANSITIONAL_STATUSES: ReadonlySet<KnowledgeStatus> = new Set([
  "pending",
  "processing",
  "deleting",
]);

const STATUS_POLL_INTERVAL_MS = 3000;

export function isTransitional(status: KnowledgeStatus): boolean {
  return TRANSITIONAL_STATUSES.has(status);
}

/**
 * useKnowledgeList 的返回值。
 *
 * - `items` 当前页知识库；`total/page/pageSize/setPage` 服务端分页
 * - `isLoading` 首次/翻页加载态；`error` 加载错误（null 表示无）
 * - `refresh` 手动刷新（silent=true 时不闪烁 skeleton，轮询用）
 * - `createKnowledgeBase` 等：执行写操作 → toast → 静默刷新，返回是否成功
 */
export interface UseKnowledgeListResult {
  items: BackendKnowledgeBase[];
  total: number;
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  isLoading: boolean;
  error: Error | null;
  refresh: (silent?: boolean) => Promise<void>;
  createKnowledgeBase: (input: KnowledgeBaseCreateInput) => Promise<boolean>;
  updateKnowledgeBase: (
    kbId: string,
    input: KnowledgeBaseUpdateInput,
  ) => Promise<boolean>;
  deleteKnowledgeBase: (kbId: string) => Promise<boolean>;
  setKnowledgeEnabled: (kbId: string, enabled: boolean) => Promise<boolean>;
}

export function useKnowledgeList(pageSize = 12): UseKnowledgeListResult {
  const [items, setItems] = useState<BackendKnowledgeBase[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const refresh = useCallback(
    async (silent = false) => {
      if (!silent) setIsLoading(true);
      setError(null);
      try {
        const result = await knowledgeService.listKnowledgeBases(
          page,
          pageSize,
        );
        setItems(result.items ?? []);
        setTotal(result.total);
      } catch (err) {
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (!silent) setIsLoading(false);
      }
    },
    [page, pageSize],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // ── 条件轮询 ─────────────────────────────────────────────────────
  // 列表里存在 pending/processing/deleting 时按固定间隔静默刷新；全部落定
  // 后不再排定下一个定时器，轮询自然停止。items 每次刷新都是新数组，
  // effect 依赖 items 即可实现"刷一次→重排一次"的循环。
  const hasTransitional = items.some((kb) => isTransitional(kb.status));
  useEffect(() => {
    if (!hasTransitional) return;
    const timer = setTimeout(
      () => void refresh(true),
      STATUS_POLL_INTERVAL_MS,
    );
    return () => clearTimeout(timer);
  }, [hasTransitional, items, refresh]);

  // ── 写操作：成功 → toast + 静默刷新；失败 → toast error_message ──
  const runAction = useCallback(
    async (action: () => Promise<unknown>, successMessage: string) => {
      try {
        await action();
        toast.success(successMessage);
        await refresh(true);
        return true;
      } catch (err) {
        toast.error(
          err instanceof BizError ? err.message : "操作失败，请稍后重试",
        );
        return false;
      }
    },
    [refresh],
  );

  const createKnowledgeBase = useCallback(
    (input: KnowledgeBaseCreateInput) =>
      runAction(() => knowledgeService.createKnowledgeBase(input), "知识库创建成功"),
    [runAction],
  );

  const updateKnowledgeBase = useCallback(
    (kbId: string, input: KnowledgeBaseUpdateInput) =>
      runAction(
        () => knowledgeService.updateKnowledgeBase(kbId, input),
        "知识库已更新",
      ),
    [runAction],
  );

  const deleteKnowledgeBase = useCallback(
    (kbId: string) =>
      runAction(() => knowledgeService.deleteKnowledgeBase(kbId), "删除请求已受理"),
    [runAction],
  );

  const setKnowledgeEnabled = useCallback(
    (kbId: string, enabled: boolean) =>
      runAction(
        () => knowledgeService.setKnowledgeEnabled(kbId, enabled),
        enabled ? "知识库已启用" : "知识库已停用",
      ),
    [runAction],
  );

  return {
    items,
    total,
    page,
    pageSize,
    setPage,
    isLoading,
    error,
    refresh,
    createKnowledgeBase,
    updateKnowledgeBase,
    deleteKnowledgeBase,
    setKnowledgeEnabled,
  };
}
