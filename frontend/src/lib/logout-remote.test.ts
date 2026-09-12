import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { logoutRemote } from "@/lib/logout-remote";
import { useAuthStore } from "@/stores/auth-store";

const user = { id: "u-1", username: "hank", nickname: "Hank" };

const resetStore = () =>
  useAuthStore.setState({
    token: null,
    refreshToken: null,
    expiresAt: null,
    refreshExpiresAt: null,
    user: null,
  });

describe("logoutRemote（登出撤销通道）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    resetStore();
    fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("已登录：裸 fetch POST /auth/logout，Bearer + refresh_token，keepalive", () => {
    useAuthStore.setState({
      token: "t-1",
      refreshToken: "r-1",
      expiresAt: "2026-01-01T12:30:00Z",
      refreshExpiresAt: "2026-01-08T12:30:00Z",
      user,
    });

    logoutRemote();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/auth/logout");
    expect(init.method).toBe("POST");
    expect(init.keepalive).toBe(true);
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer t-1");
    expect(JSON.parse(String(init.body))).toEqual({ refresh_token: "r-1" });
  });

  it("未登录（无 token/refreshToken）→ 不发请求", () => {
    logoutRemote();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("网络异常 → 静默吞掉不抛出，不阻塞登出流程", () => {
    useAuthStore.setState({ token: "t-1", refreshToken: "r-1", user });
    fetchMock.mockRejectedValue(new TypeError("network down"));

    expect(() => logoutRemote()).not.toThrow();
  });

  it("HTTP 401（access 已过期）→ 不抛出，不触发任何后续动作", async () => {
    useAuthStore.setState({ token: "t-stale", refreshToken: "r-1", user });
    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }));

    expect(() => logoutRemote()).not.toThrow();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock.mock.calls[0][1].body).toBe(JSON.stringify({ refresh_token: "r-1" }));
  });
});
