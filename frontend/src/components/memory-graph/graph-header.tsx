import { type FC } from "react";
import { useNavigate } from "react-router";
import {
  ArrowLeftIcon,
  BrainIcon,
  CalendarIcon,
  RefreshCwIcon,
  SettingsIcon,
  XIcon,
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
import type { GraphKindFilters } from "@/components/memory-graph/layout";
import type { MemoryGraphStats } from "@/services/memory-service";

/** 顶栏筛选开关的文案与色点（与图例/画布配色一一对应） */
const KIND_META = {
  entity: { label: "实体", dotClass: "bg-primary" },
  episode: { label: "事件", dotClass: "bg-indigo-400" },
  statement: { label: "事实", dotClass: "bg-[#c9bfae]" },
} as const;

const KIND_KEYS = Object.keys(KIND_META) as (keyof GraphKindFilters)[];

const KIND_COUNT_FIELD = {
  entity: "entityNodes",
  episode: "episodeNodes",
  statement: "statementEdges",
} as const;

interface GraphHeaderProps {
  /** 当前快照统计（驱动筛选开关上的计数） */
  stats: MemoryGraphStats | null;
  kinds: GraphKindFilters;
  onToggleKind: (kind: keyof GraphKindFilters) => void;
  /** 时点回放日期（null = 当前态） */
  at: string | null;
  onChangeAt: (value: string | null) => void;
  isLoading: boolean;
  onRefresh: () => void;
  /** 打开危险操作区弹窗（当日清除 / 会话遗忘 / 整体重置） */
  onOpenMaintenance: (kind: "day" | "thread" | "reset") => void;
}

/** 记忆图谱页顶栏：返回 / 标题统计 / 类型筛选 / 时点回放 / 记忆管理 / 刷新 */
export const GraphHeader: FC<GraphHeaderProps> = ({
  stats,
  kinds,
  onToggleKind,
  at,
  onChangeAt,
  isLoading,
  onRefresh,
  onOpenMaintenance,
}) => {
  const navigate = useNavigate();

  return (
    <header className="flex flex-wrap items-center gap-3 border-b border-border/60 px-4 py-3 sm:px-6">
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2 w-fit text-muted-foreground"
        onClick={() => void navigate("/admin")}
      >
        <ArrowLeftIcon />
        返回控制台
      </Button>
      <div className="mr-auto min-w-0">
        <h1 className="flex items-center gap-2 font-heading text-lg font-semibold tracking-tight">
          <BrainIcon className="size-5 text-primary" />
          记忆图谱
        </h1>
        <p className="hidden text-xs text-muted-foreground sm:block">
          跨会话长期记忆的可视化：人/物、事件与事实关系
        </p>
      </div>

      {/* 类型筛选开关：色点与图例同源；点击切换该类节点/边是否入图。
          移动端同样可见（原先 hidden md:flex 会把筛选藏起来），只是触达略高 */}
      {stats && (
        <div className="flex flex-wrap items-center gap-1.5">
          {KIND_KEYS.map((kind) => {
            const active = kinds[kind];
            const meta = KIND_META[kind];
            return (
              <Button
                key={kind}
                variant={active ? "secondary" : "outline"}
                size="sm"
                aria-pressed={active}
                aria-label={`${active ? "隐藏" : "显示"}${meta.label}`}
                className={[
                  "h-8 gap-1.5 rounded-full px-3 text-xs font-normal md:h-7 md:px-2.5",
                  active ? "" : "text-muted-foreground",
                ].join(" ")}
                onClick={() => onToggleKind(kind)}
              >
                <span
                  aria-hidden
                  className={[
                    "size-2 rounded-full",
                    meta.dotClass,
                    active ? "" : "opacity-40 grayscale",
                  ].join(" ")}
                />
                {meta.label} {stats[KIND_COUNT_FIELD[kind]]}
              </Button>
            );
          })}
        </div>
      )}

      {/* 时点回放：日期粒度切片（后端按双时间轴取该日仍在效的事实） */}
      <div className="flex items-center gap-1.5">
        <CalendarIcon className="size-4 text-muted-foreground" />
        <Input
          type="date"
          className="h-8 w-40 text-xs"
          value={at ?? ""}
          onChange={(event) => onChangeAt(event.target.value || null)}
          aria-label="时点回放日期"
        />
        {at && (
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={() => onChangeAt(null)}
            aria-label="退出回放，回到当前态"
          >
            <XIcon className="size-3.5" />
          </Button>
        )}
      </div>

      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button variant="outline" size="sm">
              <SettingsIcon />
              记忆管理
            </Button>
          }
        />
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => onOpenMaintenance("day")}>当日清除…</DropdownMenuItem>
          <DropdownMenuItem onClick={() => onOpenMaintenance("thread")}>按会话遗忘…</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="text-destructive data-highlighted:text-destructive"
            onClick={() => onOpenMaintenance("reset")}
          >
            整体重置…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Button variant="outline" size="sm" disabled={isLoading} onClick={onRefresh}>
        <RefreshCwIcon className={isLoading ? "animate-spin" : ""} />
        刷新
      </Button>
    </header>
  );
};
