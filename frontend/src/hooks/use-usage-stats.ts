import { useCallback, useEffect, useMemo, useState } from "react";

import { statsService } from "@/services/stats-service";
import type {
  UsageDailyResult,
  UsageRecordListResult,
  UsageSummary,
} from "@/services/types";

// ===========================================================================
// Hook —— 个人用量统计：时间范围 + 汇总/序列/流水的并行拉取
//
// 与 use-knowledge-list 同一套分层约定：本 hook 只管 React 状态与编排，
// 网络全部走 @/services/stats-service。范围切换重置回第一页。
// ===========================================================================

/** 预置时间范围（天数），[start, end) 半开区间按 UTC 日对齐 */
export type UsageRangeKey = "7d" | "30d";

const RANGE_DAYS: Record<UsageRangeKey, number> = { "7d": 7, "30d": 30 };

/** 范围键 → 查询参数（UTC 整日对齐：end = 明日 0 点，start = end - N 天） */
export function usageRangeParams(rangeKey: UsageRangeKey): {
  start: string;
  end: string;
} {
  const days = RANGE_DAYS[rangeKey];
  const end = new Date();
  end.setUTCHours(0, 0, 0, 0);
  end.setUTCDate(end.getUTCDate() + 1);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - days);
  return { start: start.toISOString(), end: end.toISOString() };
}

/** useUsageStats 的返回值 */
export interface UseUsageStatsResult {
  summary: UsageSummary | null;
  daily: UsageDailyResult | null;
  records: UsageRecordListResult | null;
  page: number;
  setPage: (page: number) => void;
  isLoading: boolean;
  error: Error | null;
  refresh: () => void;
}

export function useUsageStats(rangeKey: UsageRangeKey): UseUsageStatsResult {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [daily, setDaily] = useState<UsageDailyResult | null>(null);
  const [records, setRecords] = useState<UsageRecordListResult | null>(null);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  // refresh 的句柄：值本身无意义，仅用于触发 effect 重跑
  const [reloadTick, setReloadTick] = useState(0);

  const range = useMemo(() => usageRangeParams(rangeKey), [rangeKey]);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);

    const fetchAll = async () => {
      try {
        const [summaryResult, dailyResult, recordsResult] = await Promise.all([
          statsService.getUsageSummary(range),
          statsService.getUsageDaily(range),
          statsService.getUsageRecords(range, page),
        ]);
        if (cancelled) return;
        setSummary(summaryResult);
        setDaily(dailyResult);
        setRecords(recordsResult);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    };
    void fetchAll();

    return () => {
      cancelled = true;
    };
  }, [range, page, reloadTick]);

  const refresh = useCallback(() => setReloadTick((t) => t + 1), []);

  // 范围切换回到第一页（换范围后的旧页码没有意义）
  useEffect(() => {
    setPage(1);
  }, [rangeKey]);

  return { summary, daily, records, page, setPage, isLoading, error, refresh };
}
