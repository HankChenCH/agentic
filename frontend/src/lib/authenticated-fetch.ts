import { refreshSession } from "@/lib/token-refresh";
import { getToken, useAuthStore } from "@/stores/auth-store";

/**
 * SSE 流式请求的认证与错误包装：每次发请求实时读 token（agent 实例 useMemo
 * 一次创建，闭包捕获会陈旧，必须请求时现读）。三类拦截：
 * - 401（令牌缺失/过期）：先用 refresh token 单飞静默换新，成功即换新
 *   token 重试一次再建流；刷新失败才清会话并跳登录 —— SSE 端点在 200 流式
 *   头之后不走全局异常处理器，只能在 fetch 层拦截，与 lib/http.ts 的 REST
 *   拦截器口径一致（刷新通道共用 lib/token-refresh.ts）；
 * - 其余非 2xx：解析响应信封取 error_message（业务错误），5xx/解析失败给
 *   归类提示，抛出干净 Error —— 避免 HttpAgent 把 `HTTP 500: {原始JSON}`
 *   整段怼进聊天气泡。
 */
export const authenticatedFetch: typeof fetch = async (input, init) => {
  const buildHeaders = (token: string | null) => {
    const headers = new Headers(init?.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return headers;
  };
  let response: Response;
  try {
    response = await fetch(input, { ...init, headers: buildHeaders(getToken()) });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new Error("网络连接中断，请检查网络后重试");
  }
  if (response.status === 401) {
    const refreshed = await refreshSession();
    if (refreshed) {
      // 用换新的 token 重试一次；再 401 只能登出
      response = await fetch(input, {
        ...init,
        headers: buildHeaders(getToken()),
      });
    }
    if (response.status === 401) {
      useAuthStore.getState().logout();
      if (
        typeof window !== "undefined" &&
        !window.location.pathname.startsWith("/login")
      ) {
        window.location.assign(
          `/login?next=${encodeURIComponent(window.location.pathname)}`,
        );
      }
      return response;
    }
  }
  if (!response.ok) {
    let detail: string | null = null;
    try {
      const text = await response.text();
      const body = JSON.parse(text) as { error_message?: unknown };
      detail =
        typeof body?.error_message === "string" && body.error_message
          ? body.error_message
          : null;
    } catch {
      // 非 JSON 响应体（如网关 HTML 错误页），走状态码兜底
    }
    if (response.status >= 500) {
      throw new Error(detail ?? "服务器开小差了，请稍后重试");
    }
    throw new Error(detail ?? `请求失败（HTTP ${response.status}）`);
  }
  return response;
};
