// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authenticatedFetch } from "@/lib/authenticated-fetch";
import { refreshSession } from "@/lib/token-refresh";
import { useAuthStore } from "@/stores/auth-store";

vi.mock("@/lib/token-refresh", () => ({ refreshSession: vi.fn() }));
const refreshMock = vi.mocked(refreshSession);

const user = { id: "u-1", username: "hank", nickname: "Hank" };
const resetStore = () =>
  useAuthStore.setState({
    token: null,
    refreshToken: null,
    expiresAt: null,
    refreshExpiresAt: null,
    user: null,
  });

const jsonResponse = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const seedLogin = (token: string) =>
  useAuthStore.setState({
    token,
    refreshToken: "r",
    expiresAt: null,
    refreshExpiresAt: null,
    user,
  });

describe("authenticatedFetch（SSE 端点认证与错误包装）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    resetStore();
    refreshMock.mockReset();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("登录态：请求实时读 store 注入 Bearer 头", async () => {
    seedLogin("t-live");
    fetchMock.mockResolvedValue(jsonResponse(200));

    await authenticatedFetch("/agentic/run", { method: "POST" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer t-live");
  });

  it("网络异常翻译为可读提示（AbortError 原样透传不打扰归类）", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(authenticatedFetch("/agentic/run")).rejects.toThrow(
      "网络连接中断，请检查网络后重试",
    );

    const abort = new DOMException("The operation was aborted.", "AbortError");
    fetchMock.mockRejectedValue(abort);
    await expect(authenticatedFetch("/agentic/run")).rejects.toBe(abort);
  });

  it("401：刷新成功 → 换新 token 重试一次并透传流式响应", async () => {
    seedLogin("t-stale");
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401))
      .mockResolvedValueOnce(new Response("stream-body", { status: 200 }));
    refreshMock.mockImplementation(async () => {
      seedLogin("t-new");
      return true;
    });

    const res = await authenticatedFetch("/agentic/run", { method: "POST" });
    expect(res.status).toBe(200);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, retryInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new Headers(retryInit.headers).get("Authorization")).toBe(
      "Bearer t-new",
    );
  });

  it("401：刷新失败 → 登出清会话并跳 /login?next=…，返回原 401 响应", async () => {
    seedLogin("t-stale");
    const assign = vi.fn();
    const original = window.location;
    vi.stubGlobal("location", {
      ...original,
      pathname: "/",
      assign,
    } as typeof window.location);
    fetchMock.mockResolvedValue(jsonResponse(401));
    refreshMock.mockResolvedValue(false);

    const res = await authenticatedFetch("/agentic/run");

    expect(res.status).toBe(401);
    expect(useAuthStore.getState().token).toBeNull();
    expect(assign).toHaveBeenCalledWith(`/login?next=${encodeURIComponent("/")}`);
  });

  it("已停在 /login 页时不重复跳转（防回跳循环）", async () => {
    seedLogin("t-stale");
    window.history.replaceState(null, "", "/login");
    const assign = vi.fn();
    vi.stubGlobal("location", {
      ...window.location,
      pathname: "/login",
      assign,
    } as typeof window.location);
    fetchMock.mockResolvedValue(jsonResponse(401));
    refreshMock.mockResolvedValue(false);

    await authenticatedFetch("/agentic/run");
    expect(assign).not.toHaveBeenCalled();
  });

  it("其余非 2xx：拆信封取 error_message 抛干净 Error", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(409, { error_code: 4003, error_message: "会话已被删除" }),
    );
    await expect(authenticatedFetch("/agentic/run")).rejects.toThrow(
      "会话已被删除",
    );
  });

  it("5xx 信封缺失（网关 HTML 错误页）→ 状态码兜底文案", async () => {
    fetchMock.mockResolvedValue(new Response("<html>502</html>", { status: 502 }));
    await expect(authenticatedFetch("/agentic/run")).rejects.toThrow(
      "服务器开小差了，请稍后重试",
    );
  });

  it("4xx 非 JSON 响应体 → 通用失败文案带状态码", async () => {
    fetchMock.mockResolvedValue(new Response("nope", { status: 403 }));
    await expect(authenticatedFetch("/agentic/run")).rejects.toThrow(
      "请求失败（HTTP 403）",
    );
  });

  it("2xx 响应原样透传（不读 body、不包装）", async () => {
    const stream = new Response("event: RUN_FINISHED", { status: 200 });
    fetchMock.mockResolvedValue(stream);
    await expect(authenticatedFetch("/agentic/run")).resolves.toBe(stream);
  });
});
