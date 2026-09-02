import { useEffect, useMemo, useState } from "react";
import { ChevronLeftIcon, ChevronRightIcon, XIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  usePdfPreview,
  type PdfSourcePanelState,
} from "@/components/shared/pdf-preview-provider";
import { PdfViewer } from "@/components/shared/pdf-viewer";
import { formatSourcePages } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { KnowledgeSource } from "@/services/types";

/**
 * 聊天检索溯源抽屉（右侧滑出、无遮罩、非模态）。
 *
 * 点来源卡片「查看原文」弹出，不遮挡对话区——打开期间点击消息里其他
 * 来源卡片、或用头部上一处/下一处按钮随时切换当前来源（仅替换内容，
 * 不重放进出动画）。同文档切换只跳页/换高亮不重拉文件（PdfViewer 按
 * props 更新）。关闭（X / Escape）滑出后卸载，释放 canvas。
 *
 * 仅挂在聊天页布局：路由离开时组件卸载并顺带清空 provider 的抽屉状态，
 * 避免返回聊天页时残影。
 */

/** 出场过渡时长（ms）：与样式里的 duration-200 保持一致，留少量余量 */
const EXIT_DURATION = 220;

/** 来源 → 查看器定位：跳页优先取第一个 bbox 的页码（比 page_start 更贴近命中位置） */
function sourceView(source: KnowledgeSource) {
  const highlights = (source.bboxes ?? []).map(([page, x0, y0, x1, y1]) => ({
    page,
    bbox: [x0, y0, x1, y1] as const,
  }));
  return {
    kbId: source.kb_id,
    docId: source.doc_id,
    page: source.bboxes?.[0]?.[0] ?? source.page_start ?? undefined,
    highlights,
  };
}

export const PdfSourcePanel = () => {
  const { panel, setPanelIndex, closePanel } = usePdfPreview();

  // 进出场动画：进场挂载首帧停在屏外、下一帧滑入；出场需保留末帧内容
  // 滑完再卸载——panel 置空后用 lastPanel 顶住渲染，动画结束清空
  const [entered, setEntered] = useState(false);
  const [lastPanel, setLastPanel] = useState<PdfSourcePanelState | null>(null);
  const active = panel ?? lastPanel;

  // panel 存在时持续记录末帧；进场首帧后触发滑入
  useEffect(() => {
    if (!panel) return;
    setLastPanel(panel);
    const raf = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(raf);
  }, [panel]);

  // panel 置空（关闭）→ 滑出，动画结束后真正卸载
  useEffect(() => {
    if (panel || !lastPanel) return;
    setEntered(false);
    const timer = setTimeout(() => setLastPanel(null), EXIT_DURATION);
    return () => clearTimeout(timer);
  }, [panel, lastPanel]);

  // Escape 收起（无遮罩层，键盘关闭走全局监听）
  useEffect(() => {
    if (!active) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePanel();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active, closePanel]);

  // 离开聊天页（组件卸载）时清空抽屉状态，避免返回时残影
  useEffect(() => () => closePanel(), [closePanel]);

  const source = active ? active.sources[active.activeIndex] : undefined;
  const view = useMemo(() => (source ? sourceView(source) : null), [source]);

  if (!active || !source || !view) return null;
  const pages = formatSourcePages(source);

  return (
    <aside
      data-slot="pdf-source-panel"
      aria-label="检索来源预览"
      className={cn(
        // 移动端全屏（44vw 在窄屏只剩 ~170px 不可用），sm 起恢复右侧滑出面板
        "fixed inset-y-0 right-0 z-40 flex w-full flex-col border-l bg-background shadow-xl transition-transform duration-200 ease-out sm:w-[min(620px,44vw)]",
        entered ? "translate-x-0" : "translate-x-full",
      )}
    >
      <header className="flex flex-none flex-col gap-1.5 border-b px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <Badge variant="secondary" className="shrink-0 tabular-nums">
            {source.index}
          </Badge>
          <span className="truncate text-sm font-medium">{source.doc_name}</span>
          <div className="ml-auto flex shrink-0 items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="上一个来源"
              disabled={active.activeIndex <= 0}
              onClick={() => setPanelIndex(active.activeIndex - 1)}
            >
              <ChevronLeftIcon />
            </Button>
            <span className="min-w-10 text-center text-xs tabular-nums text-muted-foreground">
              {active.activeIndex + 1} / {active.sources.length}
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="下一个来源"
              disabled={active.activeIndex >= active.sources.length - 1}
              onClick={() => setPanelIndex(active.activeIndex + 1)}
            >
              <ChevronRightIcon />
            </Button>
            <span className="mx-1 h-4 w-px bg-border" aria-hidden="true" />
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="收起来源抽屉"
              onClick={closePanel}
            >
              <XIcon />
            </Button>
          </div>
        </div>
        <p className="truncate text-xs text-muted-foreground">
          {pages && `${pages} · `}
          {typeof source.score === "number" &&
            `相关度 ${(source.score * 100).toFixed(0)}% · `}
          检索片段 · 仅展示命中页
        </p>
      </header>
      {/* 查看器随抽屉内容挂载/卸载：关闭即释放 canvas，重开重新取数 */}
      <PdfViewer
        className="min-h-0 flex-1"
        kbId={view.kbId}
        docId={view.docId}
        mode="snippet"
        initialPage={view.page}
        highlights={view.highlights}
      />
    </aside>
  );
};
