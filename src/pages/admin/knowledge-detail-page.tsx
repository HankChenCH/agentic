import { useEffect, useState, type FC } from "react";
import { useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import {
  ArrowLeftIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  FileTextIcon,
  MoreVerticalIcon,
  RefreshCwIcon,
  UploadIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import { DocumentFormDialog } from "@/components/knowledge/document-form-dialog";
import { DocumentTable } from "@/components/knowledge/document-table";
import { KnowledgeFormDialog } from "@/components/knowledge/knowledge-form-dialog";
import { KnowledgeStatusBadge } from "@/components/knowledge/status-badge";
import { UploadDocumentDialog } from "@/components/knowledge/upload-document-dialog";
import { usePdfPreview } from "@/components/shared/pdf-preview-provider";
import { useKnowledgeBase } from "@/hooks/use-knowledge-base";
import { useKnowledgeDocuments } from "@/hooks/use-knowledge-documents";
import type { BackendKnowledgeDocument } from "@/services/types";

/**
 * 知识库详情页（/admin/knowledge/:kbId）。
 *
 * 头部为知识库信息 + 操作（上传文档 / 编辑 / 启停 / 删除），主体为文档
 * 表格。文档解析/删除为异步，列表由 hook 自动轮询跟进状态。
 */

export const KnowledgeDetailPage: FC = () => {
  const { kbId } = useParams<{ kbId: string }>();
  const navigate = useNavigate();

  const kb = useKnowledgeBase(kbId);
  const docs = useKnowledgeDocuments(kbId, 20);
  const pdfPreview = usePdfPreview();

  // 知识库不存在（已删完/ID 非法）→ 提示并跳回列表
  useEffect(() => {
    if (kb.notFound) {
      toast.error("知识库不存在或已被删除");
      void navigate("/admin/knowledge", { replace: true });
    }
  }, [kb.notFound, navigate]);

  const [formOpen, setFormOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deletingKb, setDeletingKb] = useState(false);
  const [editingDoc, setEditingDoc] = useState<BackendKnowledgeDocument | null>(
    null,
  );
  const [deletingDoc, setDeletingDoc] =
    useState<BackendKnowledgeDocument | null>(null);

  const totalPage = Math.max(1, Math.ceil(docs.total / docs.pageSize));
  const kbEnabled = kb.knowledgeBase?.status === "enabled";

  return (
    <div className="h-screen overflow-auto bg-background">
      <div className="mx-auto flex min-h-full max-w-6xl flex-col gap-6 p-6 lg:p-8">
        <header className="flex flex-col gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="-ml-2 w-fit text-muted-foreground"
            onClick={() => void navigate("/admin/knowledge")}
          >
            <ArrowLeftIcon />
            返回知识库
          </Button>

          {kb.error ? (
            <div className="flex items-center gap-3">
              <p className="text-sm text-muted-foreground">
                知识库信息加载失败：{kb.error.message}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void kb.refresh()}
              >
                <RefreshCwIcon />
                重试
              </Button>
            </div>
          ) : kb.isLoading || !kb.knowledgeBase ? (
            <div className="grid gap-2">
              <Skeleton className="h-7 w-64" />
              <Skeleton className="h-4 w-96" />
            </div>
          ) : (
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="flex items-center gap-2.5">
                  <h1 className="font-heading truncate text-2xl font-semibold tracking-tight">
                    {kb.knowledgeBase.name}
                  </h1>
                  <KnowledgeStatusBadge status={kb.knowledgeBase.status} />
                </div>
                <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                  {kb.knowledgeBase.description || "暂无描述"}
                </p>
                <p className="mt-1.5 text-xs text-muted-foreground">
                  嵌入模型 {kb.knowledgeBase.embedding_model} · 共{" "}
                  {kb.knowledgeBase.doc_num} 篇文档
                </p>
              </div>

              <div className="flex shrink-0 items-center gap-2">
                <Button onClick={() => setUploadOpen(true)}>
                  <UploadIcon />
                  上传文档
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger
                    render={
                      <Button
                        variant="outline"
                        size="icon"
                        aria-label="知识库操作"
                      />
                    }
                  >
                    <MoreVerticalIcon />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-36">
                    <DropdownMenuItem onClick={() => setFormOpen(true)}>
                      编辑信息
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() =>
                        void kb.setKnowledgeEnabled(!kbEnabled)
                      }
                    >
                      {kbEnabled ? "停用" : "启用"}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      variant="destructive"
                      onClick={() => setDeletingKb(true)}
                    >
                      删除知识库
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>
          )}
        </header>

        {docs.isLoading ? (
          <div className="grid gap-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 rounded-lg" />
            ))}
          </div>
        ) : docs.error ? (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-border/80 bg-card py-16 shadow-card">
            <p className="text-sm text-muted-foreground">
              文档加载失败：{docs.error.message}
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void docs.refresh()}
            >
              <RefreshCwIcon />
              重试
            </Button>
          </div>
        ) : docs.documents.length === 0 ? (
          <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed py-16">
            <span className="flex size-14 items-center justify-center rounded-full bg-primary/10 text-primary">
              <FileTextIcon className="size-6" />
            </span>
            <p className="text-sm text-muted-foreground">
              还没有文档，上传后自动解析入库
            </p>
            <Button size="sm" onClick={() => setUploadOpen(true)}>
              <UploadIcon />
              上传文档
            </Button>
          </div>
        ) : (
          <DocumentTable
            documents={docs.documents}
            onOpenDetail={(doc) => {
              if (kbId) void navigate(`/admin/knowledge/${kbId}/document/${doc.id}`);
            }}
            onPreview={(doc) => {
              if (kbId) {
                pdfPreview.open({
                  kbId,
                  docId: doc.id,
                  docName: doc.name,
                  fileSize: doc.file_size,
                });
              }
            }}
            onEdit={setEditingDoc}
            onDelete={setDeletingDoc}
            onToggleEnabled={(doc) =>
              void docs.setDocumentEnabled(doc.id, doc.status !== "enabled")
            }
            onRetry={(doc) => void docs.retryDocument(doc.id)}
          />
        )}

        {docs.total > docs.pageSize && (
          <footer className="mt-auto flex items-center justify-between text-sm text-muted-foreground">
            <span>共 {docs.total} 篇文档</span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={docs.page <= 1}
                onClick={() => docs.setPage(docs.page - 1)}
              >
                <ChevronLeftIcon />
                上一页
              </Button>
              <span className="tabular-nums">
                {docs.page} / {totalPage}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={docs.page >= totalPage}
                onClick={() => docs.setPage(docs.page + 1)}
              >
                下一页
                <ChevronRightIcon />
              </Button>
            </div>
          </footer>
        )}
      </div>

      <KnowledgeFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        initial={kb.knowledgeBase}
        onSubmit={(values) => kb.updateKnowledgeBase(values)}
      />

      <UploadDocumentDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUpload={docs.uploadDocument}
      />

      <DocumentFormDialog
        open={editingDoc != null}
        onOpenChange={(open) => {
          if (!open) setEditingDoc(null);
        }}
        document={editingDoc}
        onSubmit={(values) =>
          editingDoc
            ? docs.updateDocument(editingDoc.id, values)
            : Promise.resolve(false)
        }
      />

      <ConfirmDialog
        open={deletingDoc != null}
        onOpenChange={(open) => {
          if (!open) setDeletingDoc(null);
        }}
        title={`删除文档「${deletingDoc?.name ?? ""}」？`}
        description="将删除文档全部分段与向量数据，操作不可恢复。"
        confirmText="删除"
        destructive
        onConfirm={() =>
          deletingDoc
            ? docs.deleteDocument(deletingDoc.id)
            : Promise.resolve(false)
        }
      />

      <ConfirmDialog
        open={deletingKb}
        onOpenChange={setDeletingKb}
        title={`删除知识库「${kb.knowledgeBase?.name ?? ""}」？`}
        description="将删除库内全部文档与向量数据，操作不可恢复。"
        confirmText="删除"
        destructive
        onConfirm={async () => {
          const ok = await kb.deleteKnowledgeBase();
          if (ok) void navigate("/admin/knowledge");
          return ok;
        }}
      />
    </div>
  );
};
