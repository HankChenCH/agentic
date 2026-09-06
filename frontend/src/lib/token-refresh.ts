import { REST_BASE } from "@/lib/config";
import { getSession, useAuthStore, type AuthSessionData } from "@/stores/auth-store";

/**
 * Refresh token 静默刷新通道（REST 401 重试 / SSE fetch 覆盖 / 临期调度共用）。
 *
 * 关键约束：
 * - 走**裸 fetch**直连 /auth/refresh，不经过 lib/http.ts 的 axios 实例——
 *   拦截器对 401 的处理正是「尝试刷新」，经它会自递归；
 * - **单飞**（single-flight）：并发触发只发一次刷新请求，其余等待同一
 *   promise，避免竞态下旧 refresh token 已被旋转作废导致连环 401；
 * - 后端旋转 refresh token（每次返回全新一对），成功即整包覆写会话。
 */

/** access token 剩余寿命低于该值即触发临期刷新 */
export const PROACTIVE_REFRESH_THRESHOLD_MS = 5 * 60 * 1000;

/**
 * 临期判定（纯函数，vitest 覆盖）：剩余寿命不足阈值（或已过期）→ true。
 * expires_at 缺失（旧持久化会话）不主动刷，交由 401 兜底。
 */
export function shouldRefresh(
  expiresAt: string | null | undefined,
  now: number,
  thresholdMs = PROACTIVE_REFRESH_THRESHOLD_MS,
): boolean {
  if (!expiresAt) return false;
  const exp = Date.parse(expiresAt);
  if (Number.isNaN(exp)) return false;
  return exp - now <= thresholdMs;
}

let inflight: Promise<boolean> | null = null;

/** 单飞刷新：成功返回 true（会话已更新）；失败/无 refresh token 返回 false */
export function refreshSession(): Promise<boolean> {
  if (!inflight) {
    inflight = doRefresh().finally(() => {
      inflight = null;
    });
  }
  return inflight;
}

async function doRefresh(): Promise<boolean> {
  const session = getSession();
  if (!session?.refresh_token) return false;
  try {
    const res = await fetch(`${REST_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: session.refresh_token }),
    });
    if (!res.ok) return false;
    const body = (await res.json()) as {
      error_code: number;
      response: AuthSessionData;
    };
    if (body.error_code !== 0 || !body.response?.refresh_token) return false;
    useAuthStore.getState().setSession(body.response);
    return true;
  } catch {
    return false;
  }
}

const PROACTIVE_CHECK_INTERVAL_MS = 60 * 1000;

/**
 * 启动临期主动刷新调度（模块级只启一次；登录后无需重排——每分钟轮询
 * 剩余寿命即可覆盖任意签发时刻）。非浏览器环境（vitest node env）不启。
 */
export function startProactiveRefresh(): void {
  if (typeof window === "undefined") return;
  const w = window as typeof window & { __agenticProactiveRefresh?: boolean };
  if (w.__agenticProactiveRefresh) return;
  w.__agenticProactiveRefresh = true;
  window.setInterval(() => {
    const state = useAuthStore.getState();
    if (!state.token || !state.refreshToken) return;
    // refresh token 本身已过期：刷不动了，等 401 兜底走登出
    if (state.refreshExpiresAt && Date.parse(state.refreshExpiresAt) <= Date.now())
      return;
    if (shouldRefresh(state.expiresAt, Date.now())) void refreshSession();
  }, PROACTIVE_CHECK_INTERVAL_MS);
}
