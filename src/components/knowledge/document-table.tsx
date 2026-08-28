import type { FC } from "react";
import { EyeIcon, FileTextIcon, MoreVerticalIcon } from "lucide-react";

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
 * 行级操作在右侧 dropdown：编辑元数据 / 启用停用 / 删除。
 * failed 状态的失败原因通过状态徽章的 tooltip 展示。
 */

interface DocumentTableProps {
  documents: BackendKnowledgeDocument[];
  onPreview: (doc: BackendKnowledgeDocument) => void;
  onEdit: (doc: BackendKnowledgeDocument) => void;
  onDelete: (doc: BackendKnowledgeDocument) => void;
  onToggleEnabled: (doc: BackendKnowledgeDocument) => void;
  /** 补发处理任务（pending 卡住或 failed 重试时展示） */
  onRetry?: (doc: BackendKnowledgeDocument) => void;
}

export const DocumentTable: FC<DocumentTableProps> = ({
  documents,
  onPreview,
  onEdit,
  onDelete,
  onToggleEnabled,
  onRetry,
}) => {
  return (
    <div className="overflow-hidden rounded-xl border border-border/80 bg-card shadow-card">
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
                      title="预览文档"
                      onClick={() => onPreview(doc)}
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
                {doc.seg_num}
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
                    <DropdownMenuItem onClick={() => onPreview(doc)}>
                      <EyeIcon />
                      预览
                    </DropdownMenuItem>
                    {onRetry &&
                      (doc.status === "failed" || doc.status === "pending") && (
                        <DropdownMenuItem onClick={() => onRetry(doc)}>
                          重新处理
                        </DropdownMenuItem>
                      )}
                    <DropdownMenuItem onClick={() => onEdit(doc)}>
                      编辑信息
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => onToggleEnabled(doc)}>
                      {doc.status === "enabled" ? "停用" : "启用"}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      variant="destructive"
                      onClick={() => onDelete(doc)}
                    >
                      删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
};
