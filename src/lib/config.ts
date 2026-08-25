/**
 * 后端地址集中配置。
 *
 * 统一走环境变量（Vite 的 import.meta.env），并提供本地开发兜底值，
 * 这样 http.ts 和 agentic-runtime.tsx 不再各自硬编码，部署只改 .env。
 *
 * - REST_BASE: REST 基址（API 根，各领域端点自带顶级前缀：
 *              /agentic/conversation...、/knowledge...、/agent/.../knowledge）
 * - SSE_URL:   ag-ui SSE 流式 chat 端点。默认由 REST_BASE 派生（同源同服务），
 *              若前后端分离/走网关需要单独覆盖，可设 VITE_SSE_URL。
 *
 * .env(.local) 示例：
 *   VITE_API_BASE=http://your-host
 *   VITE_SSE_URL=http://your-host/agentic/chat   # 可选
 */
export const REST_BASE =
  import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export const SSE_URL =
  import.meta.env.VITE_SSE_URL ?? `${REST_BASE}/agentic/chat`;
