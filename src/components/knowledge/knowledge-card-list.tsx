import type { FC } from "react";
import { FileTextIcon, MoreVerticalIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatDateTime } from "@/lib/format";
import type { BackendKnowledgeBase } from "@/services/types";
import { KnowledgeStatusBadge } from "@/components/knowledge/status-badge";

/**
 * 知识库卡片网格（列表页主体）。
 *
 * 卡片整体可点击进入详情；右上角 dropdown 承载行级操作（编辑/启停/删除），
 * trigger 上 stopPropagation 防止误触发卡片跳转。
 */

interface KnowledgeCardListProps {
  items: BackendKnowledgeBase[];
  onOpen: (kb: BackendKnowledgeBase) => void;
  onEdit: (kb: BackendKnowledgeBase) => void;
  onDelete: (kb: BackendKnowledgeBase) => void;
  onToggleEnabled: (kb: BackendKnowledgeBase) => void;
}

export const KnowledgeCardList: FC<KnowledgeCardListProps> = ({
  items,
  onOpen,
  onEdit,
  onDelete,
  onToggleEnabled,
}) => {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {items.map((kb) => (
        <Card
          key={kb.id}
          className="group cursor-pointer border-border/80 shadow-card transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-card-hover"
          onClick={() => onOpen(kb)}
        >
          <CardHeader>
            <CardTitle className="truncate">{kb.name}</CardTitle>
            <CardAction>
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`操作：${kb.name}`}
                    />
                  }
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreVerticalIcon />
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-36">
                  <DropdownMenuItem onClick={() => onEdit(kb)}>
                    编辑信息
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => onToggleEnabled(kb)}>
                    {kb.status === "enabled" ? "停用" : "启用"}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    variant="destructive"
                    onClick={() => onDelete(kb)}
                  >
                    删除
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </CardAction>
            <CardDescription className="line-clamp-2 min-h-10">
              {kb.description || "暂无描述"}
            </CardDescription>
          </CardHeader>
          <CardFooter className="justify-between text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <FileTextIcon className="size-3.5" />
              {kb.doc_num} 篇文档
            </span>
            <KnowledgeStatusBadge status={kb.status} />
            <span className="ml-auto">{formatDateTime(kb.updated_at)}</span>
          </CardFooter>
        </Card>
      ))}
    </div>
  );
};
