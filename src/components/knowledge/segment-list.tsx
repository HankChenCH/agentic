import { useState, type FC } from "react";
import {
  ChevronDownIcon,
  ChevronUpIcon,
  CrosshairIcon,
  PencilIcon,
  PowerIcon,
  Trash2Icon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { KnowledgeStatusBadge } from "@/components/knowledge/status-badge";
import { cn } from "@/lib/utils";
import type { BackendDocumentSegment } from "@/services/types";

/**
 * 分段卡片列表（文档详情页主体）。
 *
 * 每张卡片：位置号 + 状态徽章 + 溯源 chips（页码/标题路径）+ 内容
 * （默认折叠为 3 行，可展开）+ 行内操作（定位预览/编辑/启停/删除）。
 * 停用的分段整卡降透明度，定位预览依赖解析 meta（手动分段无页码则隐藏入口）。
 */

/** 内容超过该长度时默认折叠（3 行 line-clamp），可展开全文 */
const COLLAPSE_THRESHOLD = 150;

interface SegmentListProps {
  segments: BackendDocumentSegment[];
  onLocate: (segment: BackendDocumentSegment) => void;
  onEdit: (segment: BackendDocumentSegment) => void;
  onToggleEnabled: (segment: BackendDocumentSegment) => void;
  onDelete: (segment: BackendDocumentSegment) => void;
}

/** 页码 chip 文案（后端 0 起存储，展示 +1；无页码返回 null） */
function segmentPagesLabel(segment: BackendDocumentSegment): string | null {
  const start = segment.meta?.page_start;
  if (start == null) return null;
  const end = segment.meta?.page_end;
  if (end == null || end === start) return `页 ${start + 1}`;
  return `页 ${start + 1}-${end + 1}`;
}

export const SegmentList: FC<SegmentListProps> = ({
  segments,
  onLocate,
  onEdit,
  onToggleEnabled,
  onDelete,
}) => {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggleExpanded = (segmentId: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(segmentId)) {
        next.delete(segmentId);
      } else {
        next.add(segmentId);
      }
      return next;
    });
  };

  return (
    <div className="flex flex-col gap-3">
      {segments.map((segment) => {
        const pages = segmentPagesLabel(segment);
        const headingPath = segment.meta?.heading_path?.filter(Boolean) ?? [];
        const canLocate = segment.meta?.page_start != null;
        const isLong = segment.content.length > COLLAPSE_THRESHOLD;
        const isOpen = expanded.has(segment.id);
        const disabled = segment.status === "disabled";

        return (
          <div
            key={segment.id}
            className={cn(
              "flex flex-col gap-3 rounded-xl border border-border/80 bg-card p-4 shadow-card",
              disabled && "opacity-70",
            )}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-md bg-muted px-2 py-0.5 text-xs font-medium tabular-nums text-muted-foreground">
                #{segment.position}
              </span>
              <KnowledgeStatusBadge status={segment.status} />
              {pages && (
                <span className="rounded-md bg-primary/10 px-2 py-0.5 text-xs tabular-nums text-primary">
                  {pages}
                </span>
              )}
              {headingPath.length > 0 && (
                <span className="max-w-72 truncate text-xs text-muted-foreground" title={headingPath.join(" > ")}>
                  {headingPath.join(" > ")}
                </span>
              )}
              <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                {segment.word_count} 字
              </span>
            </div>

            <p
              className={cn(
                "whitespace-pre-wrap break-words text-sm leading-relaxed",
                !isOpen && "line-clamp-3",
              )}
            >
              {segment.content}
            </p>
            {isLong && (
              <button
                type="button"
                className="flex w-fit items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
                onClick={() => toggleExpanded(segment.id)}
              >
                {isOpen ? (
                  <>
                    <ChevronUpIcon className="size-3.5" />
                    收起
                  </>
                ) : (
                  <>
                    <ChevronDownIcon className="size-3.5" />
                    展开全文
                  </>
                )}
              </button>
            )}

            <div className="flex items-center gap-1 self-end">
              {canLocate && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground"
                  onClick={() => onLocate(segment)}
                >
                  <CrosshairIcon />
                  定位预览
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground"
                onClick={() => onEdit(segment)}
              >
                <PencilIcon />
                编辑
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground"
                onClick={() => onToggleEnabled(segment)}
              >
                <PowerIcon />
                {disabled ? "启用" : "停用"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => onDelete(segment)}
              >
                <Trash2Icon />
                删除
              </Button>
            </div>
          </div>
        );
      })}
    </div>
  );
};
