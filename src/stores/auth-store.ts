import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * 认证会话（首个 zustand store）。
 *
 * token + 用户信息持久化到 localStorage（key: agentic-auth），刷新/重开
 * 页面后自动恢复登录态。401 处理（lib/http.ts 拦截器与 agentic-runtime 的
 * fetch 覆盖）直接调 logout() 清会话并跳登录页。
 */
export interface AuthUser {
  id: string;
  username: string;
  nickname: string;
}

interface AuthState {
  token: string | null;
  user: AuthUser | null;
  setSession: (token: string, user: AuthUser) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      setSession: (token, user) => set({ token, user }),
      logout: () => set({ token: null, user: null }),
    }),
    { name: "agentic-auth" },
  ),
);

/** 非 React 环境（axios 拦截器 / HttpAgent fetch 覆盖）读 token 的入口 */
export const getToken = (): string | null => useAuthStore.getState().token;
