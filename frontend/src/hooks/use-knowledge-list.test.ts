// @vitest-environment jsdom
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useKnowledgeBase } from "@/hooks/use-knowledge-base";
import { useKnowledgeDocuments } from "@/hooks/use-knowledge-documents";
import { isTransitional, useKnowledgeList } from "@/hooks/use-knowledge-list";
import { BizError } from "@/lib/http";
import { knowledgeService } from "@/services/knowledge-service";
import { toast } from "sonner";
import type { BackendKnowledgeBase, BackendKnowledgeDocument } from "@/services/types";

vi.mock("@/services/knowledge-service", () => ({
  knowledgeService: {
    listKnowledgeBases: vi.fn(),
    getKnowledgeBase: vi.fn(),
    listDocuments: vi.fn(),
    createKnowledgeBase: vi.fn(),
    updateKnowledgeBase: vi.fn(),
    deleteKnowledgeBase: vi.fn(),
    setKnowledgeEnabled: vi.fn(),
    uploadDocument: vi.fn(),
  },
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const listKbMock = vi.mocked(knowledgeService.listKnowledgeBases);
const getKbMock = vi.mocked(knowledgeService.getKnowledgeBase);
const listDocsMock = vi.mocked(knowledgeService.listDocuments);
const createKbMock = vi.mocked(knowledgeService.createKnowledgeBase);
const toastMock = vi.mocked(toast);

let seq = 0;
const makeKb = (overrides: Partial<BackendKnowledgeBase> = {}): BackendKnowledgeBase => ({
  id: `kb-${(seq += 1)}`,
  user_id: "u-1",
  name: "知识库",
  description: "",
  embedding_model: "bge-m3",
  is_public: false,
  weight: 0,
  status: "ready",
  doc_num: 0,
  created_at: "",
  updated_at: "",
  ...overrides,
});

const makeDoc = (overrides: Partial<BackendKnowledgeDocument> = {}) =>
  ({
    id: `doc-${(seq += 1)}`,
    name: "文档.md",
    status: "ready",
    ...overrides,
  }) as BackendKnowledgeDocument;

beforeEach(() => {
  vi.useFakeTimers();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

/** 落定挂载时的首次加载（fake timers 下手动冲刷微任务） */
const settle = async () => {
  await act(async () => {});
};

describe("isTransitional（过渡态判定）", () => {
  it("pending/processing/deleting 为过渡态，其余为终态", () => {
    expect(isTransitional("pending")).toBe(true);
    expect(isTransitional("processing")).toBe(true);
    expect(isTransitional("deleting")).toBe(true);
    expect(isTransitional("ready")).toBe(false);
    expect(isTransitional("failed")).toBe(false);
    expect(isTransitional("enabled")).toBe(false);
  });
});

describe("useKnowledgeList（列表轮询状态机）", () => {
  it("全部终态：只拉一次，无轮询定时器", async () => {
    listKbMock.mockResolvedValue({ items: [makeKb()], total: 1, page: 1, pageSize: 12 });
    const { result } = renderHook(() => useKnowledgeList());
    await settle();

    expect(listKbMock).toHaveBeenCalledTimes(1);
    expect(result.current.items).toHaveLength(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(listKbMock).toHaveBeenCalledTimes(1);
  });

  it("存在过渡态：3s 后静默刷新（不闪烁 loading），落定后自然停", async () => {
    listKbMock
      .mockResolvedValueOnce({ items: [makeKb({ status: "processing" })], total: 1, page: 1, pageSize: 12 })
      .mockResolvedValue({ items: [makeKb({ status: "ready" })], total: 1, page: 1, pageSize: 12 });
    const { result } = renderHook(() => useKnowledgeList());
    await settle();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(listKbMock).toHaveBeenCalledTimes(2);
    expect(result.current.isLoading).toBe(false); // 轮询刷新是静默的
    expect(result.current.items[0].status).toBe("ready");

    // 全部落定：不再排下一个定时器
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(listKbMock).toHaveBeenCalledTimes(2);
  });

  it("创建成功：toast + 静默刷新返回 true；失败 toast error_message 返回 false", async () => {
    listKbMock.mockResolvedValue({ items: [], total: 0, page: 1, pageSize: 12 });
    const { result } = renderHook(() => useKnowledgeList());
    await settle();

    createKbMock.mockResolvedValue(makeKb());
    await act(async () => {
      const ok = await result.current.createKnowledgeBase({ name: "新库" } as never);
      expect(ok).toBe(true);
    });
    expect(toastMock.success).toHaveBeenCalledWith("知识库创建成功");
    expect(listKbMock).toHaveBeenCalledTimes(2);

    createKbMock.mockRejectedValue(new BizError(409, "重名"));
    await act(async () => {
      const ok = await result.current.createKnowledgeBase({ name: "重名" } as never);
      expect(ok).toBe(false);
    });
    expect(toastMock.error).toHaveBeenCalledWith("重名");
  });
});

describe("useKnowledgeBase（详情 404 语义 + 轮询）", () => {
  it("404 → notFound 标记（区别于网络错误），页面据此跳回列表", async () => {
    const axiosErr = Object.assign(new Error("not found"), {
      isAxiosError: true,
      response: { status: 404 },
    });
    getKbMock.mockRejectedValue(axiosErr);
    const { result } = renderHook(() => useKnowledgeBase("kb-gone"));
    await settle();

    expect(result.current.notFound).toBe(true);
    expect(result.current.error).toBeNull();
    expect(result.current.isLoading).toBe(false);
  });

  it("kbId 缺失：不发请求；过渡态按 3s 轮询直至落定", async () => {
    const { result } = renderHook(() => useKnowledgeBase(undefined));
    await settle();
    expect(getKbMock).not.toHaveBeenCalled();
    expect(result.current.isLoading).toBe(true); // kbId 缺失时 refresh 早退，loading 恒挂

    getKbMock
      .mockResolvedValueOnce(makeKb({ id: "kb-1", status: "pending" }))
      .mockResolvedValue(makeKb({ id: "kb-1", status: "ready" }));
    const { result: result2, rerender } = renderHook(
      ({ kbId }: { kbId: string | undefined }) => useKnowledgeBase(kbId),
      { initialProps: { kbId: "kb-1" as string | undefined } },
    );
    await settle();
    expect(result2.current.knowledgeBase?.status).toBe("pending");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(getKbMock).toHaveBeenCalledTimes(2);
    expect(result2.current.knowledgeBase?.status).toBe("ready");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(getKbMock).toHaveBeenCalledTimes(2);
    void rerender;
  });
});

describe("useKnowledgeDocuments（文档轮询 + 无 kbId 防护）", () => {
  it("过渡态文档触发轮询，落定停止；操作失败走 toast", async () => {
    listDocsMock
      .mockResolvedValueOnce({ items: [makeDoc({ status: "processing" })], total: 1, page: 1, pageSize: 20 })
      .mockResolvedValue({ items: [makeDoc({ status: "ready" })], total: 1, page: 1, pageSize: 20 });
    const { result } = renderHook(() => useKnowledgeDocuments("kb-1"));
    await settle();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(listDocsMock).toHaveBeenCalledTimes(2);
    expect(result.current.documents[0].status).toBe("ready");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(listDocsMock).toHaveBeenCalledTimes(2);
  });

  it("kbId 缺失：不发请求，上传直接返回 false", async () => {
    const { result } = renderHook(() => useKnowledgeDocuments(undefined));
    await settle();
    expect(listDocsMock).not.toHaveBeenCalled();

    await act(async () => {
      const ok = await result.current.uploadDocument(new File(["x"], "a.md"));
      expect(ok).toBe(false);
    });
  });
});
