import { describe, expect, it } from "vitest";

import type { KnowledgeSource } from "@/services/types";
import {
  formatDateTime,
  formatFileSize,
  formatSourcePages,
} from "@/lib/format";

describe("formatFileSize（字节 → 人类可读大小）", () => {
  it("缺失/非法输入返回占位符", () => {
    expect(formatFileSize(null)).toBe("-");
    expect(formatFileSize(undefined)).toBe("-");
    expect(formatFileSize(Number.NaN)).toBe("-");
  });

  it("不足 1KB 直接输出字节", () => {
    expect(formatFileSize(0)).toBe("0 B");
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(1023)).toBe("1023 B");
  });

  it("1024 边界进位到 KB", () => {
    expect(formatFileSize(1024)).toBe("1.0 KB");
  });

  it("KB/MB/GB 逐级进位，保留一位小数", () => {
    expect(formatFileSize(1536)).toBe("1.5 KB");
    expect(formatFileSize(1024 * 1024)).toBe("1.0 MB");
    expect(formatFileSize(2.5 * 1024 * 1024)).toBe("2.5 MB");
    expect(formatFileSize(1024 ** 3)).toBe("1.0 GB");
  });

  it("≥100 的值取整不再带小数", () => {
    expect(formatFileSize(150 * 1024)).toBe("150 KB");
    expect(formatFileSize(200 * 1024 * 1024)).toBe("200 MB");
  });

  it("超过 TB 封顶不再进位", () => {
    // 1 PB 逐级除到底停在 TB 档
    expect(formatFileSize(1024 ** 5)).toBe("1024 TB");
  });
});

describe("formatDateTime（ISO → 本地 YYYY-MM-DD HH:mm）", () => {
  it("缺失输入返回占位符", () => {
    expect(formatDateTime(null)).toBe("-");
    expect(formatDateTime(undefined)).toBe("-");
    expect(formatDateTime("")).toBe("-");
  });

  it("非法时间串返回占位符", () => {
    expect(formatDateTime("not-a-date")).toBe("-");
  });

  it("本地时间分量往返一致（时区无关）", () => {
    // 用本地分量构造时刻再取 ISO，期望值即同一组本地分量——
    // 断言不依赖机器时区（CI 为 UTC、开发机为 +08:00 均成立）
    const date = new Date(2026, 2, 5, 9, 7);
    expect(formatDateTime(date.toISOString())).toBe("2026-03-05 09:07");
  });

  it("月/日/时/分不足两位补零，秒被截断不进位", () => {
    const date = new Date(2026, 0, 2, 3, 4, 59);
    expect(formatDateTime(date.toISOString())).toBe("2026-01-02 03:04");
  });
});

/** 只填 formatSourcePages 用到的字段，其余给类型占位 */
const makeSource = (pageStart: number | null, pageEnd: number | null) =>
  ({
    index: 0,
    kb_id: "kb",
    doc_id: "doc",
    doc_name: "doc.md",
    position: 0,
    content: "",
    page_start: pageStart,
    page_end: pageEnd,
    heading_path: [],
  }) as KnowledgeSource;

describe("formatSourcePages（0 起存储页码 → 展示页码）", () => {
  it("无页码信息返回空串", () => {
    expect(formatSourcePages(makeSource(null, null))).toBe("");
  });

  it("起始页缺失同样返回空串", () => {
    expect(formatSourcePages(makeSource(null, 3))).toBe("");
  });

  it("终止页缺失或与起始同页 → 单页，展示 +1", () => {
    expect(formatSourcePages(makeSource(0, null))).toBe("p.1");
    expect(formatSourcePages(makeSource(4, 4))).toBe("p.5");
  });

  it("跨页区间 → 起止各 +1", () => {
    expect(formatSourcePages(makeSource(0, 2))).toBe("p.1-3");
    expect(formatSourcePages(makeSource(9, 11))).toBe("p.10-12");
  });
});
