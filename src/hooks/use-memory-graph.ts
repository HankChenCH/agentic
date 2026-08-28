import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { BizError } from "@/lib/http";
import { memoryService } from "@/services/memory-service";
import type { MemoryGraphSnapshot } from "@/services/memory-service";

/**
 * useMemoryGraph —— 记忆图谱快照加载。
 *
 * 与 use-knowledge-list 同一套分层约定：hook 只管 React 状态，网络走
 * services/memory-service；加载失败用 sonner toast 展示信封 error_message。
 * 无轮询需求：快照是惰性只读投影，用户手动刷新即可。
 */
export interface UseMemoryGraphResult {
  snapshot: MemoryGraphSnapshot | null;
  isLoading: boolean;
  error: Error | null;
  refresh: (silent?: boolean) => Promise<void>;
}

export function useMemoryGraph(options: {
  at?: string | null;
  limit?: number;
} = {}): UseMemoryGraphResult {
  const { at = null, limit } = options;
  const [snapshot, setSnapshot] = useState<MemoryGraphSnapshot | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const refresh = useCallback(
    async (silent = false) => {
      if (!silent) setIsLoading(true);
      setError(null);
      try {
        const result = await memoryService.graphSnapshot({ at, limit });
        setSnapshot(result);
      } catch (err) {
        const wrapped = err instanceof Error ? err : new Error(String(err));
        setError(wrapped);
        toast.error(
          err instanceof BizError ? err.message : "记忆图谱加载失败，请稍后重试",
        );
      } finally {
        if (!silent) setIsLoading(false);
      }
    },
    [at, limit],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { snapshot, isLoading, error, refresh };
}
