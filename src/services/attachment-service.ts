import { postForm } from "@/lib/http";
import type { BackendAttachment } from "@/services/types";

/**
 * 会话附件上传服务。
 *
 * 分域口径（与后端约定）：上传永远过后端（类型/大小校验收口在后端），
 * 下载走预签名 302（浏览器直拉对象存储）——消息里引用的是稳定相对
 * url（永不过期），<img> 直接指向 `${REST_BASE}${url}` 即可。
 */
export const attachmentService = {
  /** 上传一个图片附件；上传失败抛 BizError（调用方据此阻断消息提交）。 */
  upload(file: File): Promise<BackendAttachment> {
    const form = new FormData();
    form.append("file", file);
    return postForm<BackendAttachment>("/agentic/attachments", form);
  },
};
