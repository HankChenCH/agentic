import type { FC } from "react";
import { Loader2Icon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { KnowledgeStatus } from "@/services/types";

/**
 * 知识库/文档通用状态徽章（暖砂语义色）。
 *
 * 等待/停用用中性暖灰，处理/删除中用琥珀/橙的过渡色，就绪用橄榄绿、
 * 启用用品牌焦糖橘（「开」状态与主色对齐），失败用 destructive 赭红。
 * status=failed 且带 error_message 时包一层 Tooltip 展示失败原因。
 */

const STATUS_META: Record<
  KnowledgeStatus,
  { label: string; className: string }
> = {
  pending: {
    label: "等待中",
    className: "bg-muted text-muted-foreground",
  },
  processing: {
    label: "处理中",
    className: "bg-amber-500/15 text-amber-700",
  },
  ready: {
    label: "就绪",
    className: "bg-emerald-500/15 text-emerald-700",
  },
  enabled: {
    label: "已启用",
    className: "bg-primary/15 text-primary",
  },
  disabled: {
    label: "已停用",
    className: "bg-muted text-muted-foreground",
  },
  deleting: {
    label: "删除中",
    className: "bg-orange-500/15 text-orange-700",
  },
  failed: {
    label: "失败",
    className: "bg-destructive/15 text-destructive",
  },
};

interface KnowledgeStatusBadgeProps {
  status: KnowledgeStatus;
  /** 处理失败原因（仅 failed 状态展示） */
  errorMessage?: string | null;
  className?: string;
}

export const KnowledgeStatusBadge: FC<KnowledgeStatusBadgeProps> = ({
  status,
  errorMessage,
  className,
}) => {
  const meta = STATUS_META[status];

  const badge = (
    <Badge className={cn(meta.className, className)}>
      {status === "processing" && (
        <Loader2Icon className="animate-spin" data-icon="inline-start" />
      )}
      {meta.label}
    </Badge>
  );

  if (status !== "failed" || !errorMessage) return badge;

  return (
    <Tooltip>
      <TooltipTrigger render={<span className="inline-flex cursor-help" />}>
        {badge}
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-80 whitespace-pre-wrap">
        {errorMessage}
      </TooltipContent>
    </Tooltip>
  );
};
