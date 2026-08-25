import type { FC } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatFileSize } from "@/lib/format";
import {
  PdfViewer,
  type PdfHighlight,
} from "@/components/knowledge/pdf-viewer";

export type { PdfHighlight };

/**
 * 文档预览弹窗：Dialog 壳（标题 + 描述）内嵌 ``PdfViewer``。
 *
 * 查看器本体（连续滚动/懒渲染/页码跟随/缩放锚定/溯源高亮）在
 * ``pdf-viewer.tsx``，可脱离弹窗独立嵌入使用；本组件只是弹窗形态的
 * 包装，Props 与拆分前完全一致，调用方零改动。
 */

interface DocumentPreviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  kbId?: string;
  docId?: string;
  docName?: string;
  fileSize?: number | null;
  /** 预览模式：document 整篇（默认）；snippet 仅命中页（检索溯源） */
  mode?: "document" | "snippet";
  /** 打开/变更时滚动定位到的页（0 起）；不传停在顶部 */
  initialPage?: number;
  /** 溯源高亮区域（各页只画自己的框） */
  highlights?: readonly PdfHighlight[];
}

export const DocumentPreviewDialog: FC<DocumentPreviewDialogProps> = ({
  open,
  onOpenChange,
  kbId,
  docId,
  docName,
  fileSize,
  mode,
  initialPage,
  highlights,
}) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[85dvh] w-[90vw] max-w-5xl flex-col gap-0 p-0 sm:max-w-5xl">
        <DialogHeader className="flex-none border-b px-4 py-3">
          <DialogTitle className="truncate">{docName ?? "文档预览"}</DialogTitle>
          <DialogDescription>
            {fileSize != null && `${formatFileSize(fileSize)} · `}
            {mode === "snippet" ? "检索片段 · 仅展示命中页" : "PDF 原始文件"}
          </DialogDescription>
        </DialogHeader>
        {/* 查看器随弹窗内容挂载/卸载：关闭即释放 canvas，重开重新取数 */}
        <PdfViewer
          className="flex-1"
          kbId={kbId}
          docId={docId}
          mode={mode}
          initialPage={initialPage}
          highlights={highlights}
        />
      </DialogContent>
    </Dialog>
  );
};
