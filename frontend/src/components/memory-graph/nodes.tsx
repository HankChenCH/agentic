import type { FC } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { CalendarIcon, UserIcon } from "lucide-react";

import {
  ENTITY_TYPE_COLOR_FALLBACK,
  ENTITY_TYPE_COLORS,
  ENTITY_TYPE_LABELS,
  type EntityFlowNode,
  type EpisodeFlowNode,
} from "@/components/memory-graph/layout";

/**
 * 记忆图谱自定义节点：实体卡（人/物）与情节卡（事件）。
 *
 * 只负责渲染——选中态由 React Flow 注入的 selected prop 驱动；点击行为
 * 统一在页面的 onNodeClick 里处理。字面量事实（无实体客体的谓词）内嵌
 * 在实体卡下沿，最多 3 行。
 */

const typeColor = (entityType: string) =>
  ENTITY_TYPE_COLORS[entityType] ?? ENTITY_TYPE_COLOR_FALLBACK;

const typeLabel = (entityType: string) =>
  ENTITY_TYPE_LABELS[entityType] ?? entityType;

export const EntityNodeView: FC<NodeProps<EntityFlowNode>> = ({
  data,
  selected,
}) => {
  const { memory, facts } = data;
  return (
    <div
      className={[
        "flex h-full flex-col justify-center gap-1 rounded-xl border bg-card px-3 py-2 shadow-card transition-shadow",
        memory.isUser ? "border-primary/50" : "border-border/80",
        selected ? "ring-2 ring-primary/70" : "",
      ].join(" ")}
    >
      <div className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="size-2 shrink-0 rounded-full"
          style={{ backgroundColor: typeColor(memory.entityType) }}
        />
        <span className="truncate text-sm font-medium" title={memory.name}>
          {memory.name}
        </span>
        {memory.isUser ? (
          <span className="ml-auto inline-flex shrink-0 items-center gap-0.5 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
            <UserIcon className="size-2.5" />
            用户
          </span>
        ) : (
          <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">
            {typeLabel(memory.entityType)}
          </span>
        )}
      </div>
      {memory.aliases.length > 0 && (
        <div className="truncate text-[10px] text-muted-foreground">
          又名 {memory.aliases.join(" · ")}
        </div>
      )}
      {facts.slice(0, 3).map((fact) => (
        <div
          key={fact.statementId}
          className={[
            "truncate border-l-2 pl-1.5 text-[10px]",
            fact.state === "ACTIVE"
              ? "border-border text-muted-foreground"
              : "border-dashed border-muted-foreground/40 text-muted-foreground/60 line-through",
          ].join(" ")}
          title={`${fact.predicate} → ${fact.objectText}`}
        >
          {fact.predicate} · {fact.objectText}
        </div>
      ))}
      <Handle type="target" position={Position.Left} className="!size-1.5" />
      <Handle type="source" position={Position.Right} className="!size-1.5" />
    </div>
  );
};

export const EpisodeNodeView: FC<NodeProps<EpisodeFlowNode>> = ({
  data,
  selected,
}) => {
  const { memory } = data;
  return (
    <div
      className={[
        "flex h-full flex-col gap-1 rounded-xl border border-indigo-200 bg-indigo-50/70 px-3 py-2 shadow-card",
        selected ? "ring-2 ring-indigo-400/80" : "",
      ].join(" ")}
    >
      <div className="flex items-center gap-1.5 text-indigo-500">
        <CalendarIcon className="size-3 shrink-0" />
        <span className="truncate text-[10px] font-medium">
          {memory.occurredAt ? memory.occurredAt.slice(0, 10) : "时间未知"}
          {memory.scene ? ` · ${memory.scene}` : ""}
        </span>
      </div>
      <div
        className="line-clamp-2 text-xs leading-snug text-indigo-950"
        title={memory.summary}
      >
        {memory.summary}
      </div>
      <Handle type="source" position={Position.Right} className="!size-1.5" />
      <Handle type="target" position={Position.Left} className="!size-1.5" />
    </div>
  );
};
