import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type FC,
} from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import "react-pdf/dist/Page/TextLayer.css";
import "react-pdf/dist/Page/AnnotationLayer.css";
import {
  AlertCircleIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Loader2Icon,
  RefreshCwIcon,
  ZoomInIcon,
  ZoomOutIcon,
} from "lucide-react";
import { toast } from "sonner";

// Vite 下 pdf.js worker 用 ?url 导入交给 bundler 处理（裸模块说明符
// 不能走 new URL(..., import.meta.url) 那套静态资源解析）
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { Button } from "@/components/ui/button";
import { BizError } from "@/lib/http";
import { cn } from "@/lib/utils";
import { knowledgeService } from "@/services/knowledge-service";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

// 部分中文/老版 PDF 依赖 cMap 与标准字体数据才能正确渲染文本层；
// 按运行时 pdfjs.version 钉住 CDN 版本，避免与打包的 pdfjs-dist 漂移
const PDF_OPTIONS = {
  cMapUrl: `https://unpkg.com/pdfjs-dist@${pdfjs.version}/cmaps/`,
  cMapPacked: true,
  standardFontDataUrl: `https://unpkg.com/pdfjs-dist@${pdfjs.version}/standard_fonts/`,
};

// 缩放范围与步进（显示为百分比）
const MIN_SCALE = 0.5;
const MAX_SCALE = 3;
const SCALE_STEP = 0.25;

// 连续滚动的懒渲染窗口：进入预取范围的页前后各保留几页真正栅格化，
// 其余页用等尺寸占位——浏览器 PDF 查看器同款策略，长文档不爆内存
const RENDER_WINDOW = 2;
// 预取缓冲：页进入视口前多少像素就开始栅格化，滚动时减少空白闪现
const PREFETCH_MARGIN = 600;

/** 溯源高亮区域：bbox 为 0-1 归一化（左上原点、相对页面宽高），缩放无关 */
export interface PdfHighlight {
  /** 页码（0 起，与后端 meta/page_idx 对齐） */
  page: number;
  bbox: readonly [number, number, number, number];
}

/** 预览模式：document 整篇连续滚动；snippet 仅渲染命中页（检索溯源） */
export type PdfViewerMode = "document" | "snippet";

/**
 * PDF 查看器（无弹窗壳，可独立嵌入）：浏览器式连续滚动渲染。
 *
 * 文件字节经 GET /knowledge/{kbId}/document/{docId}/file 拉取（ArrayBuffer
 * 全量载入，上传上限 50MB 内可接受）。全部页等尺寸占位、视口附近（预取
 * 缓冲 ±窗口）才真正栅格化；工具栏页码跟随视口中占比最大的页；缩放保持
 * 视口阅读位置。
 *
 * 双模式：``mode="document"``（默认，知识库管理整篇预览）如上；``mode=
 * "snippet"``（检索片段溯源）只渲染含高亮的页（无高亮退化为跳页页）、
 * 隐藏翻页按钮——避免整篇内容暴露，用户聚焦命中来源。
 *
 * 溯源受控：``initialPage``（0 起）变更时滚动定位到目标页；``highlights``
 * 里各页的 bbox 以百分比叠加层画框（MinerU 归一化坐标与渲染尺寸解耦），
 * 并等该页 canvas 首次栅格化完成后才淡入，避免高亮悬浮在占位灰底上的
 * 错位观感。弹窗形态见 ``DocumentPreviewDialog``（壳 + 本组件）。
 */

interface PdfViewerProps {
  kbId?: string;
  docId?: string;
  /** 预览模式，默认 document（整篇）；snippet 仅命中页（见组件注释） */
  mode?: PdfViewerMode;
  /** 打开/变更时滚动定位到的页（0 起）；不传停在顶部 */
  initialPage?: number;
  /** 溯源高亮区域（各页只画自己的框） */
  highlights?: readonly PdfHighlight[];
  /** 根容器样式覆盖：默认占满父容器（父级需给定高度或用 flex-1） */
  className?: string;
}

interface PageSize {
  w: number;
  h: number;
}

export const PdfViewer: FC<PdfViewerProps> = ({
  kbId,
  docId,
  mode = "document",
  initialPage,
  highlights,
  className,
}) => {
  const [data, setData] = useState<ArrayBuffer | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [numPages, setNumPages] = useState<number | null>(null);
  const [pageSizes, setPageSizes] = useState<PageSize[] | null>(null);
  const [scale, setScale] = useState(1);
  // 视口当前页（1 起）：由 IntersectionObserver 依可见占比同步
  const [currentPage, setCurrentPage] = useState(1);
  // 需要真正栅格化的页码集合（1 起）：预取范围内各页 ± 窗口
  const [renderPages, setRenderPages] = useState<Set<number>>(() => new Set());
  // canvas 已栅格化的页（1 起）：高亮层等首次栅格化完成后再淡入
  const [paintedPages, setPaintedPages] = useState<ReadonlySet<number>>(
    () => new Set(),
  );

  const containerRef = useRef<HTMLDivElement>(null);
  const numPagesRef = useRef<number | null>(null);
  const visibleRatioRef = useRef<Map<number, number>>(new Map());
  const visiblePagesRef = useRef<Set<number>>(new Set());
  // 缩放锚点：变更前视口顶所在页 + 页内偏移比例，缩放布局完成后恢复
  const scaleAnchorRef = useRef<{ page: number; frac: number } | null>(null);

  // 挂载（或切换文档/点重试）时拉取文件字节并重置视图状态
  useEffect(() => {
    if (!docId || !kbId) return;
    setData(null);
    setNumPages(null);
    numPagesRef.current = null;
    setPageSizes(null);
    setCurrentPage(1);
    setRenderPages(new Set());
    setPaintedPages(new Set());
    visibleRatioRef.current.clear();
    visiblePagesRef.current.clear();
    scaleAnchorRef.current = null;
    setScale(1);
    setFetching(true);
    setFetchError(null);
    knowledgeService
      .getDocumentFile(kbId, docId)
      .then((buf) => setData(buf))
      .catch((err: unknown) => {
        const message =
          err instanceof BizError || err instanceof Error
            ? err.message
            : "文件加载失败";
        setFetchError(message);
        toast.error(`文档预览失败：${message}`);
      })
      .finally(() => setFetching(false));
  }, [docId, kbId, reloadKey]);

  const handleDocumentLoad = useCallback(async (pdf: PDFDocumentProxy) => {
    setNumPages(pdf.numPages);
    numPagesRef.current = pdf.numPages;
    // 只取各页 viewport 元数据（不栅格化）确定占位尺寸：滚动条长度与
    // 跳页定位在页面渲染完成前就准确
    const sizes = await Promise.all(
      Array.from({ length: pdf.numPages }, (_, i) =>
        pdf.getPage(i + 1).then((page) => {
          const viewport = page.getViewport({ scale: 1 });
          return { w: viewport.width, h: viewport.height };
        }),
      ),
    );
    setPageSizes(sizes);
  }, []);

  // snippet 模式的受限页清单（1 起、升序去重）：含高亮的页；无高亮退化
  // 为跳页页（存量文档无 bbox 时按页码定位单页）。null = document 模式
  const snippetPages = useMemo(() => {
    if (mode !== "snippet") return null;
    const pages = new Set((highlights ?? []).map((hl) => hl.page + 1));
    if (pages.size === 0) pages.add((initialPage ?? 0) + 1);
    return [...pages].sort((a, b) => a - b);
  }, [mode, highlights, initialPage]);

  const handlePageRenderSuccess = useCallback((page: number) => {
    setPaintedPages((prev) => {
      if (prev.has(page)) return prev;
      const next = new Set(prev);
      next.add(page);
      return next;
    });
  }, []);

  // 页占位元素挂载后建立双观察器：窗口观察器（带预取缓冲）维护渲染集合，
  // 页码观察器（无缓冲）以可见占比最大的页为准——两者必须分开，否则
  // 预取到的视口外页面会被误判为当前页
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !numPages) return;

    const applyWindow = () => {
      const total = numPagesRef.current ?? 0;
      const next = new Set<number>();
      visiblePagesRef.current.forEach((p) => {
        for (let d = -RENDER_WINDOW; d <= RENDER_WINDOW; d++) {
          const q = p + d;
          if (q >= 1 && q <= total) next.add(q);
        }
      });
      setRenderPages(next);
    };

    const windowObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const page = Number((entry.target as HTMLElement).dataset.page);
          if (entry.isIntersecting) visiblePagesRef.current.add(page);
          else visiblePagesRef.current.delete(page);
        }
        applyWindow();
      },
      { root: container, rootMargin: `${PREFETCH_MARGIN}px 0px` },
    );

    const pageObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const page = Number((entry.target as HTMLElement).dataset.page);
          if (entry.isIntersecting) {
            visibleRatioRef.current.set(page, entry.intersectionRatio);
          } else {
            visibleRatioRef.current.delete(page);
          }
        }
        let best = 0;
        let bestRatio = -1;
        visibleRatioRef.current.forEach((ratio, page) => {
          if (ratio > bestRatio) {
            bestRatio = ratio;
            best = page;
          }
        });
        if (best > 0) setCurrentPage(best);
      },
      { root: container },
    );

    container
      .querySelectorAll<HTMLElement>("[data-page]")
      .forEach((el) => {
        windowObserver.observe(el);
        pageObserver.observe(el);
      });

    return () => {
      windowObserver.disconnect();
      pageObserver.disconnect();
      visibleRatioRef.current.clear();
      visiblePagesRef.current.clear();
    };
  }, [numPages, pageSizes]);

  // 溯源跳页：initialPage（0 起）变更时滚动定位。依赖 pageSizes 就绪后
  // 执行——占位尺寸已准确，目标页即使尚未栅格化 offsetTop 也正确
  useEffect(() => {
    if (initialPage == null || !pageSizes) return;
    const target = initialPage + 1;
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-page="${target}"]`,
    );
    if (el) el.scrollIntoView({ block: "start" });
  }, [initialPage, pageSizes]);

  // 缩放锚定：占位尺寸随 scale 同步更新（同步布局），在 DOM 变更后、
  // 绘制前把滚动位置恢复到「同页同比例」——不锚定时 zoom in 会让视口
  // 落到文档更靠前的位置，阅读位置丢失
  useLayoutEffect(() => {
    const anchor = scaleAnchorRef.current;
    if (!anchor) return;
    scaleAnchorRef.current = null;
    const container = containerRef.current;
    const target = container?.querySelector<HTMLElement>(
      `[data-page="${anchor.page}"]`,
    );
    if (!container || !target) return;
    const containerRect = container.getBoundingClientRect();
    const targetRect = target.getBoundingClientRect();
    const targetTopInContent =
      targetRect.top - containerRect.top + container.scrollTop;
    container.scrollTop = targetTopInContent + anchor.frac * target.offsetHeight;
  }, [scale]);

  const handleRetry = useCallback(() => setReloadKey((n) => n + 1), []);

  // pdf.js 加载时会把传入的 ArrayBuffer transfer/detach 给 worker;
  // StrictMode 双挂载或重试复用同一缓冲区会抛
  // "Cannot perform Construct on a detached ArrayBuffer"。
  // 因此每次挂载都传独立副本,同时用 useMemo 保持引用稳定避免重复触发加载。
  const fileProps = useMemo(
    () => (data ? { data: data.slice(0) } : null),
    [data],
  );

  const scrollToPage = useCallback((page: number) => {
    const el = containerRef.current?.querySelector<HTMLElement>(
      `[data-page="${page}"]`,
    );
    el?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, []);

  // 缩放前记录视口顶所在页 + 页内偏移比例（跨页间隙时退化为当前页顶部）
  const changeScale = useCallback(
    (delta: number) => {
      const container = containerRef.current;
      setScale((s) => {
        const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, s + delta));
        if (next === s || !container) return next;
        const containerRect = container.getBoundingClientRect();
        const scrollTop = container.scrollTop;
        let anchor: { page: number; frac: number } | null = null;
        container
          .querySelectorAll<HTMLElement>("[data-page]")
          .forEach((el) => {
            if (anchor) return;
            const top =
              el.getBoundingClientRect().top - containerRect.top + scrollTop;
            if (scrollTop >= top && scrollTop < top + el.offsetHeight) {
              anchor = {
                page: Number(el.dataset.page),
                frac: (scrollTop - top) / el.offsetHeight,
              };
            }
          });
        scaleAnchorRef.current = anchor ?? { page: currentPage, frac: 0 };
        return next;
      });
    },
    [currentPage],
  );

  const zoomIn = () => changeScale(SCALE_STEP);
  const zoomOut = () => changeScale(-SCALE_STEP);

  const canPrev = currentPage > 1;
  const canNext = numPages != null && currentPage < numPages;

  return (
    <div className={cn("flex h-full min-h-0 w-full flex-col", className)}>
      <div className="flex-none flex items-center justify-center gap-1.5 border-b px-4 py-2">
        {/* snippet 模式只有命中页，无翻页概念；隐藏前后页按钮 */}
        {!snippetPages && (
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="上一页"
            disabled={!canPrev}
            onClick={() => scrollToPage(currentPage - 1)}
          >
            <ChevronLeftIcon />
          </Button>
        )}
        {/* 页码跟随视口（可见占比最大的页） */}
        <span className="min-w-14 text-center text-xs tabular-nums text-muted-foreground">
          {numPages != null ? `${currentPage} / ${numPages}` : "– / –"}
        </span>
        {!snippetPages && (
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="下一页"
            disabled={!canNext}
            onClick={() => scrollToPage(currentPage + 1)}
          >
            <ChevronRightIcon />
          </Button>
        )}

        <span className="mx-2 h-4 w-px bg-border" aria-hidden="true" />

        <Button
          variant="outline"
          size="icon-sm"
          aria-label="缩小"
          disabled={scale <= MIN_SCALE}
          onClick={zoomOut}
        >
          <ZoomOutIcon />
        </Button>
        <span className="min-w-12 text-center text-xs tabular-nums text-muted-foreground">
          {Math.round(scale * 100)}%
        </span>
        <Button
          variant="outline"
          size="icon-sm"
          aria-label="放大"
          disabled={scale >= MAX_SCALE}
          onClick={zoomIn}
        >
          <ZoomInIcon />
        </Button>
      </div>

      <div
        ref={containerRef}
        className="flex min-h-0 flex-1 flex-col items-center overflow-auto bg-muted/40 p-4"
      >
        {fetching ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
            <Loader2Icon className="size-6 animate-spin" />
            <p className="text-sm">文件加载中…</p>
          </div>
        ) : fetchError ? (
          <div className="flex h-full flex-col items-center justify-center gap-3">
            <AlertCircleIcon className="size-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">{fetchError}</p>
            <Button variant="outline" size="sm" onClick={handleRetry}>
              <RefreshCwIcon />
              重试
            </Button>
          </div>
        ) : data && fileProps ? (
          <Document
            file={fileProps}
            options={PDF_OPTIONS}
            onLoadSuccess={handleDocumentLoad}
            onLoadError={(err) => toast.error(`PDF 解析失败：${err.message}`)}
            loading={
              <div className="flex h-full items-center">
                <Loader2Icon className="size-6 animate-spin text-muted-foreground" />
              </div>
            }
            error={
              <div className="flex flex-col items-center gap-2 p-8 text-muted-foreground">
                <AlertCircleIcon className="size-8 text-muted-foreground/50" />
                <p className="text-sm">PDF 解析失败</p>
              </div>
            }
          >
            {numPages && pageSizes
              ? // snippet：只渲染命中页（全部直接栅格化，无需懒渲染窗口）；
                // document：全部页按占位 + 懒渲染窗口栅格化
                (snippetPages ?? Array.from({ length: numPages }, (_, i) => i + 1))
                  .filter((page) => page >= 1 && page <= numPages)
                  .map((page) => {
                    const shouldRender = snippetPages
                      ? true
                      : renderPages.has(page);
                    const { w, h } = pageSizes[page - 1];
                    return (
                      <div
                        key={page}
                        data-page={page}
                        data-rendered={shouldRender ? "true" : "false"}
                        className="relative mx-auto mb-4 last:mb-0"
                        style={{ width: w * scale, height: h * scale }}
                      >
                        {shouldRender ? (
                          // Page 根 div 即本页渲染框：高亮叠加层以它为基准，
                          // bbox 百分比定位缩放无关
                          <Page
                            pageNumber={page}
                            scale={scale}
                            className="shadow-lg"
                            onRenderSuccess={() =>
                              handlePageRenderSuccess(page)
                            }
                            loading={
                              <div className="flex h-full items-center justify-center">
                                <Loader2Icon className="size-5 animate-spin text-muted-foreground" />
                              </div>
                            }
                          >
                            {/* 高亮层等 canvas 首次栅格化完成再淡入：高亮是
                                Page 的 children、page proxy 就绪即渲染，若不
                                门控会先于内容悬浮在灰底上（异步错位观感）；
                                只门控首次，缩放重渲染不隐藏避免闪烁 */}
                            <div
                              data-slot="pdf-highlight-layer"
                              className={cn(
                                "pointer-events-none absolute inset-0 z-10 transition-opacity duration-200",
                                paintedPages.has(page)
                                  ? "opacity-100"
                                  : "opacity-0",
                              )}
                            >
                              {(highlights ?? [])
                                .filter((hl) => hl.page === page - 1)
                                .map((hl, j) => {
                                  const [x0, y0, x1, y1] = hl.bbox;
                                  return (
                                    <div
                                      key={`${hl.page}-${j}`}
                                      data-slot="pdf-highlight-box"
                                      className="absolute rounded-sm border-2 border-amber-500/70 bg-amber-300/25"
                                      style={{
                                        left: `${x0 * 100}%`,
                                        top: `${y0 * 100}%`,
                                        width: `${(x1 - x0) * 100}%`,
                                        height: `${(y1 - y0) * 100}%`,
                                      }}
                                    />
                                  );
                                })}
                            </div>
                          </Page>
                        ) : (
                          // 未栅格化的页：等尺寸占位，保持滚动条/跳页定位准确
                          <div className="h-full w-full bg-background shadow-lg" />
                        )}
                      </div>
                    );
                  })
              : null}
          </Document>
        ) : null}
      </div>
    </div>
  );
};
