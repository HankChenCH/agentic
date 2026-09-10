import { describe, expect, it, vi } from "vitest";

import { isAttachmentRef } from "@/lib/attachment-url";

describe("isAttachmentRef（本域附件引用判定）", () => {
  it("绝对 http(s) URL 按 path 前缀判定，host 任意", () => {
    expect(isAttachmentRef("http://demo.example.com/agentic/attachments/id/a.png")).toBe(true);
    expect(isAttachmentRef("https://127.0.0.1:8000/agentic/attachments/id/a.png")).toBe(true);
    expect(isAttachmentRef("https://example.com/other/a.png")).toBe(false);
    expect(isAttachmentRef("https://example.com/agentic/attachments-impostor/a.png")).toBe(false);
  });

  it("裸相对引用直接比前缀（/api 反代前缀形态不算——后端按裸前缀识别）", () => {
    expect(isAttachmentRef("/agentic/attachments/id/a.png")).toBe(true);
    expect(isAttachmentRef("/agentic/attachments-x/a.png")).toBe(false);
    expect(isAttachmentRef("/api/agentic/attachments/id/a.png")).toBe(false);
    expect(isAttachmentRef("images/a.png")).toBe(false);
  });

  it("data URL 与任意串不作本域引用", () => {
    expect(isAttachmentRef("data:image/png;base64,QUJD")).toBe(false);
    expect(isAttachmentRef("")).toBe(false);
  });
});

// toFetchableUrl 依赖模块级 REST_BASE 的形态（绝对根 / 相对根），用
// doMock + 动态 import 按 case 切换（生产同源反代部署 REST_BASE=/api）
describe("toFetchableUrl（REST_BASE 拼可 fetch 地址）", () => {
  it("绝对引用原样返回；http(s) 根去尾斜杠后拼接", async () => {
    vi.resetModules();
    vi.doMock("@/lib/config", () => ({ REST_BASE: "http://127.0.0.1:8000" }));
    const { toFetchableUrl } = await import("@/lib/attachment-url");
    expect(
      toFetchableUrl("http://a.example.com/agentic/attachments/id/a.png"),
    ).toBe("http://a.example.com/agentic/attachments/id/a.png");
    expect(toFetchableUrl("/agentic/attachments/id/a.png")).toBe(
      "http://127.0.0.1:8000/agentic/attachments/id/a.png",
    );
  });

  it("相对根（/api）：拼页面 origin，保留反代前缀（new URL 相对 base 必抛，故字符串拼接）", async () => {
    vi.resetModules();
    vi.doMock("@/lib/config", () => ({ REST_BASE: "/api/" }));
    const { toFetchableUrl } = await import("@/lib/attachment-url");
    expect(toFetchableUrl("/agentic/attachments/id/a.png", "http://demo.test")).toBe(
      "http://demo.test/api/agentic/attachments/id/a.png",
    );
    // 非浏览器环境（pageOrigin 空串）退化为同源相对地址，仍可 fetch
    expect(toFetchableUrl("/agentic/attachments/id/a.png")).toBe(
      "/api/agentic/attachments/id/a.png",
    );
  });
});
