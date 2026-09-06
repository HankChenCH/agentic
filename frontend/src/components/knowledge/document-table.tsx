import type { FC } from "react";
import {
  EyeIcon,
  FileTextIcon,
  LayersIcon,
  MoreVerticalIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDateTime, formatFileSize } from "@/lib/format";
import type { BackendKnowledgeDocument } from "@/services/types";
import { KnowledgeStatusBadge } from "@/components/knowledge/status-badge";

/**
 * 知识库文档表格（详情页主体）。
 *
 * 同一份操作数据渲染两种形态：md+ 为表格（行级操作在右侧 dropdown），
 * 窄屏为卡片列表（元信息折行、操作仍在 dropdown）。failed 状态的失败
 * 原因通过状态徽章的 tooltip 展示。
 */

interface DocumentAction {
  key: string;
  label: string;
  icon?: FC<{ className?: string }>;
  onSelect: () => void;
  destructive?: boolean;
  /** 渲染在该项之前的分隔线 */
  separatorBefore?: boolean;
}

/** 行操作单一数据源：桌面表格与移动卡片的 dropdown 共用，避免双份维护 */
const buildDocumentActions = (
  doc: BackendKnowledgeDocument,
  handlers: {
    onOpenDetail: () => void;
    onPreview: () => void;
    onEdit: () => void;
    onDelete: () => void;
    onToggleEnabled: () => void;
    onRetry?: () => void;
  },
): DocumentAction[] => {
  const actions: DocumentAction[] = [
    { key: "detail", label: "分段详情", icon: LayersIcon, onSelect: handlers.onOpenDetail },
    { key: "preview", label: "预览", icon: EyeIcon, onSelect: handlers.onPreview },
  ];
  if (handlers.onRetry && (doc.status === "failed" || doc.status === "pending")) {
    actions.push({ key: "retry", label: "重新处理", icon: RefreshCwIcon, onSelect: handlers.onRetry });
  }
  actions.push(
    { key: "edit", label: "编辑信息", onSelect: handlers.onEdit },
    { key: "toggle", label: doc.status === "enabled" ? "停用" : "启用", onSelect: handlers.onToggleEnabled },
    {
      key: "delete",
      label: "删除",
      icon: Trash2Icon,
      onSelect: handlers.onDelete,
      destructive: true,
      separatorBefore: true,
    },
  );
  return actions;
};

const DocumentActionMenu: FC<{
  doc: BackendKnowledgeDocument;
  actions: DocumentAction[];
}> = ({ doc, actions }) => (
  <DropdownMenu>
    <DropdownMenuTrigger
      render={
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`操作：${doc.name}`}
        />
      }
    >
      <MoreVerticalIcon />
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end" className="w-36">
      {actions.map((action) => (
        <FragmentWithSeparator key={action.key} separator={action.separatorBefore}>
          <DropdownMenuItem
            variant={action.destructive ? "destructive" : undefined}
            onClick={action.onSelect}
          >
            {action.icon && <action.icon />}
            {action.label}
          </DropdownMenuItem>
        </FragmentWithSeparator>
      ))}
    </DropdownMenuContent>
  </DropdownMenu>
);

/** 带 可选前缘分隔线 的条目包装（key 由外层承担） */
const FragmentWithSeparator: FC<{ separator?: boolean; children: React.ReactNode }> = ({
  separator,
  children,
}) => (
  <>
    {separator && <DropdownMenuSeparator />}
    {children}
  </>
);

/** 窄屏文档卡：名称/描述 + 状态 + 元信息折行 + 同一套操作菜单 */
const DocumentCard: FC<{
  doc: BackendKnowledgeDocument;
  actions: DocumentAction[];
  onOpenDetail: () => void;
}> = ({ doc, actions, onOpenDetail }) => (
  <div className="rounded-xl border border-border/80 bg-card shadow-card">
    <div className="flex items-start justify-between gap-2 px-3 pt-3">
      <button
        type="button"
        className="flex min-w-0 items-start gap-2 text-left"
        title="查看分段详情"
        onClick={onOpenDetail}
      >
        <FileTextIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{doc.name}</span>
          {doc.description && (
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {doc.description}
            </span>
          )}
        </span>
      </button>
      <DocumentActionMenu doc={doc} actions={actions} />
    </div>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 pb-3 pt-2 text-xs text-muted-foreground tabular-nums">
      <KnowledgeStatusBadge status={doc.status} errorMessage={doc.error_message} />
      <span>{formatFileSize(doc.file_size)}</span>
      <span>{doc.seg_num} 段</span>
      <span>权重 {doc.weight}</span>
      <span>{formatDateTime(doc.updated_at)}</span>
    </div>
  </div>
);

interface DocumentTableProps {
  documents: BackendKnowledgeDocument[];
  /** 进入文档详情页（分段管理） */
  onOpenDetail: (doc: BackendKnowledgeDocument) => void;
  onPreview: (doc: BackendKnowledgeDocument) => void;
  onEdit: (doc: BackendKnowledgeDocument) => void;
  onDelete: (doc: BackendKnowledgeDocument) => void;
  onToggleEnabled: (doc: BackendKnowledgeDocument) => void;
  /** 补发处理任务（pending 卡住或 failed 重试时展示） */
  onRetry?: (doc: BackendKnowledgeDocument) => void;
}

export const DocumentTable: FC<DocumentTableProps> = ({
  documents,
  onOpenDetail,
  onPreview,
  onEdit,
  onDelete,
  onToggleEnabled,
  onRetry,
}) => {
  const handlersFor = (doc: BackendKnowledgeDocument) => ({
    onOpenDetail: () => onOpenDetail(doc),
    onPreview: () => onPreview(doc),
    onEdit: () => onEdit(doc),
    onDelete: () => onDelete(doc),
    onToggleEnabled: () => onToggleEnabled(doc),
    onRetry: onRetry ? () => onRetry(doc) : undefined,
  });

  return (
    <>
      {/* 窄屏：卡片列表 */}
      <div className="flex flex-col gap-3 md:hidden">
        {documents.map((doc) => (
          <DocumentCard
            key={doc.id}
            doc={doc}
            actions={buildDocumentActions(doc, handlersFor(doc))}
            onOpenDetail={() => onOpenDetail(doc)}
          />
        ))}
      </div>

      {/* md+：完整表格 */}
      <div className="hidden overflow-hidden rounded-xl border border-border/80 bg-card shadow-card md:block">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/60 hover:bg-muted/60">
              <TableHead className="pl-4">文档</TableHead>
              <TableHead>大小</TableHead>
              <TableHead>分段</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>权重</TableHead>
              <TableHead>更新时间</TableHead>
              <TableHead className="w-12 pr-4" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {documents.map((doc) => (
              <TableRow key={doc.id}>
                <TableCell className="max-w-72 pl-4">
                  <div className="flex items-center gap-2">
                    <FileTextIcon className="size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <button
                        type="button"
                        className="max-w-full truncate text-left font-medium hover:underline"
                        title="查看分段详情"
                        onClick={() => onOpenDetail(doc)}
                      >
                        {doc.name}
                      </button>
                      {doc.description && (
                        <p className="truncate text-xs text-muted-foreground">
                          {doc.description}
                        </p>
                      )}
                    </div>
                  </div>
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatFileSize(doc.file_size)}
                </TableCell>
                <TableCell className="text-muted-foreground tabular-nums">
                  <button
                    type="button"
                    className="hover:text-foreground hover:underline"
                    title="查看分段详情"
                    onClick={() => onOpenDetail(doc)}
                  >
                    {doc.seg_num}
                  </button>
                </TableCell>
                <TableCell>
                  <KnowledgeStatusBadge
                    status={doc.status}
                    errorMessage={doc.error_message}
                  />
                </TableCell>
                <TableCell className="text-muted-foreground tabular-nums">
                  {doc.weight}
                </TableCell>
                <TableCell className="text-muted-foreground tabular-nums">
                  {formatDateTime(doc.updated_at)}
                </TableCell>
                <TableCell className="pr-4">
                  <DocumentActionMenu
                    doc={doc}
                    actions={buildDocumentActions(doc, handlersFor(doc))}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </>
  );
};
