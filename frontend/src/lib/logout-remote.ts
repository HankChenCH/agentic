import { REST_BASE } from "@/lib/config";
import { useAuthStore } from "@/stores/auth-store";

/**
 * 登出撤销通道：best-effort 调 POST /auth/logout 吊销服务端 refresh 会话族。
 *
 * 关键约束：
 * - **裸 fetch**，不经 lib/http.ts 的 axios 实例——access 过期时拦截器
 *   会尝试静默刷新/强制登出，与登出流程自递归；
 * - **即发即忘**（fire-and-forget）：任何失败静默吞掉，登出的本地清理
 *   与整页跳转绝不因网络问题阻塞；撤销失败的票仍有 7 天自然过期兜底；
 * - `keepalive: true`：紧随其后的整页跳转会中止在途请求，keepalive 让
 *   撤销请求在页面 unload 后仍送达服务端。
 */
export function logoutRemote(): void {
  const { token, refreshToken } = useAuthStore.getState();
  if (!token || !refreshToken) return;
  void fetch(`${REST_BASE}/auth/logout`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ refresh_token: refreshToken }),
    keepalive: true,
  }).catch(() => {
    // 撤销失败不阻塞登出（幂等语义在服务端；本地会话照常清理）
  });
}
