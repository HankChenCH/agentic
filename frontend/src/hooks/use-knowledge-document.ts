import { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";

import { BizError } from "@/lib/http";
import { knowledgeService } from "@/services/knowledge-service";
import type { KnowledgeDocumentUpdateInput } from "@/services/knowledge-service";
import type { BackendKnowledgeDocument } from "@/services/types";
import { isTransitional } from "@/hooks/use-knowledge-list";

// ===========================================================================
// Hook —— 单个知识库文档：详情加载 + 状态轮询 + 文档级写操作
//
// 供文档详情页头部使用；与 use-knowledge-base 同构（404 用 notFound 标记，
// 页面据此跳回知识库详情）。分段管理（use-knowledge-segments）只依赖本
// hook 的数据做状态闸门展示，写操作互不耦合。
// ===========================================================================

const STATUS_POLL_INTERVAL_MS = 3000;

export interface UseKnowledgeDocumentResult {
  document: BackendKnowledgeDocument | null;
  isLoading: boolean;
  /** true = 后端返回 404（文档不存在/已删完），区别于网络错误 */
  notFound: boolean;
  error: Error | null;
  refresh: (silent?: boolean) => Promise<void>;
  updateDocument: (input: KnowledgeDocumentUpdateInput) => Promise<boolean>;
  deleteDocument: () => Promise<boolean>;
  setDocumentEnabled: (enabled: boolean) => Promise<boolean>;
  retryDocument: () => Promise<boolean>;
  /** 整篇重分段（受理后文档回 pending，状态由轮询跟进） */
  rechunkDocument: () => Promise<boolean>;
}

export function useKnowledgeDocument(
  kbId: string | undefined,
  docId: string | undefined,
): UseKnowledgeDocumentResult {
  const [document, setDocument] = useState<BackendKnowledgeDocument | null>(
    null,
  );
  const [isLoading, setIsLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  const refresh = useCallback(
    async (silent = false) => {
      if (!kbId || !docId) return;
      if (!silent) setIsLoading(true);
      setError(null);
      try {
        const doc = await knowledgeService.getDocument(kbId, docId);
        setDocument(doc);
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
    [kbId, docId],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 条件轮询：解析/向量化（pending/processing）与删除清理（deleting）时跟进状态
  useEffect(() => {
    if (!document || !isTransitional(document.status)) return;
    const timer = setTimeout(() => void refresh(true), STATUS_POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [document, refresh]);

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
    document,
    isLoading,
    notFound,
    error,
    refresh,
    updateDocument: useCallback(
      (input: KnowledgeDocumentUpdateInput) =>
        kbId && docId
          ? runAction(
              () => knowledgeService.updateDocument(kbId, docId, input),
              "文档信息已更新",
            )
          : Promise.resolve(false),
      [kbId, docId, runAction],
    ),
    deleteDocument: useCallback(
      () =>
        kbId && docId
          ? runAction(
              () => knowledgeService.deleteDocument(kbId, docId),
              "删除请求已受理",
            )
          : Promise.resolve(false),
      [kbId, docId, runAction],
    ),
    setDocumentEnabled: useCallback(
      (enabled: boolean) =>
        kbId && docId
          ? runAction(
              () => knowledgeService.setDocumentEnabled(kbId, docId, enabled),
              enabled ? "文档已启用" : "文档已停用",
            )
          : Promise.resolve(false),
      [kbId, docId, runAction],
    ),
    retryDocument: useCallback(
      () =>
        kbId && docId
          ? runAction(
              () => knowledgeService.retryDocument(kbId, docId),
              "已重新排队处理",
            )
          : Promise.resolve(false),
      [kbId, docId, runAction],
    ),
    rechunkDocument: useCallback(
      () =>
        kbId && docId
          ? runAction(
              () => knowledgeService.rechunkDocument(kbId, docId),
              "已重新排队分段处理",
            )
          : Promise.resolve(false),
      [kbId, docId, runAction],
    ),
  };
}
