import type { FC } from "react";
import { XIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ENTITY_TYPE_COLOR_FALLBACK,
  ENTITY_TYPE_COLORS,
  ENTITY_TYPE_LABELS,
  type EntityRelation,
  type LiteralFact,
} from "@/components/memory-graph/layout";
import type {
  MemoryEntityNode,
  MemoryEpisodeNode,
  MemoryGraphEdge,
} from "@/services/memory-service";

/**
 * 记忆图谱详情面板：展示选中节点/边（陈述）的完整档案。
 *
 * 纯展示组件——选中对象与索引数据由页面组装后传入；`onClose` 清空选中。
 */

const fmtDate = (iso: string | null): string =>
  iso ? iso.slice(0, 10) : "—";

const TraceRow: FC<{ label: string; value: string | null }> = ({
  label,
  value,
}) => (
  <div className="flex items-baseline justify-between gap-3 text-xs">
    <span className="shrink-0 text-muted-foreground">{label}</span>
    <span className="truncate font-mono text-[11px]" title={value ?? undefined}>
      {value ?? "—"}
    </span>
  </div>
);

const SectionTitle: FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="pt-1 text-[11px] font-medium tracking-wide text-muted-foreground">
    {children}
  </div>
);

export interface EntitySelection {
  kind: "entity";
  node: MemoryEntityNode;
  facts: LiteralFact[];
  relations: EntityRelation[];
}

export interface EpisodeSelection {
  kind: "episode";
  node: MemoryEpisodeNode;
  roles: { entityName: string; role: string | null }[];
}

export interface StatementSelection {
  kind: "statement";
  edge: Extract<MemoryGraphEdge, { kind: "statement" }>;
  sourceName: string;
  targetName: string | null;
}

export type MemorySelection =
  | EntitySelection
  | EpisodeSelection
  | StatementSelection;

export const MemoryDetailPanel: FC<{
  selection: MemorySelection;
  onClose: () => void;
}> = ({ selection, onClose }) => {
  return (
    <aside className="absolute right-4 top-4 z-10 flex max-h-[calc(100%-2rem)] w-80 flex-col overflow-hidden rounded-xl border border-border/80 bg-card shadow-card">
      <div className="flex items-center justify-between border-b border-border/60 px-4 py-2.5">
        <span className="text-xs font-medium tracking-wide text-muted-foreground">
          {selection.kind === "statement" ? "事实档案" : "节点档案"}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="size-6"
          onClick={onClose}
          aria-label="关闭详情"
        >
          <XIcon className="size-3.5" />
        </Button>
      </div>

      <div className="flex flex-col gap-2.5 overflow-auto px-4 py-3">
        {selection.kind === "entity" && (
          <EntityDetail selection={selection} />
        )}
        {selection.kind === "episode" && <EpisodeDetail selection={selection} />}
        {selection.kind === "statement" && (
          <StatementDetail selection={selection} />
        )}
      </div>
    </aside>
  );
};

const EntityDetail: FC<{ selection: EntitySelection }> = ({ selection }) => {
  const { node, facts, relations } = selection;
  const color = ENTITY_TYPE_COLORS[node.entityType] ?? ENTITY_TYPE_COLOR_FALLBACK;
  return (
    <>
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className="size-2.5 rounded-full"
          style={{ backgroundColor: color }}
        />
        <span className="text-base font-semibold">{node.name}</span>
        {node.isUser && <Badge className="rounded-full">用户本人</Badge>}
      </div>
      <div className="text-xs text-muted-foreground">
        类型：{ENTITY_TYPE_LABELS[node.entityType] ?? node.entityType}
      </div>
      {node.aliases.length > 0 && (
        <>
          <SectionTitle>别名</SectionTitle>
          <div className="flex flex-wrap gap-1">
            {node.aliases.map((alias) => (
              <Badge key={alias} variant="outline" className="rounded-full font-normal">
                {alias}
              </Badge>
            ))}
          </div>
        </>
      )}
      <SectionTitle>记忆统计</SectionTitle>
      <div className="flex items-center gap-2 text-xs">
        <span>重要度</span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary/70"
            style={{ width: `${Math.round(node.importance * 100)}%` }}
          />
        </div>
        <span className="tabular-nums text-muted-foreground">
          {node.importance.toFixed(2)}
        </span>
      </div>
      <TraceRow label="被召回次数" value={String(node.accessCount)} />
      <TraceRow label="最近召回" value={fmtDate(node.lastAccessedAt)} />
      {facts.length > 0 && (
        <>
          <SectionTitle>属性事实（字面量）</SectionTitle>
          <div className="flex flex-col gap-1">
            {facts.map((fact) => (
              <div
                key={fact.statementId}
                className={[
                  "rounded-md border border-border/60 bg-background/60 px-2 py-1 text-xs",
                  fact.state !== "ACTIVE" && "text-muted-foreground/70",
                ].join(" ")}
              >
                <span className="font-medium">{fact.predicate}</span>
                <span className="mx-1 text-muted-foreground">→</span>
                {fact.objectText}
                {fact.state !== "ACTIVE" && (
                  <span className="ml-1 text-[10px]">（已被取代）</span>
                )}
              </div>
            ))}
          </div>
        </>
      )}
      {relations.length > 0 && (
        <>
          <SectionTitle>关系</SectionTitle>
          <div className="flex flex-col gap-1">
            {relations.map((rel) => (
              <div key={rel.edgeId} className="text-xs">
                <span className="font-medium">{rel.predicate}</span>
                <span className="mx-1 text-muted-foreground">→</span>
                {rel.targetName}
                {rel.origin === "MANUAL" && (
                  <Badge variant="outline" className="ml-1 rounded-full px-1 text-[10px]">
                    人工
                  </Badge>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
};

const EpisodeDetail: FC<{ selection: EpisodeSelection }> = ({ selection }) => {
  const { node, roles } = selection;
  return (
    <>
      <div className="text-sm font-semibold leading-snug">{node.summary}</div>
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        发生于 {fmtDate(node.occurredAt)}
        {node.scene && (
          <Badge variant="outline" className="rounded-full font-normal">
            {node.scene}
          </Badge>
        )}
      </div>
      <TraceRow label="被召回次数" value={String(node.accessCount)} />
      {roles.length > 0 && (
        <>
          <SectionTitle>参与角色</SectionTitle>
          <div className="flex flex-col gap-1 text-xs">
            {roles.map((role, index) => (
              <div key={`${role.entityName}-${index}`}>
                <Badge variant="outline" className="mr-1.5 rounded-full px-1.5 font-normal">
                  {role.role ?? "涉及"}
                </Badge>
                {role.entityName}
              </div>
            ))}
          </div>
        </>
      )}
    </>
  );
};

const StatementDetail: FC<{ selection: StatementSelection }> = ({
  selection,
}) => {
  const { edge, sourceName, targetName } = selection;
  return (
    <>
      <div className="text-sm font-semibold">
        {sourceName} -[{edge.predicate}]-&gt; {targetName ?? "（字面量）"}
      </div>
      <div className="text-xs leading-relaxed text-muted-foreground">
        {edge.summary}
      </div>
      <div className="flex items-center gap-1.5">
        <Badge
          variant="outline"
          className={[
            "rounded-full font-normal",
            edge.state !== "ACTIVE" && "text-muted-foreground/70",
          ].join(" ")}
        >
          {edge.state === "ACTIVE"
            ? "在效"
            : edge.state === "SUPERSEDED"
              ? "已被取代"
              : "已归档"}
        </Badge>
        {edge.origin === "MANUAL" && (
          <Badge variant="outline" className="rounded-full font-normal">
            人工维护
          </Badge>
        )}
        <span className="text-xs text-muted-foreground">
          置信度 {edge.confidence.toFixed(2)}
        </span>
      </div>
      <SectionTitle>时效（valid time）</SectionTitle>
      <TraceRow label="自" value={fmtDate(edge.validFrom)} />
      <TraceRow label="至" value={fmtDate(edge.validTo)} />
      <SectionTitle>溯源</SectionTitle>
      <TraceRow label="会话" value={edge.sourceThreadId} />
      <TraceRow label="轮次" value={edge.sourceTurnId} />
    </>
  );
};
