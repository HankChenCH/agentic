import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { BizError } from "@/lib/http";
import { knowledgeService } from "@/services/knowledge-service";
import type { BackendDocumentSegment } from "@/services/types";

// ===========================================================================
// Hook —— 文档分段列表：分页 + 关键词搜索（防抖）+ 分段写操作
//
// 与 use-knowledge-documents 同构，数据源换成某文档下的分段。分段集合在
// 文档就绪后是静态的（仅管理操作改变），无需状态轮询；写操作成功后静默
// 刷新当前页。
// ===========================================================================

const KEYWORD_DEBOUNCE_MS = 300;

export interface UseKnowledgeSegmentsResult {
  segments: BackendDocumentSegment[];
  total: number;
  page: number;
  pageSize: number;
  setPage: (page: number) => void;
  /** 搜索框输入值（未防抖，受控绑定） */
  keyword: string;
  /** 设置关键词并回到第 1 页（防抖后生效） */
  setKeyword: (keyword: string) => void;
  isLoading: boolean;
  error: Error | null;
  refresh: (silent?: boolean) => Promise<void>;
  createSegment: (content: string) => Promise<boolean>;
  updateSegment: (segmentId: string, content: string) => Promise<boolean>;
  deleteSegment: (segmentId: string) => Promise<boolean>;
  setSegmentEnabled: (segmentId: string, enabled: boolean) => Promise<boolean>;
}

export function useKnowledgeSegments(
  kbId: string | undefined,
  docId: string | undefined,
  pageSize = 10,
): UseKnowledgeSegmentsResult {
  const [segments, setSegments] = useState<BackendDocumentSegment[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keyword, setKeywordState] = useState("");
  const [appliedKeyword, setAppliedKeyword] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  // 关键词防抖：输入停顿后才真正触发查询（keyword 变化已同步回到第 1 页）
  useEffect(() => {
    const timer = setTimeout(() => setAppliedKeyword(keyword.trim()), KEYWORD_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [keyword]);

  const refresh = useCallback(
    async (silent = false) => {
      if (!kbId || !docId) return;
      if (!silent) setIsLoading(true);
      setError(null);
      try {
        const result = await knowledgeService.listSegments(
          kbId,
          docId,
          page,
          pageSize,
          appliedKeyword || undefined,
        );
        setSegments(result.items ?? []);
        setTotal(result.total);
      } catch (err) {
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (!silent) setIsLoading(false);
      }
    },
    [kbId, docId, page, pageSize, appliedKeyword],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setKeyword = useCallback((next: string) => {
    setKeywordState(next);
    setPage(1);
  }, []);

  const refreshAfter = useCallback(
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
    segments,
    total,
    page,
    pageSize,
    setPage,
    keyword,
    setKeyword,
    isLoading,
    error,
    refresh,
    createSegment: useCallback(
      (content: string) =>
        kbId && docId
          ? refreshAfter(
              () => knowledgeService.createSegment(kbId, docId, { content }),
              "分段已新增",
            )
          : Promise.resolve(false),
      [kbId, docId, refreshAfter],
    ),
    updateSegment: useCallback(
      (segmentId: string, content: string) =>
        kbId && docId
          ? refreshAfter(
              () => knowledgeService.updateSegment(kbId, docId, segmentId, { content }),
              "分段已更新，正在重新嵌入",
            )
          : Promise.resolve(false),
      [kbId, docId, refreshAfter],
    ),
    deleteSegment: useCallback(
      (segmentId: string) =>
        kbId && docId
          ? refreshAfter(
              () => knowledgeService.deleteSegment(kbId, docId, segmentId),
              "分段已删除",
            )
          : Promise.resolve(false),
      [kbId, docId, refreshAfter],
    ),
    setSegmentEnabled: useCallback(
      (segmentId: string, enabled: boolean) =>
        kbId && docId
          ? refreshAfter(
              () => knowledgeService.setSegmentEnabled(kbId, docId, segmentId, enabled),
              enabled ? "分段已启用" : "分段已停用，不再参与召回",
            )
          : Promise.resolve(false),
      [kbId, docId, refreshAfter],
    ),
  };
}
