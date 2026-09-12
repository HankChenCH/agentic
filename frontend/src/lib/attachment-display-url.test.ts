import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearDisplayUrlCacheForTest,
  resolveDisplayUrl,
} from "@/lib/attachment-display-url";
import { attachmentService } from "@/services/attachment-service";
import { useAuthStore } from "@/stores/auth-store";

vi.mock("@/services/attachment-service", () => ({
  attachmentService: { getDisplayUrl: vi.fn() },
}));

const getDisplayUrlMock = vi.mocked(attachmentService.getDisplayUrl);

const REF = "/agentic/attachments/att-1/cat.png";
const SIGNED = "https://rustfs.local/signed/cat.png?sig=x";
const user = { id: "u-1", username: "hank", nickname: "Hank" };
const BASE_NOW = Date.parse("2026-01-01T12:00:00Z");

describe("resolveDisplayUrl（预签名换址 + TTL 缓存 + blob 降级）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers({ now: BASE_NOW });
    clearDisplayUrlCacheForTest();
    getDisplayUrlMock.mockReset();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    useAuthStore.setState({
      token: "t-1",
      refreshToken: "r",
      expiresAt: null,
      refreshExpiresAt: null,
      user,
    });
  });

  afterEach(() => {
    delete (URL as unknown as Record<string, unknown>).createObjectURL;
    vi.unstubAllGlobals();
    vi.useRealTimers();
    useAuthStore.setState({
      token: null,
      refreshToken: null,
      expiresAt: null,
      refreshExpiresAt: null,
      user: null,
    });
  });

  it("首次换签：经 /agentic/attachments/url 换预签名地址，写入缓存", async () => {
    getDisplayUrlMock.mockResolvedValue(SIGNED);

    await expect(resolveDisplayUrl(REF)).resolves.toBe(SIGNED);
    expect(getDisplayUrlMock).toHaveBeenCalledTimes(1);
    expect(getDisplayUrlMock).toHaveBeenCalledWith(REF);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("缓存未过期（< 半程 TTL）命中：不再发起换签请求", async () => {
    getDisplayUrlMock.mockResolvedValue(SIGNED);
    await resolveDisplayUrl(REF);

    vi.setSystemTime(BASE_NOW + 4 * 60 * 1000);
    await expect(resolveDisplayUrl(REF)).resolves.toBe(SIGNED);
    expect(getDisplayUrlMock).toHaveBeenCalledTimes(1);
  });

  it("到达半程 TTL（5 分钟）过期重签", async () => {
    getDisplayUrlMock
      .mockResolvedValueOnce(SIGNED)
      .mockResolvedValueOnce(SIGNED + "&v=2");
    await resolveDisplayUrl(REF);

    vi.setSystemTime(BASE_NOW + 5 * 60 * 1000);
    await expect(resolveDisplayUrl(REF)).resolves.toBe(SIGNED + "&v=2");
    expect(getDisplayUrlMock).toHaveBeenCalledTimes(2);
  });

  it("签名地址不可用（url=null）→ 鉴权 fetch 转 blob 降级，拼可 fetch 地址", async () => {
    getDisplayUrlMock.mockResolvedValue(null);
    const createObjectURL = vi.fn(() => "blob:cat");
    (URL as unknown as { createObjectURL: unknown }).createObjectURL =
      createObjectURL;
    const blobBytes = new Blob(["fake-image"]);
    fetchMock.mockResolvedValue({ ok: true, blob: async () => blobBytes });

    await expect(resolveDisplayUrl(REF)).resolves.toBe("blob:cat");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    // REST_BASE 兜底 http://127.0.0.1:8000（node 环境无 import.meta 注入）
    expect(url).toBe(`http://127.0.0.1:8000${REF}`);
    expect((init.headers as Record<string, string>).Authorization).toBe(
      "Bearer t-1",
    );
    expect(createObjectURL).toHaveBeenCalledWith(blobBytes);
  });

  it("blob 降级结果不参与 TTL 过期（复用到页面卸载）", async () => {
    getDisplayUrlMock.mockResolvedValue(null);
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(
      () => "blob:cat",
    );
    fetchMock.mockResolvedValue({ ok: true, blob: async () => new Blob() });
    await resolveDisplayUrl(REF);

    vi.setSystemTime(BASE_NOW + 3600 * 1000); // 1h 后仍命中
    await expect(resolveDisplayUrl(REF)).resolves.toBe("blob:cat");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getDisplayUrlMock).toHaveBeenCalledTimes(1);
  });

  it("无 token（未登录残留请求）→ blob 降级不带 Authorization 头", async () => {
    getDisplayUrlMock.mockResolvedValue(null);
    useAuthStore.setState({
      token: null,
      refreshToken: null,
      expiresAt: null,
      refreshExpiresAt: null,
      user: null,
    });
    (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(
      () => "blob:cat",
    );
    fetchMock.mockResolvedValue({ ok: true, blob: async () => new Blob() });

    await resolveDisplayUrl(REF);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.headers).toBeUndefined();
  });

  it("换签失败：返回 undefined 且不降级 blob、不写缓存", async () => {
    getDisplayUrlMock.mockRejectedValue(new Error("boom"));

    await expect(resolveDisplayUrl(REF)).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();

    // 不写缓存：下次调用重新发起换签
    getDisplayUrlMock.mockResolvedValue(SIGNED);
    await expect(resolveDisplayUrl(REF)).resolves.toBe(SIGNED);
    expect(getDisplayUrlMock).toHaveBeenCalledTimes(2);
  });

  it("blob 降级 fetch 也失败（非 ok / 网络错）→ undefined 不写缓存", async () => {
    getDisplayUrlMock.mockResolvedValue(null);
    fetchMock.mockResolvedValue({ ok: false, blob: async () => new Blob() });

    await expect(resolveDisplayUrl(REF)).resolves.toBeUndefined();

    fetchMock.mockRejectedValue(new TypeError("down"));
    await expect(resolveDisplayUrl(REF)).resolves.toBeUndefined();
    expect(getDisplayUrlMock).toHaveBeenCalledTimes(2); // 两次都未写缓存
  });
});
