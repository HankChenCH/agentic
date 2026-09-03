import { useEffect, useState, type FC } from "react";

import { useDialogSubmit } from "@/components/shared/use-dialog-submit";
import { DialogFormFooter } from "@/components/shared/dialog-footer";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { BackendDocumentSegment } from "@/services/types";

/**
 * 手动新增 / 编辑分段弹窗（segment 为 null 时是新增模式）。
 *
 * 内容约束镜像后端 KnowledgeSegmentCreateRequest/UpdateRequest：
 * content 1..4000 字符。编辑为整体替换，后端保存后自动重新嵌入。
 */

const CONTENT_MAX = 4000;

export interface SegmentFormValues {
  content: string;
}

interface SegmentFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 编辑目标分段；null = 新增模式 */
  segment: BackendDocumentSegment | null;
  /** 提交（已通过前端校验）；返回 true 才关闭弹窗 */
  onSubmit: (values: SegmentFormValues) => Promise<boolean>;
}

export const SegmentFormDialog: FC<SegmentFormDialogProps> = ({
  open,
  onOpenChange,
  segment,
  onSubmit,
}) => {
  const [content, setContent] = useState("");
  const [contentError, setContentError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setContent(segment?.content ?? "");
    setContentError(null);
  }, [open, segment]);

  const isEditing = segment != null;

  const { submitting, handleSubmit } = useDialogSubmit(async () => {
    const trimmed = content.trim();
    const nextError = !trimmed
      ? "请输入分段内容"
      : trimmed.length > CONTENT_MAX
        ? `内容不能超过 ${CONTENT_MAX} 个字符`
        : null;
    setContentError(nextError);
    if (nextError) return null;
    return onSubmit({ content: trimmed });
  }, onOpenChange);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEditing ? "编辑分段" : "新增分段"}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? "整体替换分段内容，保存后自动重新嵌入向量库。"
              : "新增分段将追加到文档末尾，保存后自动嵌入向量库并参与召回。"}
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="segment-content">内容</Label>
              <span className="text-xs tabular-nums text-muted-foreground">
                {content.length} / {CONTENT_MAX}
              </span>
            </div>
            <Textarea
              id="segment-content"
              value={content}
              rows={10}
              maxLength={CONTENT_MAX}
              placeholder="输入分段文本内容"
              aria-invalid={contentError != null}
              className="whitespace-pre-wrap"
              onChange={(e) => setContent(e.target.value)}
            />
            {contentError && (
              <p className="text-xs text-destructive">{contentError}</p>
            )}
          </div>

          <DialogFormFooter submitting={submitting} label="保存" />
        </form>
      </DialogContent>
    </Dialog>
  );
};
