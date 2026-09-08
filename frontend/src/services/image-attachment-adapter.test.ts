import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PendingAttachment } from "@assistant-ui/react";

import { attachmentService } from "@/services/attachment-service";
import { ServerImageAttachmentAdapter } from "@/services/image-attachment-adapter";

vi.mock("@/services/attachment-service", () => ({
  attachmentService: { upload: vi.fn() },
}));

const uploadMock = vi.mocked(attachmentService.upload);

const uploaded = {
  id: "att-1",
  filename: "cat.png",
  mime_type: "image/png",
  size_bytes: 4,
  url: "/agentic/attachments/att-1/cat.png",
};

const makeFile = () => new File(["fake"], "cat.png", { type: "image/png" });

/** 取生成器下一帧产出；提前结束（done）即断言失败 */
const nextValue = async (
  gen: AsyncGenerator<PendingAttachment, void>,
): Promise<PendingAttachment> => {
  const result = await gen.next();
  if (result.done) throw new Error("生成器提前结束");
  return result.value;
};

/** 消费完整个 add() 生成器，返回末帧（待发送态）附件 */
const drainAdd = async (
  adapter: ServerImageAttachmentAdapter,
  file: File,
): Promise<PendingAttachment> => {
  const gen = adapter.add({ file });
  await nextValue(gen);
  const ready = await nextValue(gen);
  expect((await gen.next()).done).toBe(true);
  return ready;
};

describe("ServerImageAttachmentAdapter", () => {
  beforeEach(() => {
    uploadMock.mockReset();
  });

  it("选中文件即发起上传：首帧为上传中态，不等 send", async () => {
    let resolveUpload!: (v: typeof uploaded) => void;
    uploadMock.mockReturnValue(
      new Promise((resolve) => {
        resolveUpload = resolve;
      }),
    );
    const adapter = new ServerImageAttachmentAdapter();
    const gen = adapter.add({ file: makeFile() });

    const first = await nextValue(gen);
    expect(first.status).toEqual({
      type: "running",
      reason: "uploading",
      progress: -1,
    });
    // add 即传：此刻上传请求已发出
    expect(uploadMock).toHaveBeenCalledTimes(1);

    resolveUpload(uploaded);
    const second = await nextValue(gen);
    expect(second.status).toEqual({
      type: "requires-action",
      reason: "composer-send",
    });
    expect((await gen.next()).done).toBe(true);
  });

  it("上传完成后再发送：send 复用结果零等待，不再二次上传", async () => {
    uploadMock.mockResolvedValue(uploaded);
    const adapter = new ServerImageAttachmentAdapter();
    const pending = await drainAdd(adapter, makeFile());
    expect(uploadMock).toHaveBeenCalledTimes(1);

    const complete = await adapter.send(pending);
    expect(uploadMock).toHaveBeenCalledTimes(1);
    expect(complete.status).toEqual({ type: "complete" });
    expect(complete.id).toBe("att-1");
    // REST_BASE 兜底 http://127.0.0.1:8000：稳定相对引用拼成绝对 URL
    expect(complete.content).toEqual([
      {
        type: "image",
        image: "http://127.0.0.1:8000/agentic/attachments/att-1/cat.png",
      },
    ]);
  });

  it("上传进行中点发送：等待同一上传完成，不重复发起", async () => {
    let resolveUpload!: (v: typeof uploaded) => void;
    uploadMock.mockReturnValue(
      new Promise((resolve) => {
        resolveUpload = resolve;
      }),
    );
    const adapter = new ServerImageAttachmentAdapter();
    const gen = adapter.add({ file: makeFile() });
    const pending = await nextValue(gen);

    const sending = adapter.send(pending);
    resolveUpload(uploaded);
    const complete = await sending;
    expect(uploadMock).toHaveBeenCalledTimes(1);
    expect(complete.id).toBe("att-1");
  });

  it("add 阶段上传失败：生成器抛错；发送时重试一次，成功则放行", async () => {
    uploadMock.mockRejectedValueOnce(new Error("boom"));
    const adapter = new ServerImageAttachmentAdapter();
    const gen = adapter.add({ file: makeFile() });
    const pending = await nextValue(gen);
    await expect(gen.next()).rejects.toThrow("boom");

    uploadMock.mockResolvedValue(uploaded);
    const complete = await adapter.send(pending);
    expect(uploadMock).toHaveBeenCalledTimes(2);
    expect(complete.status).toEqual({ type: "complete" });
  });

  it("发送时重试仍失败：抛错中止提交", async () => {
    uploadMock.mockRejectedValue(new Error("down"));
    const adapter = new ServerImageAttachmentAdapter();
    const gen = adapter.add({ file: makeFile() });
    const pending = await nextValue(gen);
    await expect(gen.next()).rejects.toThrow("down");

    await expect(adapter.send(pending)).rejects.toThrow("down");
    expect(uploadMock).toHaveBeenCalledTimes(2);
  });
});
