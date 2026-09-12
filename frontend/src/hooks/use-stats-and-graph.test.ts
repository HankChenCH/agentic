// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { usageRangeParams, useUsageStats } from "@/hooks/use-usage-stats";
import { useMemoryGraph } from "@/hooks/use-memory-graph";
import { statsService } from "@/services/stats-service";
import { memoryService } from "@/services/memory-service";
import { toast } from "sonner";

vi.mock("@/services/stats-service", () => ({
  statsService: {
    getUsageSummary: vi.fn(),
    getUsageDaily: vi.fn(),
    getUsageRecords: vi.fn(),
  },
}));
vi.mock("@/services/memory-service", () => ({
  memoryService: { graphSnapshot: vi.fn() },
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const summaryMock = vi.mocked(statsService.getUsageSummary);
const dailyMock = vi.mocked(statsService.getUsageDaily);
const recordsMock = vi.mocked(statsService.getUsageRecords);
const snapshotMock = vi.mocked(memoryService.graphSnapshot);
const toastMock = vi.mocked(toast);

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("usageRangeParams（UTC 整日对齐的半开区间）", () => {
  it("end = 明日 0 点 UTC，start = end - N 天", () => {
    vi.useFakeTimers({ now: new Date("2026-01-10T15:30:00Z") });
    try {
      const { start, end } = usageRangeParams("7d");
      expect(end).toBe("2026-01-11T00:00:00.000Z");
      expect(start).toBe("2026-01-04T00:00:00.000Z");

      const month = usageRangeParams("30d");
      expect(month.start).toBe("2025-12-12T00:00:00.000Z");
      expect(month.end).toBe("2026-01-11T00:00:00.000Z");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("useUsageStats（三接口并行拉取）", () => {
  it("并行拉取汇总/序列/流水，全部落地后 loading 复位", async () => {
    summaryMock.mockResolvedValue({ total_tokens: 1 } as never);
    dailyMock.mockResolvedValue({ items: [] } as never);
    recordsMock.mockResolvedValue({ items: [], total: 0 } as never);

    const { result } = renderHook(() => useUsageStats("7d"));
    expect(result.current.isLoading).toBe(true);
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(summaryMock).toHaveBeenCalledTimes(1);
    expect(dailyMock).toHaveBeenCalledTimes(1);
    expect(recordsMock).toHaveBeenCalledWith(expect.objectContaining({ start: expect.any(String) }), 1);
    expect(result.current.summary).toEqual({ total_tokens: 1 });
    expect(result.current.page).toBe(1);
  });

  it("任一接口失败：error 记录、loading 复位", async () => {
    summaryMock.mockRejectedValue(new Error("stats down"));
    dailyMock.mockResolvedValue({ items: [] } as never);
    recordsMock.mockResolvedValue({ items: [], total: 0 } as never);

    const { result } = renderHook(() => useUsageStats("30d"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error?.message).toBe("stats down");
    expect(result.current.summary).toBeNull();
  });

  it("refresh 触发整体重拉", async () => {
    summaryMock.mockResolvedValue({ total_tokens: 1 } as never);
    dailyMock.mockResolvedValue({ items: [] } as never);
    recordsMock.mockResolvedValue({ items: [], total: 0 } as never);

    const { result } = renderHook(() => useUsageStats("7d"));
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    await act(async () => {
      result.current.setPage(2);
    });
    await waitFor(() => expect(recordsMock).toHaveBeenCalledTimes(2));
    expect(recordsMock).toHaveBeenLastCalledWith(expect.anything(), 2);
    // effect 依赖 page：翻页三接口整体重拉（同一 range）
    expect(summaryMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      result.current.refresh();
    });
    // 初始 1 + 翻页 1 + refresh 1
    await waitFor(() => expect(summaryMock).toHaveBeenCalledTimes(3));
  });
});

describe("useMemoryGraph（快照加载）", () => {
  const snapshot = {
    nodes: [],
    edges: [],
    at: null,
    generatedAt: "2026-01-01T00:00:00Z",
    stats: { entityNodes: 0, episodeNodes: 0, statementEdges: 0, episodeLinkEdges: 0 },
  };

  it("挂载即拉快照；at/limit 透传", async () => {
    snapshotMock.mockResolvedValue(snapshot as never);
    const { result } = renderHook(() => useMemoryGraph({ at: "2026-01-01", limit: 50 }));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(snapshotMock).toHaveBeenCalledWith({ at: "2026-01-01", limit: 50 });
    expect(result.current.snapshot).toEqual(snapshot);
  });

  it("失败：error + toast；静默刷新不闪 loading", async () => {
    snapshotMock.mockRejectedValueOnce(new Error("graph down"));
    const { result } = renderHook(() => useMemoryGraph());
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error?.message).toBe("graph down");
    expect(toastMock.error).toHaveBeenCalledWith("记忆图谱加载失败，请稍后重试");

    // 静默刷新成功：不再置 loading、error 清空
    snapshotMock.mockResolvedValue(snapshot as never);
    await act(async () => {
      await result.current.refresh(true);
    });
    expect(result.current.error).toBeNull();
    expect(result.current.snapshot).toEqual(snapshot);
  });
});
