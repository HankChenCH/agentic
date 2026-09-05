import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { BizError } from "@/lib/http";
import { knowledgeService } from "@/services/knowledge-service";
import type { KnowledgeDocumentUpdateInput } from "@/services/knowledge-service";
import type { BackendKnowledgeDocument } from "@/services/types";
import { isTransitional } from "@/hooks/use-knowledge-list";

// ===========================================================================
// Hook —— 知识库文档列表：分页加载 + 状态轮询 + 上传/元数据/启停/删除
//
// 与 use-knowledge-list 同构，只是数据源换成某个知识库下的文档。上传是
// 单文件单请求，多文件由调用方（上传弹窗）自行串行调度。
// ===========================================================================

const STATUS_POLL_INTERVAL_MS = 3000;

export interface UseKnowledgeDocumentsResult {
  documents: BackendKnowledgeDocument[];
  total: number;
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  isLoading: boolean;
  error: Error | null;
  refresh: (silent?: boolean) => Promise<void>;
  uploadDocument: (
    file: File,
    options?: { name?: string; description?: string },
  ) => Promise<boolean>;
  updateDocument: (
    docId: string,
    input: KnowledgeDocumentUpdateInput,
  ) => Promise<boolean>;
  deleteDocument: (docId: string) => Promise<boolean>;
  retryDocument: (docId: string) => Promise<boolean>;
  setDocumentEnabled: (docId: string, enabled: boolean) => Promise<boolean>;
}

export function useKnowledgeDocuments(
  kbId: string | undefined,
  pageSize = 20,
): UseKnowledgeDocumentsResult {
  const [documents, setDocuments] = useState<BackendKnowledgeDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const refresh = useCallback(
    async (silent = false) => {
      if (!kbId) return;
      if (!silent) setIsLoading(true);
      setError(null);
      try {
        const result = await knowledgeService.listDocuments(
          kbId,
          page,
          pageSize,
        );
        setDocuments(result.items ?? []);
        setTotal(result.total);
      } catch (err) {
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (!silent) setIsLoading(false);
      }
    },
    [kbId, page, pageSize],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 条件轮询：文档解析/向量化（pending/processing）与删除清理（deleting）
  const hasTransitional = documents.some((doc) => isTransitional(doc.status));
  useEffect(() => {
    if (!hasTransitional) return;
    const timer = setTimeout(
      () => void refresh(true),
      STATUS_POLL_INTERVAL_MS,
    );
    return () => clearTimeout(timer);
  }, [hasTransitional, documents, refresh]);

  // 上传的 toast 由调用方控制（多文件串行时逐个提示），这里只刷新列表
  const refreshAfter = useCallback(
    async (action: () => Promise<unknown>) => {
      try {
        await action();
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

  const uploadDocument = useCallback(
    async (
      file: File,
      options?: { name?: string; description?: string },
    ): Promise<boolean> => {
      if (!kbId) return false;
      try {
        await knowledgeService.uploadDocument(kbId, file, options);
        await refresh(true);
        return true;
      } catch (err) {
        toast.error(
          err instanceof BizError ? err.message : "上传失败，请稍后重试",
        );
        return false;
      }
    },
    [kbId, refresh],
  );

  return {
    documents,
    total,
    page,
    pageSize,
    setPage,
    isLoading,
    error,
    refresh,
    uploadDocument,
    updateDocument: useCallback(
      (docId: string, input: KnowledgeDocumentUpdateInput) =>
        kbId
          ? refreshAfter(() =>
              knowledgeService.updateDocument(kbId, docId, input),
            ).then(async (ok) => {
              if (ok) toast.success("文档信息已更新");
              return ok;
            })
          : Promise.resolve(false),
      [kbId, refreshAfter],
    ),
    deleteDocument: useCallback(
      (docId: string) =>
        kbId
          ? refreshAfter(() => knowledgeService.deleteDocument(kbId, docId)).then(
              async (ok) => {
                if (ok) toast.success("删除请求已受理");
                return ok;
              },
            )
          : Promise.resolve(false),
      [kbId, refreshAfter],
    ),
    retryDocument: useCallback(
      (docId: string) =>
        kbId
          ? refreshAfter(() => knowledgeService.retryDocument(kbId, docId)).then(
              async (ok) => {
                if (ok) toast.success("已重新排队处理");
                return ok;
              },
            )
          : Promise.resolve(false),
      [kbId, refreshAfter],
    ),
    setDocumentEnabled: useCallback(
      (docId: string, enabled: boolean) =>
        kbId
          ? refreshAfter(() =>
              knowledgeService.setDocumentEnabled(kbId, docId, enabled),
            ).then(async (ok) => {
              if (ok) toast.success(enabled ? "文档已启用" : "文档已停用");
              return ok;
            })
          : Promise.resolve(false),
      [kbId, refreshAfter],
    ),
  };
}
