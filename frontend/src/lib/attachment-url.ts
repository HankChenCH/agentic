import { REST_BASE } from "@/lib/config";

/**
 * 附件稳定引用的解析助手（纯函数，可单测）。
 *
 * 稳定引用的契约与后端对齐（ConversationAttachmentStore.resolve_own_key
 * 同口径）：本域引用 = path 以 `/agentic/attachments/` 开头，host 部分任意
 * （发送链路上送的是「页面 origin + 相对路径」的绝对 URL，历史数据也存在
 * 裸相对引用形态）。引用**不带** API 反代前缀（/api）——后端按 path 前缀
 * 识别，带上即失配。
 *
 * 生产踩坑：不能 `new URL(src, REST_BASE)` 判域/拼址——同源反代部署
 * （VITE_API_BASE=/api）下 REST_BASE 是相对形态，URL 构造器对非法 base
 * 直接抛 TypeError（绝对 URL 也一样抛），判域恒 false，换签永不发生，
 * 稳定引用直进 `<img>` 必 401/404。
 */

const ATTACHMENT_PATH_PREFIX = "/agentic/attachments/";

/** 是否本域附件引用：绝对 http(s) URL 取 path 比前缀；相对串直接比前缀。 */
export function isAttachmentRef(src: string): boolean {
  if (/^https?:\/\//i.test(src)) {
    try {
      return new URL(src).pathname.startsWith(ATTACHMENT_PATH_PREFIX);
    } catch {
      return false;
    }
  }
  return src.startsWith(ATTACHMENT_PATH_PREFIX);
}

/**
 * 引用 → 浏览器可 fetch 的绝对 URL（附件下载路由用，需带 /api 反代前缀）。
 *
 * REST_BASE 形态自适应：http(s) 根去尾斜杠后字符串拼接；相对根（/api）
 * 拼页面 origin。不走 `new URL(path, base)`——base 带路径时绝对 path 会
 * 整段替换掉 base 的路径（/api 前缀丢失），base 相对时又直接抛错。
 *
 * ``pageOrigin`` 供单测注入；缺省取当前页面 origin（非浏览器环境为空串）。
 */
export function toFetchableUrl(ref: string, pageOrigin?: string): string {
  if (/^https?:\/\//i.test(ref)) return ref;
  const path = ref.startsWith("/") ? ref : `/${ref}`;
  const base = REST_BASE.startsWith("http")
    ? REST_BASE.replace(/\/+$/, "")
    : `${pageOrigin ?? (typeof window === "undefined" ? "" : window.location.origin)}${REST_BASE.replace(/\/+$/, "")}`;
  return `${base}${path}`;
}
