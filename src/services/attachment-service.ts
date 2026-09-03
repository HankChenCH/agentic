import { getJson, postForm } from "@/lib/http";
import type { BackendAttachment } from "@/services/types";

/**
 * 会话附件上传服务。
 *
 * 分域口径（与后端约定）：上传永远过后端（类型/大小校验收口在后端），
 * 消息里引用的是稳定 url（永不过期）。展示不走稳定引用直渲——<img>/
 * 新标签页带不上 Authorization 头会 401——先经 getDisplayUrl 换预签名
 * 地址再渲染（后端鉴权 + 属主校验后 JSON 返回签名地址，浏览器直拉对象
 * 存储）。
 */
export const attachmentService = {
  /** 上传一个图片附件；上传失败抛 BizError（调用方据此阻断消息提交）。 */
  upload(file: File): Promise<BackendAttachment> {
    const form = new FormData();
    form.append("file", file);
    return postForm<BackendAttachment>("/agentic/attachments", form);
  },

  /**
   * 解析附件稳定引用为浏览器可直拉的预签名展示地址。
   *
   * ``ref`` 接受任意形态（裸相对路径或「REST_BASE + 相对路径」绝对 URL，
   * host 任意）。存储后端不具备签名能力（本地磁盘）时返回 null，调用方
   * 降级为鉴权 fetch 稳定引用转 blob（无 302、同 API 源，无跨域问题）。
   */
  async getDisplayUrl(ref: string): Promise<string | null> {
    const { url } = await getJson<{ url: string | null }>(
      "/agentic/attachments/url",
      { params: { ref } },
    );
    return url;
  },
};
