import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { refreshSession, shouldRefresh, startProactiveRefresh } from "@/lib/token-refresh";
import { useAuthStore } from "@/stores/auth-store";

describe("shouldRefresh（access token 临期判定）", () => {
  const now = Date.parse("2026-01-01T12:00:00Z");
  const threshold = 5 * 60 * 1000;

  it("剩余寿命不足阈值 → 需要刷新", () => {
    const exp = new Date(now + 4 * 60 * 1000).toISOString();
    expect(shouldRefresh(exp, now)).toBe(true);
  });

  it("刚好压线（等于阈值）→ 需要刷新", () => {
    const exp = new Date(now + threshold).toISOString();
    expect(shouldRefresh(exp, now)).toBe(true);
  });

  it("剩余寿命充足 → 不刷新", () => {
    const exp = new Date(now + 25 * 60 * 1000).toISOString();
    expect(shouldRefresh(exp, now)).toBe(false);
  });

  it("已过期 → 需要刷新", () => {
    const exp = new Date(now - 1000).toISOString();
    expect(shouldRefresh(exp, now)).toBe(true);
  });

  it("缺失/非法时间戳（旧持久化会话）→ 不主动刷，交由 401 兜底", () => {
    expect(shouldRefresh(null, now)).toBe(false);
    expect(shouldRefresh(undefined, now)).toBe(false);
    expect(shouldRefresh("not-a-date", now)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// refreshSession：裸 fetch 单飞刷新通道
// ---------------------------------------------------------------------------

const user = { id: "u-1", username: "hank", nickname: "Hank" };

const seedSession = () =>
  useAuthStore.setState({
    token: "t-old",
    refreshToken: "r-old",
    expiresAt: "2026-01-01T12:30:00Z",
    refreshExpiresAt: "2026-01-08T12:30:00Z",
    user,
  });

const refreshedPayload = {
  token: "t-new",
  refresh_token: "r-new",
  expires_at: "2026-01-01T13:30:00Z",
  refresh_expires_at: "2026-01-09T13:30:00Z",
  user,
};

/** 构造 /auth/refresh 的成功响应 */
const okRefresh = () =>
  new Response(JSON.stringify({ error_code: 0, error_message: "", response: refreshedPayload }), {
    status: 200,
  });

const resetStore = () =>
  useAuthStore.setState({
    token: null,
    refreshToken: null,
    expiresAt: null,
    refreshExpiresAt: null,
    user: null,
  });

describe("refreshSession（单飞刷新）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    resetStore();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("无 refresh token（未登录）→ false 且不发请求", async () => {
    await expect(refreshSession()).resolves.toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("成功：裸 fetch 直连 /auth/refresh，签发对覆写会话", async () => {
    seedSession();
    fetchMock.mockReturnValue(okRefresh());

    await expect(refreshSession()).resolves.toBe(true);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/auth/refresh");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ refresh_token: "r-old" });

    const s = useAuthStore.getState();
    expect(s.token).toBe("t-new");
    expect(s.refreshToken).toBe("r-new");
  });

  it("HTTP 非 2xx → false，会话保持原样", async () => {
    seedSession();
    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }));

    await expect(refreshSession()).resolves.toBe(false);
    expect(useAuthStore.getState().token).toBe("t-old");
  });

  it("信封 error_code !== 0 → false", async () => {
    seedSession();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error_code: 5001, error_message: "x", response: null }), { status: 200 }),
    );
    await expect(refreshSession()).resolves.toBe(false);
  });

  it("响应缺 refresh_token（旋转契约破坏）→ false", async () => {
    seedSession();
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ error_code: 0, error_message: "", response: { ...refreshedPayload, refresh_token: undefined } }),
        { status: 200 },
      ),
    );
    await expect(refreshSession()).resolves.toBe(false);
  });

  it("网络异常 → false 不抛出", async () => {
    seedSession();
    fetchMock.mockRejectedValue(new TypeError("network down"));
    await expect(refreshSession()).resolves.toBe(false);
  });

  it("单飞：挂起期间并发多次只发一次请求，共享同一结果", async () => {
    seedSession();
    let resolveFetch!: (v: Response) => void;
    fetchMock.mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );

    const first = refreshSession();
    const second = refreshSession();
    const third = refreshSession();
    expect(fetchMock).toHaveBeenCalledTimes(1); // 后两次等同一 promise

    resolveFetch(okRefresh());
    await expect(Promise.all([first, second, third])).resolves.toEqual([true, true, true]);
  });

  it("单飞复位：一次刷新落地后，再次调用发起新请求", async () => {
    seedSession();
    fetchMock.mockReturnValue(okRefresh());

    await refreshSession();
    await refreshSession();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// startProactiveRefresh：临期主动刷新调度
// ---------------------------------------------------------------------------

/** 伪造 window：捕获 setInterval 回调，测试手动触发「每分钟一次」的轮询 */
const stubWindow = () => {
  const handlers: Array<() => void> = [];
  const fakeWindow = {
    setInterval: vi.fn((cb: () => void, _ms?: number) => {
      handlers.push(cb);
      return handlers.length;
    }),
  };
  vi.stubGlobal("window", fakeWindow);
  return {
    fire: () => handlers.forEach((cb) => cb()),
    intervalMock: fakeWindow.setInterval,
    dispose: () => {
      vi.unstubAllGlobals();
    },
  };
};

describe("startProactiveRefresh（临期主动刷新调度）", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    resetStore();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("非浏览器环境（无 window）no-op", () => {
    // node 环境本就无 window：不应抛错也不应留下副作用
    expect(() => startProactiveRefresh()).not.toThrow();
  });

  it("按 60s 间隔注册轮询，且模块级幂等（重复调用不重复注册）", () => {
    const w = stubWindow();
    try {
      startProactiveRefresh();
      startProactiveRefresh();
      expect(w.intervalMock).toHaveBeenCalledTimes(1);
      expect(w.intervalMock.mock.calls[0][1]).toBe(60 * 1000);
    } finally {
      w.dispose();
    }
  });

  it("未登录：轮询空转不发请求", () => {
    const w = stubWindow();
    try {
      startProactiveRefresh();
      w.fire();
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      w.dispose();
    }
  });

  it("access 临期 → 触发静默刷新", () => {
    const w = stubWindow();
    try {
      useAuthStore.setState({
        token: "t-old",
        refreshToken: "r-old",
        expiresAt: new Date(Date.now() + 4 * 60 * 1000).toISOString(),
        refreshExpiresAt: new Date(Date.now() + 6 * 24 * 3600 * 1000).toISOString(),
        user,
      });
      startProactiveRefresh();
      w.fire();
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(String(fetchMock.mock.calls[0][0])).toContain("/auth/refresh");
    } finally {
      w.dispose();
    }
  });

  it("access 充足 → 不刷", () => {
    const w = stubWindow();
    try {
      useAuthStore.setState({
        token: "t",
        refreshToken: "r",
        expiresAt: new Date(Date.now() + 25 * 60 * 1000).toISOString(),
        refreshExpiresAt: new Date(Date.now() + 6 * 24 * 3600 * 1000).toISOString(),
        user,
      });
      startProactiveRefresh();
      w.fire();
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      w.dispose();
    }
  });

  it("refresh token 本身已过期 → 刷不动，等 401 兜底", () => {
    const w = stubWindow();
    try {
      useAuthStore.setState({
        token: "t",
        refreshToken: "r",
        expiresAt: new Date(Date.now() + 60 * 1000).toISOString(),
        refreshExpiresAt: new Date(Date.now() - 1000).toISOString(),
        user,
      });
      startProactiveRefresh();
      w.fire();
      expect(fetchMock).not.toHaveBeenCalled();
    } finally {
      w.dispose();
    }
  });
});
