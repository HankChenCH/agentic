import { beforeEach, describe, expect, it } from "vitest";

import { getToken, getSession, useAuthStore } from "@/stores/auth-store";

const user = { id: "u-1", username: "hank", nickname: "Hank" };

const session = {
  token: "t-old",
  refresh_token: "r-old",
  expires_at: "2026-01-01T12:30:00Z",
  refresh_expires_at: "2026-01-08T12:30:00Z",
  user,
};

/** 恢复未登录初始态（zustand persist 在 node 下不 hydration，直接 setState 即可） */
const resetStore = () =>
  useAuthStore.setState({
    token: null,
    refreshToken: null,
    expiresAt: null,
    refreshExpiresAt: null,
    user: null,
  });

describe("useAuthStore（认证会话 store）", () => {
  beforeEach(resetStore);

  it("setSession：snake_case 载荷落位为 camelCase 状态", () => {
    useAuthStore.getState().setSession(session);
    const s = useAuthStore.getState();
    expect(s.token).toBe("t-old");
    expect(s.refreshToken).toBe("r-old");
    expect(s.expiresAt).toBe("2026-01-01T12:30:00Z");
    expect(s.refreshExpiresAt).toBe("2026-01-08T12:30:00Z");
    expect(s.user).toEqual(user);
  });

  it("setUser：只更新 user，票四件套不动（资料页改昵称同步用）", () => {
    useAuthStore.getState().setSession(session);
    const before = useAuthStore.getState();
    useAuthStore.getState().setUser({ ...user, nickname: "新昵称" });

    const after = useAuthStore.getState();
    expect(after.user?.nickname).toBe("新昵称");
    expect(after.token).toBe(before.token);
    expect(after.refreshToken).toBe(before.refreshToken);
    expect(after.expiresAt).toBe(before.expiresAt);
    expect(after.refreshExpiresAt).toBe(before.refreshExpiresAt);
  });

  it("logout：全字段清空回未登录态", () => {
    useAuthStore.getState().setSession(session);
    useAuthStore.getState().logout();
    const s = useAuthStore.getState();
    expect(s.token).toBeNull();
    expect(s.refreshToken).toBeNull();
    expect(s.expiresAt).toBeNull();
    expect(s.refreshExpiresAt).toBeNull();
    expect(s.user).toBeNull();
  });
});

describe("getToken（非 React 环境读票入口）", () => {
  beforeEach(resetStore);

  it("未登录返回 null；登录后返回 access token", () => {
    expect(getToken()).toBeNull();
    useAuthStore.getState().setSession(session);
    expect(getToken()).toBe("t-old");
  });
});

describe("getSession（临期刷新读的会话载荷）", () => {
  beforeEach(resetStore);

  it("未登录返回 null", () => {
    expect(getSession()).toBeNull();
  });

  it("缺 refresh token 视为无会话（刷不动）", () => {
    useAuthStore.setState({ token: "t-only" });
    expect(getSession()).toBeNull();
  });

  it("会话齐全：还原为 snake_case 载荷", () => {
    useAuthStore.getState().setSession(session);
    expect(getSession()).toEqual(session);
  });

  it("过期时刻缺失（旧持久化会话）回退空串，不阻塞刷新判定", () => {
    useAuthStore.setState({
      token: "t",
      refreshToken: "r",
      expiresAt: null,
      refreshExpiresAt: null,
      user,
    });
    expect(getSession()).toEqual({
      token: "t",
      refresh_token: "r",
      expires_at: "",
      refresh_expires_at: "",
      user,
    });
  });
});
