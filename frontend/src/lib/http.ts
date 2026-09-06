import axios from "axios";
import type { AxiosRequestConfig, InternalAxiosRequestConfig } from "axios";

import { REST_BASE } from "@/lib/config";
import { refreshSession, startProactiveRefresh } from "@/lib/token-refresh";
import { getToken, useAuthStore } from "@/stores/auth-store";

// 临期主动刷新调度随模块加载启动（内部对非浏览器环境 no-op）
startProactiveRefresh();

/**
 * 后端 REST 响应统一信封（见 server Response.success）。
 * 所有 REST 端点都返回 { error_code, error_message, response }，
 * 业务数据挂在 response 字段上。
 */
export interface ApiResponse<T = unknown> {
  error_code: number;
  error_message: string;
  response: T;
}

/**
 * 业务层错误：后端返回 error_code !== 0 时抛出，区别于 HTTP/网络错误。
 * 调用方可用 `err instanceof BizError` 判断是否业务失败。
 */
export class BizError extends Error {
  readonly errorCode: number;
  constructor(errorCode: number, message: string) {
    super(message);
    this.name = "BizError";
    this.errorCode = errorCode;
  }
}

/**
 * 唯一的 HTTP 客户端。所有 REST 调用都经此实例，不直接用 fetch。
 *
 * baseURL 统一来自 @/lib/config（环境变量 + 兜底）。
 * 响应拦截器把业务错误转成异常：
 *   - error_code !== 0 → 抛 BizError（业务错误）
 *   - HTTP 层错误（4xx/5xx/网络）原样透传，调用方自行 catch
 * 拆信封（剥掉外层取 response 字段）在 getJson 里做，不放拦截器：
 * axios 1.19 起 AxiosInterceptorFulfilled 要求拦截器原样返回
 * AxiosResponse，老"拦截器里换裸 payload"的写法不再通过类型检查。
 *
 * 注意：SSE 流式 agentic run 端点（/agentic/run）不归这里管，仍由 @ag-ui/client 的
 * HttpAgent 直接消费。
 */
export const http = axios.create({
  baseURL: REST_BASE,
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

/**
 * 响应为二进制（arraybuffer/blob）的请求标识。
 *
 * 二进制响应没有信封结构：成功分支必须跳过 error_code 检查（ArrayBuffer
 * 上取 .error_code 是 undefined，`undefined !== 0` 会把成功误判成业务
 * 错误）；失败分支的 4xx body 也是 ArrayBuffer，需先解码成文本再拆信封。
 */
const isBinaryResponse = (config: AxiosRequestConfig | undefined) =>
  config?.responseType === "arraybuffer" || config?.responseType === "blob";

/**
 * 401 统一处理：清会话并跳登录页（带 next 回跳参数）。
 * /auth/* 自身的 401（用户名或密码错误）属于业务错误，交给调用方在
 * 表单上展示，不做登出跳转。
 */
function handleUnauthorized(url: string | undefined) {
  if (url?.startsWith("/auth/")) return;
  useAuthStore.getState().logout();
  if (
    typeof window !== "undefined" &&
    !window.location.pathname.startsWith("/login")
  ) {
    window.location.assign(
      `/login?next=${encodeURIComponent(window.location.pathname)}`,
    );
  }
}

// 请求拦截器：注入 Authorization: Bearer <token>（token 实时读 store，
// 登录/登出后无需重建实例）
http.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

/** 二进制错误 body（ArrayBuffer）解码回信封结构，拿不到就返回 undefined */
function decodeBinaryEnvelope(data: unknown): ApiResponse | undefined {
  if (!(data instanceof ArrayBuffer)) return undefined;
  try {
    const body = JSON.parse(new TextDecoder().decode(data)) as ApiResponse;
    if (typeof body?.error_code === "number") return body;
    return undefined;
  } catch {
    return undefined;
  }
}

http.interceptors.response.use(
  (res) => {
    if (isBinaryResponse(res.config)) return res;
    const body = res.data as ApiResponse;
    if (body.error_code !== 0) {
      return Promise.reject(
        new BizError(body.error_code, body.error_message || "请求失败"),
      );
    }
    return res;
  },
  (error) => {
    // HTTP 4xx/5xx：全局异常处理器把业务错误映射为 4xx + 信封体（如
    // error_code=4003 状态冲突），在 rejection 分支同样拆包成 BizError，
    // 调用方无需区分"业务错误走 2xx 还是 4xx"。
    const body =
      (decodeBinaryEnvelope(error?.response?.data) as ApiResponse | undefined) ??
      (error?.response?.data as ApiResponse | undefined);
    // 令牌过期（非 /auth/* 请求）：先用 refresh token 静默换新并重放原请求
    // 一次（单飞去并发，__retried 防二次循环）；刷新失败才登出回登录页。
    // /auth/* 自身的 401（用户名或密码错误）属于业务错误，交给调用方在
    // 表单上展示。SSE 流式端点不走这里的 axios 实例，其 401 在
    // agentic-runtime 的 fetch 覆盖里做同样的刷新重试。
    const config = error?.config as (InternalAxiosRequestConfig & { __retried?: boolean }) | undefined;
    if (
      error?.response?.status === 401 &&
      config &&
      !config.__retried &&
      !config.url?.startsWith("/auth/")
    ) {
      return refreshSession().then((refreshed) => {
        if (!refreshed) {
          handleUnauthorized(config.url as string | undefined);
          return Promise.reject(error);
        }
        config.__retried = true;
        return http.request(config);
      });
    }
    if (error?.response?.status === 401) {
      handleUnauthorized(error?.config?.url as string | undefined);
    }
    if (
      body &&
      typeof body === "object" &&
      typeof body.error_code === "number" &&
      body.error_code !== 0
    ) {
      return Promise.reject(
        new BizError(body.error_code, body.error_message || "请求失败"),
      );
    }
    return Promise.reject(error);
  },
);

/**
 * 类型友好的 GET 取数 helper。
 *
 * 拆信封在这里做：error_code === 0（拦截器已放行）→ 返回 response 字段
 * 的干净 payload。service 方法调用时只需写 getJson<目标类型>(...)。
 */
export async function getJson<T>(
  url: string,
  config?: AxiosRequestConfig,
): Promise<T> {
  const res = await http.get<ApiResponse<T>>(url, config);
  return res.data.response;
}

/**
 * 二进制 GET helper（文件下载/预览）：无信封，直接返回响应字节。
 * 业务错误（4xx 信封体）仍由拦截器转成 BizError 抛出。
 */
export async function getBinary(
  url: string,
  config?: AxiosRequestConfig,
): Promise<ArrayBuffer> {
  const res = await http.get(url, { ...config, responseType: "arraybuffer" });
  return res.data as ArrayBuffer;
}

/**
 * 类型友好的写操作 helper（POST JSON）。拆信封逻辑与 getJson 对称。
 */
export async function postJson<T>(
  url: string,
  body?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  const res = await http.post<ApiResponse<T>>(url, body, config);
  return res.data.response;
}

/**
 * 类型友好的写操作 helper（PATCH JSON，部分更新语义）。
 */
export async function patchJson<T>(
  url: string,
  body?: unknown,
  config?: AxiosRequestConfig,
): Promise<T> {
  const res = await http.patch<ApiResponse<T>>(url, body, config);
  return res.data.response;
}

/**
 * 类型友好的写操作 helper（DELETE）。
 */
export async function deleteJson<T>(
  url: string,
  config?: AxiosRequestConfig,
): Promise<T> {
  const res = await http.delete<ApiResponse<T>>(url, config);
  return res.data.response;
}

/**
 * multipart 表单上传（文件 + 字段）。
 *
 * 实例默认头是 application/json，这里必须按请求清掉（axios 中 header 值
 * 置 null 即删除），交给浏览器为 FormData 自动生成带 boundary 的
 * Content-Type，否则后端按 JSON 解析 body 会上传失败。
 */
export async function postForm<T>(url: string, form: FormData): Promise<T> {
  const res = await http.post<ApiResponse<T>>(url, form, {
    headers: { "Content-Type": null },
  });
  return res.data.response;
}
