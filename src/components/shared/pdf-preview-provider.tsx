import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type FC,
  type ReactNode,
} from "react";

import { DocumentPreviewDialog } from "@/components/shared/document-preview-dialog";
import type { PdfHighlight } from "@/components/shared/pdf-viewer";
import type { KnowledgeSource } from "@/services/types";

/**
 * 全局 PDF 预览通道：任意页面（聊天溯源卡片 / 知识库文档表格）都能唤起
 * 预览并携带溯源定位（页码 + bbox 高亮）。
 *
 * 两种形态：
 * - 弹窗（``open``/``close``）：整篇文档预览，模态，由本 Provider 直接渲染；
 * - 抽屉（``openPanel``/``setPanelIndex``/``closePanel``）：聊天检索溯源，
 *   右侧滑出、无遮罩非模态，打开期间可在多来源间切换；渲染在聊天页
 *   挂载的 ``PdfSourcePanel``（离开聊天页即卸载并清空状态）。
 *
 * 挂在 App 路由外层（与 AgenticRuntimeProvider 平级），弹窗全局仅一份，
 * 避免 pdfjs 实例与字节缓存的重复开销。
 */

export type { PdfHighlight };

/** 预览目标：定位信息（页码/高亮）可选，缺失时退化为普通预览 */
export interface PdfPreviewTarget {
  kbId: string;
  docId: string;
  docName?: string;
  fileSize?: number | null;
  /** 预览模式：document 整篇连续滚动（默认）；snippet 仅命中页（检索溯源） */
  mode?: "document" | "snippet";
  /** 初始页（0 起，与后端 meta 对齐） */
  page?: number;
  /** 溯源高亮区域 */
  highlights?: readonly PdfHighlight[];
}

/** 抽屉形态状态：整批来源 + 当前下标——切换来源只改下标，抽屉不重开 */
export interface PdfSourcePanelState {
  sources: readonly KnowledgeSource[];
  activeIndex: number;
}

interface PdfPreviewContextValue {
  /** 弹窗形态预览（整篇文档） */
  open: (target: PdfPreviewTarget) => void;
  close: () => void;
  /** 抽屉形态（聊天溯源）当前状态，null = 收起 */
  panel: PdfSourcePanelState | null;
  openPanel: (state: PdfSourcePanelState) => void;
  setPanelIndex: (index: number) => void;
  closePanel: () => void;
}

const PdfPreviewContext = createContext<PdfPreviewContextValue | null>(null);

export const PdfPreviewProvider: FC<{ children: ReactNode }> = ({
  children,
}) => {
  const [target, setTarget] = useState<PdfPreviewTarget | null>(null);
  const [panel, setPanel] = useState<PdfSourcePanelState | null>(null);

  const open = useCallback((next: PdfPreviewTarget) => setTarget(next), []);
  const close = useCallback(() => setTarget(null), []);
  const openPanel = useCallback(
    (next: PdfSourcePanelState) => setPanel(next),
    [],
  );
  const setPanelIndex = useCallback(
    (index: number) =>
      setPanel((prev) => (prev ? { ...prev, activeIndex: index } : prev)),
    [],
  );
  const closePanel = useCallback(() => setPanel(null), []);
  const value = useMemo(
    () => ({ open, close, panel, openPanel, setPanelIndex, closePanel }),
    [open, close, panel, openPanel, setPanelIndex, closePanel],
  );

  return (
    <PdfPreviewContext.Provider value={value}>
      {children}
      {/* target 整体替换：同文档换来源时仅跳页/换高亮，不重拉文件 */}
      <DocumentPreviewDialog
        open={target != null}
        onOpenChange={(next) => {
          if (!next) close();
        }}
        kbId={target?.kbId}
        docId={target?.docId}
        docName={target?.docName}
        fileSize={target?.fileSize ?? null}
        mode={target?.mode}
        initialPage={target?.page}
        highlights={target?.highlights}
      />
    </PdfPreviewContext.Provider>
  );
};

export function usePdfPreview(): PdfPreviewContextValue {
  const ctx = useContext(PdfPreviewContext);
  if (!ctx) {
    throw new Error("usePdfPreview must be used within PdfPreviewProvider");
  }
  return ctx;
}
