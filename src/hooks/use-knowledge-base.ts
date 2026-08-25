import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";

import { BizError } from "@/lib/http";
import { knowledgeService } from "@/services/knowledge-service";
import type { KnowledgeBaseUpdateInput } from "@/services/knowledge-service";
import type { BackendKnowledgeBase } from "@/services/types";
import { isTransitional } from "@/hooks/use-knowledge-list";

// ===========================================================================
// Hook —— 单个知识库：详情加载 + 状态轮询 + 写操作
//
// 供知识库详情页头部使用；kbId 来自路由参数。404（知识库不存在/已删完）
// 用 notFound 标记而非 error，页面据此跳回列表页。
// ===========================================================================

const STATUS_POLL_INTERVAL_MS = 3000;

export interface UseKnowledgeBaseResult {
  knowledgeBase: BackendKnowledgeBase | null;
  isLoading: boolean;
  /** true = 后端返回 404（新建后异步落库/已被删除），区别于网络错误 */
  notFound: boolean;
  error: Error | null;
  refresh: (silent?: boolean) => Promise<void>;
  updateKnowledgeBase: (input: KnowledgeBaseUpdateInput) => Promise<boolean>;
  deleteKnowledgeBase: () => Promise<boolean>;
  setKnowledgeEnabled: (enabled: boolean) => Promise<boolean>;
}

export function useKnowledgeBase(kbId: string | undefined): UseKnowledgeBaseResult {
  const [knowledgeBase, setKnowledgeBase] = useState<BackendKnowledgeBase | null>(
    null,
  );
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const refresh = useCallback(
    async (silent = false) => {
      if (!kbId) return;
      if (!silent) setIsLoading(true);
      setError(null);
      try {
        const kb = await knowledgeService.getKnowledgeBase(kbId);
        setKnowledgeBase(kb);
        setNotFound(false);
      } catch (err) {
        if (axios.isAxiosError(err) && err.response?.status === 404) {
          setNotFound(true);
        } else {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      } finally {
        if (!silent) setIsLoading(false);
      }
    },
    [kbId],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 条件轮询：新建库处于 pending/processing、删除中处于 deleting 时跟进状态
  useEffect(() => {
    if (!knowledgeBase || !isTransitional(knowledgeBase.status)) return;
    const timer = setTimeout(
      () => void refresh(true),
      STATUS_POLL_INTERVAL_MS,
    );
    return () => clearTimeout(timer);
  }, [knowledgeBase, refresh]);

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

  return {
    knowledgeBase,
    isLoading,
    notFound,
    error,
    refresh,
    updateKnowledgeBase: useCallback(
      (input: KnowledgeBaseUpdateInput) =>
        kbId
          ? runAction(
              () => knowledgeService.updateKnowledgeBase(kbId, input),
              "知识库已更新",
            )
          : Promise.resolve(false),
      [kbId, runAction],
    ),
    deleteKnowledgeBase: useCallback(
      () =>
        kbId
          ? runAction(
              () => knowledgeService.deleteKnowledgeBase(kbId),
              "删除请求已受理",
            )
          : Promise.resolve(false),
      [kbId, runAction],
    ),
    setKnowledgeEnabled: useCallback(
      (enabled: boolean) =>
        kbId
          ? runAction(
              () => knowledgeService.setKnowledgeEnabled(kbId, enabled),
              enabled ? "知识库已启用" : "知识库已停用",
            )
          : Promise.resolve(false),
      [kbId, runAction],
    ),
  };
}
