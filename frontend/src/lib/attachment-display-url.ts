import { toFetchableUrl } from "@/lib/attachment-url";
import { attachmentService } from "@/services/attachment-service";
import { getToken } from "@/stores/auth-store";

// 预签名展示地址缓存：稳定引用 → { src, 签发时刻, 是否签名地址 }。缩略图
// 与大图对话框共享同一缓存避免重复解析；blob 降级结果不参与 TTL 过期
// （blob 无过期概念，复用到页面卸载即可）。
const displayUrlCache = new Map<
  string,
  { src: string; issuedAt: number; presigned: boolean }
>();
// 后端签名 TTL 600s，取半程视为过期重签，避免渲染中途签名失效
const DISPLAY_URL_TTL_MS = 5 * 60 * 1000;

// 本域附件引用判定与 URL 拼接在 lib/attachment-url.ts（纯逻辑可单测）：
// REST_BASE 为相对形态（同源反代部署）时不能走 new URL(ref, REST_BASE)，
// 否则 URL 构造器抛错、判域恒 false、换签永不发生（生产裂图根因）。

// 无签名可用（后端返回 url=null，本地磁盘后端）时的降级：鉴权 fetch 稳定
// 引用转 blob。本地后端无 302、同 API 源无跨域问题，字节可直接读取。
const fetchAttachmentBlobUrl = async (
  ref: string,
): Promise<string | undefined> => {
  try {
    const token = getToken();
    const res = await fetch(toFetchableUrl(ref), {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
    if (!res.ok) return undefined;
    return URL.createObjectURL(await res.blob());
  } catch {
    return undefined;
  }
};

/**
 * 本域附件引用 → 可展示 src。
 *
 * 稳定引用不能直接进 ``<img>``：图片标签带不上 Authorization 头，直接
 * 渲染必 401（rustfs 的 302 预签名响应无 CORS 头，fetch 跟随也拿不到
 * blob）。故先经后端 ``/agentic/attachments/url`` 换预签名地址再渲染；
 * 解析失败回落 undefined（缩略图显示图标），不打断消息渲染。
 * 「是否本域引用」的门槛判定归调用方（attachment.tsx 的 hook，外部
 * http(s) / data URL 原样透传），本函数只处理已判定的本域引用。
 */
export const resolveDisplayUrl = async (
  ref: string,
): Promise<string | undefined> => {
  const cached = displayUrlCache.get(ref);
  if (
    cached &&
    (!cached.presigned || Date.now() - cached.issuedAt < DISPLAY_URL_TTL_MS)
  ) {
    return cached.src;
  }
  let presigned: string | null;
  try {
    presigned = await attachmentService.getDisplayUrl(ref);
  } catch {
    return undefined;
  }
  const resolved = presigned ?? (await fetchAttachmentBlobUrl(ref));
  if (!resolved) return undefined;
  displayUrlCache.set(ref, {
    src: resolved,
    issuedAt: Date.now(),
    presigned: presigned != null,
  });
  return resolved;
};

/** 测试辅助：清空换址缓存（生产代码不使用） */
export const clearDisplayUrlCacheForTest = (): void => {
  displayUrlCache.clear();
};
