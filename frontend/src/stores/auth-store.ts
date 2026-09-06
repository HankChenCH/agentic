import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * 认证会话（首个 zustand store）。
 *
 * 双令牌 + 用户信息持久化到 localStorage（key: agentic-auth），刷新/重开
 * 页面后自动恢复登录态。access token 短命（后端 30 分钟），临期由
 * lib/token-refresh.ts 用 refresh token 静默换新（REST 401 重试与 SSE
 * fetch 覆盖共用该单飞通道）；刷新彻底失败时调 logout() 清会话跳登录页。
 */
export interface AuthUser {
  id: string;
  username: string;
  nickname: string;
  /** 注册时间（ISO 字符串）。可选：旧版本持久化的会话里没有该字段 */
  created_at?: string;
}

/** 一次成功签发（登录/注册/刷新）的持久化载荷 */
export interface AuthSessionData {
  token: string;
  refresh_token: string;
  /** 两个 token 的过期时刻（ISO 字符串，后端签发时给出） */
  expires_at: string;
  refresh_expires_at: string;
  user: AuthUser;
}

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  expiresAt: string | null;
  refreshExpiresAt: string | null;
  user: AuthUser | null;
  setSession: (session: AuthSessionData) => void;
  /** 只更新用户信息（token 不动），资料页改昵称后同步用 */
  setUser: (user: AuthUser) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      refreshToken: null,
      expiresAt: null,
      refreshExpiresAt: null,
      user: null,
      setSession: (session) =>
        set({
          token: session.token,
          refreshToken: session.refresh_token,
          expiresAt: session.expires_at,
          refreshExpiresAt: session.refresh_expires_at,
          user: session.user,
        }),
      setUser: (user) => set({ user }),
      logout: () =>
        set({
          token: null,
          refreshToken: null,
          expiresAt: null,
          refreshExpiresAt: null,
          user: null,
        }),
    }),
    { name: "agentic-auth" },
  ),
);

/** 非 React 环境（axios 拦截器 / HttpAgent fetch 覆盖）读 token 的入口 */
export const getToken = (): string | null => useAuthStore.getState().token;

/** 非 React 环境读当前会话完整载荷（临期刷新用） */
export const getSession = (): AuthSessionData | null => {
  const s = useAuthStore.getState();
  if (!s.token || !s.refreshToken) return null;
  return {
    token: s.token,
    refresh_token: s.refreshToken,
    expires_at: s.expiresAt ?? "",
    refresh_expires_at: s.refreshExpiresAt ?? "",
    user: s.user!,
  };
};
