import { useState, type FC } from "react";
import { useNavigate } from "react-router";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  DatabaseIcon,
  PlusIcon,
  RefreshCwIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AdminPageShell } from "@/components/shared/admin-page-shell";
import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import { KnowledgeCardList } from "@/components/knowledge/knowledge-card-list";
import { KnowledgeFormDialog } from "@/components/knowledge/knowledge-form-dialog";
import { useKnowledgeList } from "@/hooks/use-knowledge-list";
import type { BackendKnowledgeBase } from "@/services/types";

/**
 * 知识库列表页（/admin/knowledge，管理侧知识库模块首屏）。
 *
 * 卡片网格 + 服务端分页；新建/编辑走表单弹窗，删除走确认弹窗。
 * 过渡态（入库/删除中）由 hook 自动轮询刷新。
 */

export const KnowledgeListPage: FC = () => {
  const navigate = useNavigate();
  const {
    items,
    total,
    page,
    pageSize,
    setPage,
    isLoading,
    error,
    refresh,
    createKnowledgeBase,
    updateKnowledgeBase,
    deleteKnowledgeBase,
    setKnowledgeEnabled,
  } = useKnowledgeList(12);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<BackendKnowledgeBase | null>(null);
  const [deleting, setDeleting] = useState<BackendKnowledgeBase | null>(null);

  const totalPage = Math.max(1, Math.ceil(total / pageSize));

  const openCreate = () => {
    setEditing(null);
    setFormOpen(true);
  };

  const openEdit = (kb: BackendKnowledgeBase) => {
    setEditing(kb);
    setFormOpen(true);
  };

  return (
    <AdminPageShell
      backTo="/admin"
      backLabel="返回控制台"
      title="知识库"
      description="管理知识库与文档，供对话检索引用"
      width="wide"
      actions={
        <Button onClick={openCreate}>
          <PlusIcon />
          新建知识库
        </Button>
      }
    >
      {isLoading ? (
          // 首屏/翻页骨架：与卡片网格同布局，避免跳动
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-36 rounded-xl" />
            ))}
          </div>
        ) : error ? (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-border/80 bg-card py-16 shadow-card">
            <p className="text-sm text-muted-foreground">
              列表加载失败：{error.message}
            </p>
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              <RefreshCwIcon />
              重试
            </Button>
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed py-16">
            <span className="flex size-14 items-center justify-center rounded-full bg-primary/10 text-primary">
              <DatabaseIcon className="size-6" />
            </span>
            <p className="text-sm text-muted-foreground">
              还没有知识库，创建一个开始积累资料吧
            </p>
            <Button size="sm" onClick={openCreate}>
              <PlusIcon />
              新建知识库
            </Button>
          </div>
        ) : (
          <KnowledgeCardList
            items={items}
            onOpen={(kb) => navigate(`/admin/knowledge/${kb.id}`)}
            onEdit={openEdit}
            onDelete={setDeleting}
            onToggleEnabled={(kb) =>
              void setKnowledgeEnabled(kb.id, kb.status !== "enabled")
            }
          />
        )}

        {total > pageSize && (
          <footer className="mt-auto flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
            <span>共 {total} 个知识库</span>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage(page - 1)}
              >
                <ChevronLeftIcon />
                上一页
              </Button>
              <span className="tabular-nums">
                {page} / {totalPage}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPage}
                onClick={() => setPage(page + 1)}
              >
                下一页
                <ChevronRightIcon />
              </Button>
            </div>
          </footer>
        )}

        <KnowledgeFormDialog
          open={formOpen}
          onOpenChange={setFormOpen}
          initial={editing}
          onSubmit={(values) =>
            editing
              ? updateKnowledgeBase(editing.id, values)
              : createKnowledgeBase(values)
          }
        />

        <ConfirmDialog
          open={deleting != null}
          onOpenChange={(open) => {
            if (!open) setDeleting(null);
          }}
          title={`删除知识库「${deleting?.name ?? ""}」？`}
          description="将删除库内全部文档与向量数据，操作不可恢复。"
          confirmText="删除"
          destructive
          onConfirm={() =>
            deleting ? deleteKnowledgeBase(deleting.id) : Promise.resolve(false)
          }
        />
    </AdminPageShell>
  );
};
