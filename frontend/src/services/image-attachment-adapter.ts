import type {
  AttachmentAdapter,
  CompleteAttachment,
  PendingAttachment,
} from "@assistant-ui/react";

import { REST_BASE } from "@/lib/config";
import { attachmentService } from "@/services/attachment-service";
import type { BackendAttachment } from "@/services/types";

/**
 * 随附件对象「随行」的上传句柄：add 阶段发起的上传 promise 直接挂在
 * PendingAttachment 上（不进适配器实例字段），send 阶段从收到的附件上原样
 * 取回——附件被移除/落库后对象整体可被 GC，无需任何缓存清理逻辑。
 */
type UploadingAttachment = PendingAttachment & {
  upload?: Promise<BackendAttachment>;
};

/**
 * 图片附件适配器：选中即上传（eager upload），发送零等待。
 *
 * 流程：选中文件 → add() 立即发起上传，以 AsyncGenerator 两段产出同一附件
 * （runtime 按 id 原位替换）：先 yield「上传中」态（缩略图转圈 + 本地预览），
 * 上传完成再切「待发送」态——上传与用户打字并行；用户点发送 → send() 复用
 * 已完成的上传结果，不再等待上传，大图不再阻塞提问。
 *
 * 失败语义保持「上传成功才能发消息」：
 * - add 阶段失败：生成器抛错，runtime 把附件原位标记为 error 态（缩略图
 *   红框 + 错误浮层），并发 composer.attachmentAddError 事件（chat-page
 *   订阅转 toast）；附件留在待发区，可移除重选；
 * - 发送时对失败附件重试一次（catch 后重新 upload），与旧实现「上传失败
 *   可再点发送重试」的出路对齐；再失败抛错中止提交，文本由 runtime 还原。
 *
 * 引用 URL 为稳定相对路径拼 API 根；消息发出后 react-ag-ui 自动转成
 * ag-ui 的 image url source（绝对 http URL → url source），后端落库该
 * 引用并在渲染时 302 重定向到预签名地址（浏览器直拉对象存储）。
 */
export class ServerImageAttachmentAdapter implements AttachmentAdapter {
  accept = "image/png,image/jpeg,image/webp,image/gif";

  async *add({
    file,
  }: {
    file: File;
  }): AsyncGenerator<PendingAttachment, void> {
    // 上传即刻发起（multipart 经 axios 鉴权拦截器），不等用户点发送
    const upload = attachmentService.upload(file);
    const attachment: UploadingAttachment = {
      id: crypto.randomUUID(),
      type: "image",
      name: file.name,
      contentType: file.type || "image/png",
      file,
      // progress 无真实进度源，-1 表示不确定（UI 只按 running 态转圈）
      status: { type: "running", reason: "uploading", progress: -1 },
      upload,
    };
    yield attachment;
    // 失败向上抛：runtime 原位标记 error 态并发 attachmentAddError 事件
    await upload;
    yield {
      ...attachment,
      status: { type: "requires-action", reason: "composer-send" },
    };
  }

  async send(attachment: PendingAttachment): Promise<CompleteAttachment> {
    const eager = (attachment as UploadingAttachment).upload;
    let uploaded: BackendAttachment;
    if (eager) {
      try {
        // 选中即传的成果：已完成则立即返回（发送零等待），仍在传则收尾等待
        uploaded = await eager;
      } catch {
        // 选中阶段的上传失败过（缩略图已呈错误态）：发送时重试一次
        uploaded = await attachmentService.upload(attachment.file);
      }
    } else {
      // 兜底：send 只会收到 add() 产出的附件（必带 upload 句柄）
      uploaded = await attachmentService.upload(attachment.file);
    }
    // 引用必须是绝对 http(s) URL，且 path 以 /agentic/attachments/ 开头：
    // - react-ag-ui 的 resolveFilePartSource 只把 http(s):// 开头的值当 url
    //   source，相对路径（同源反代形态 REST_BASE=/api）会被整段误包成 data
    //   source，后端按 base64 解析必然 400（Invalid base64 data）；
    // - 后端按 urlsplit(path).startswith("/agentic/attachments/") 识别本域
    //   引用，故 base 不能带 /api 前缀——REST_BASE 为相对形态时用页面 origin。
    const apiRoot = REST_BASE.startsWith("http")
      ? REST_BASE
      : window.location.origin;
    return {
      id: uploaded.id,
      type: "image",
      name: attachment.name,
      contentType: attachment.contentType,
      file: attachment.file,
      status: { type: "complete" },
      content: [
        { type: "image", image: new URL(uploaded.url, apiRoot).toString() },
      ],
    };
  }

  async remove(): Promise<void> {
    // 无需清理：不做对象删除（会话附件孤儿清理是后续项），选中即传意味着
    // 「选了又移除」同样会留下服务端孤儿，口径不变；上传中的请求不取消，
    // 本地预览 File 由 runtime 随附件对象释放
  }
}
