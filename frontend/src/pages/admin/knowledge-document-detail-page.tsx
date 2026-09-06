import { useEffect, useState, type FC } from "react";
import { useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  EyeIcon,
  LayersIcon,
  Loader2Icon,
  MoreVerticalIcon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  TriangleAlertIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { AdminPageShell } from "@/components/shared/admin-page-shell";
import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import { DocumentFormDialog } from "@/components/knowledge/document-form-dialog";
import { SegmentFormDialog } from "@/components/knowledge/segment-form-dialog";
import { SegmentList } from "@/components/knowledge/segment-list";
import { KnowledgeStatusBadge } from "@/components/knowledge/status-badge";
import { usePdfPreview } from "@/components/shared/pdf-preview-provider";
import { useKnowledgeDocument } from "@/hooks/use-knowledge-document";
import { useKnowledgeSegments } from "@/hooks/use-knowledge-segments";
import { isTransitional } from "@/hooks/use-knowledge-list";
import { formatDateTime, formatFileSize } from "@/lib/format";
import type { BackendDocumentSegment } from "@/services/types";

/**
 * 文档详情页（/admin/knowledge/:kbId/document/:docId）。
 *
 * 头部为文档信息 + 操作（预览原文 / 编辑 / 启停 / 重分段 / 删除），主体为
 * 分段卡片列表：查看/搜索/定位预览 + 新增/编辑/启停/删除。文档解析中
 * （pending/processing）分段集合正被流水线重写，只读提示不开放管理。
 */

// 分段卡片较多且高，每页条数小于文档列表
const SEGMENT_PAGE_SIZE = 10;

export const KnowledgeDocumentDetailPage: FC = () => {
  const { kbId, docId } = useParams<{ kbId: string; docId: string }>();
  const navigate = useNavigate();
  const pdfPreview = usePdfPreview();

  const doc = useKnowledgeDocument(kbId, docId);
  const segments = useKnowledgeSegments(kbId, docId, SEGMENT_PAGE_SIZE);
  const docInfo = doc.document;

  // 文档不存在（已删完/ID 非法）→ 提示并跳回知识库详情
  useEffect(() => {
    if (doc.notFound) {
      toast.error("文档不存在或已被删除");
      if (kbId) void navigate(`/admin/knowledge/${kbId}`, { replace: true });
    }
  }, [doc.notFound, kbId, navigate]);

  const [docFormOpen, setDocFormOpen] = useState(false);
  const [deletingDoc, setDeletingDoc] = useState(false);
  const [rechunkOpen, setRechunkOpen] = useState(false);
  const [segmentForm, setSegmentForm] = useState<
    { mode: "create" } | { mode: "edit"; segment: BackendDocumentSegment } | null
  >(null);
  const [deletingSegment, setDeletingSegment] =
    useState<BackendDocumentSegment | null>(null);

  const totalPage = Math.max(1, Math.ceil(segments.total / SEGMENT_PAGE_SIZE));
  const docEnabled = docInfo?.status === "enabled";
  // 分段管理闸门：稳定态才可写（pending/processing 期间流水线正重写分段集合）
  const canManageSegments =
    docInfo != null &&
    (docInfo.status === "ready" ||
      docInfo.status === "enabled" ||
      docInfo.status === "disabled");

  const openSegmentPreview = (segment: BackendDocumentSegment) => {
    if (!kbId || !docId || !docInfo) return;
    const meta = segment.meta;
    const page = meta?.page_start;
    const highlights = meta?.bboxes?.length
      ? meta.bboxes.map((b) => ({
          page: b.page,
          bbox: b.bbox as [number, number, number, number],
        }))
      : undefined;
    pdfPreview.open({
      kbId,
      docId,
      docName: docInfo.name,
      fileSize: docInfo.file_size,
      mode: "document",
      ...(page != null ? { page } : {}),
      ...(highlights ? { highlights } : {}),
    });
  };

  return (
    <AdminPageShell
      backTo={`/admin/knowledge/${kbId ?? ""}`}
      backLabel="返回知识库"
      width="wide"
      title={
        doc.error ? (
          <div className="flex items-center gap-3">
            <p className="text-sm text-muted-foreground">
              文档信息加载失败：{doc.error.message}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void doc.refresh()}
            >
              <RefreshCwIcon />
              重试
            </Button>
          </div>
        ) : doc.isLoading || !docInfo ? (
          <div className="grid gap-2">
            <Skeleton className="h-7 w-64 max-w-full" />
            <Skeleton className="h-4 w-96 max-w-full" />
          </div>
        ) : (
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <h1 className="font-heading truncate text-2xl font-semibold tracking-tight sm:text-3xl">
                {docInfo.name}
              </h1>
              <KnowledgeStatusBadge
                status={docInfo.status}
                errorMessage={docInfo.error_message}
              />
            </div>
            {docInfo.description && (
              <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                {docInfo.description}
              </p>
            )}
            <p className="mt-1.5 text-xs text-muted-foreground">
              {formatFileSize(docInfo.file_size)} · {docInfo.seg_num} 段 ·
              更新于 {formatDateTime(docInfo.updated_at)}
            </p>
          </div>
        )
      }
      actions={
        doc.error || doc.isLoading || !docInfo ? null : (
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="outline"
              onClick={() =>
                pdfPreview.open({
                  kbId: kbId!,
                  docId: docId!,
                  docName: docInfo.name,
                  fileSize: docInfo.file_size,
                })
              }
            >
              <EyeIcon />
              预览原文
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={
                  <Button
                    variant="outline"
                    size="icon"
                    aria-label="文档操作"
                  />
                }
              >
                <MoreVerticalIcon />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-36">
                <DropdownMenuItem onClick={() => setDocFormOpen(true)}>
                  编辑信息
                </DropdownMenuItem>
                {(docInfo.status === "enabled" ||
                  docInfo.status === "disabled") && (
                  <DropdownMenuItem
                    onClick={() => void doc.setDocumentEnabled(!docEnabled)}
                  >
                    {docEnabled ? "停用文档" : "启用文档"}
                  </DropdownMenuItem>
                )}
                {docInfo.status === "failed" && (
                  <DropdownMenuItem onClick={() => void doc.retryDocument()}>
                    重新处理
                  </DropdownMenuItem>
                )}
                {canManageSegments && (
                  <DropdownMenuItem onClick={() => setRechunkOpen(true)}>
                    重新分段
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  variant="destructive"
                  onClick={() => setDeletingDoc(true)}
                >
                  删除文档
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        )
      }
    >
      {docInfo && isTransitional(docInfo.status) ? (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-border/80 bg-card py-16 shadow-card">
            <Loader2Icon className="size-6 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {docInfo.status === "deleting"
                ? "文档删除中，分段与向量数据正在清理"
                : "文档解析中，分段将在处理完成后可用"}
            </p>
          </div>
        ) : docInfo && docInfo.status === "failed" ? (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 py-16">
            <TriangleAlertIcon className="size-6 text-destructive" />
            <p className="max-w-xl text-center text-sm text-muted-foreground">
              {docInfo.error_message || "文档处理失败"}
            </p>
            <Button variant="outline" size="sm" onClick={() => void doc.retryDocument()}>
              <RefreshCwIcon />
              重新处理
            </Button>
          </div>
        ) : (
          <>
            {/* 分段工具条：搜索 / 刷新 / 新增 */}
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <SearchIcon className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={segments.keyword}
                  placeholder="搜索分段内容…"
                  className="pl-9"
                  onChange={(e) => segments.setKeyword(e.target.value)}
                />
              </div>
              <Button
                variant="outline"
                size="icon"
                aria-label="刷新分段"
                onClick={() => void segments.refresh()}
              >
                <RefreshCwIcon />
              </Button>
              {canManageSegments && (
                <Button onClick={() => setSegmentForm({ mode: "create" })}>
                  <PlusIcon />
                  新增分段
                </Button>
              )}
            </div>

            {segments.isLoading ? (
              <div className="grid gap-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-28 rounded-xl" />
                ))}
              </div>
            ) : segments.error ? (
              <div className="flex flex-col items-center gap-3 rounded-xl border border-border/80 bg-card py-16 shadow-card">
                <p className="text-sm text-muted-foreground">
                  分段加载失败：{segments.error.message}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void segments.refresh()}
                >
                  <RefreshCwIcon />
                  重试
                </Button>
              </div>
            ) : segments.segments.length === 0 ? (
              segments.keyword.trim() ? (
                <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-16">
                  <SearchIcon className="size-6 text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">
                    未找到匹配「{segments.keyword.trim()}」的分段
                  </p>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed py-16">
                  <span className="flex size-14 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <LayersIcon className="size-6" />
                  </span>
                  <p className="text-sm text-muted-foreground">
                    还没有分段，可手动新增或对文档重新分段
                  </p>
                  {canManageSegments && (
                    <Button size="sm" onClick={() => setSegmentForm({ mode: "create" })}>
                      <PlusIcon />
                      新增分段
                    </Button>
                  )}
                </div>
              )
            ) : (
              <SegmentList
                segments={segments.segments}
                onLocate={openSegmentPreview}
                onEdit={(segment) =>
                  setSegmentForm({ mode: "edit", segment })
                }
                onToggleEnabled={(segment) =>
                  void segments.setSegmentEnabled(
                    segment.id,
                    segment.status === "disabled",
                  )
                }
                onDelete={setDeletingSegment}
              />
            )}

            {segments.total > SEGMENT_PAGE_SIZE && (
              <footer className="mt-auto flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
                <span>共 {segments.total} 段</span>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={segments.page <= 1}
                    onClick={() => segments.setPage(segments.page - 1)}
                  >
                    <ChevronLeftIcon />
                    上一页
                  </Button>
                  <span className="tabular-nums">
                    {segments.page} / {totalPage}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={segments.page >= totalPage}
                    onClick={() => segments.setPage(segments.page + 1)}
                  >
                    下一页
                    <ChevronRightIcon />
                  </Button>
                </div>
              </footer>
            )}
          </>
        )}

      <DocumentFormDialog
        open={docFormOpen}
        onOpenChange={setDocFormOpen}
        document={docInfo}
        onSubmit={(values) => doc.updateDocument(values)}
      />

      <SegmentFormDialog
        open={segmentForm != null}
        onOpenChange={(open) => {
          if (!open) setSegmentForm(null);
        }}
        segment={segmentForm?.mode === "edit" ? segmentForm.segment : null}
        onSubmit={(values) =>
          segmentForm == null
            ? Promise.resolve(false)
            : segmentForm.mode === "create"
              ? segments.createSegment(values.content)
              : segments.updateSegment(segmentForm.segment.id, values.content)
        }
      />

      <ConfirmDialog
        open={deletingDoc}
        onOpenChange={setDeletingDoc}
        title={`删除文档「${docInfo?.name ?? ""}」？`}
        description="将删除文档全部分段与向量数据，操作不可恢复。"
        confirmText="删除"
        destructive
        onConfirm={async () => {
          const ok = await doc.deleteDocument();
          if (ok && kbId) void navigate(`/admin/knowledge/${kbId}`);
          return ok;
        }}
      />

      <ConfirmDialog
        open={rechunkOpen}
        onOpenChange={setRechunkOpen}
        title={`重新分段「${docInfo?.name ?? ""}」？`}
        description="将重新解析并分块整篇文档，向量全量重写；手动新增的分段与分段停用状态会被替换。完成后文档回到就绪状态，如需参与召回请重新启用。"
        confirmText="重新分段"
        onConfirm={() => doc.rechunkDocument()}
      />

      <ConfirmDialog
        open={deletingSegment != null}
        onOpenChange={(open) => {
          if (!open) setDeletingSegment(null);
        }}
        title={`删除分段 #${deletingSegment?.position ?? ""}？`}
        description="将删除该分段及其向量数据，操作不可恢复。"
        confirmText="删除"
        destructive
        onConfirm={() =>
          deletingSegment
            ? segments.deleteSegment(deletingSegment.id)
            : Promise.resolve(false)
        }
      />
    </AdminPageShell>
  );
};
