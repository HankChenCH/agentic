import { useEffect, useMemo, useState, type FC, type FormEvent } from "react";
import { Loader2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ENTITY_TYPE_LABELS } from "@/components/memory-graph/layout";
import type { EntityRelation, LiteralFact } from "@/components/memory-graph/layout";
import type {
  EntityMergeResult,
  EntitySplitResult,
  MemoryEntityNode,
} from "@/services/memory-service";

/**
 * 实体身份纠错弹窗（L2）：错分离合并 + 错合并拆分。
 *
 * 语义与 server/docs/memory-edit-plan.md §4 对齐：
 * - 合并是全量迁移（含历史版本），预览计数先行、原实体删除；
 * - 拆分按选择迁移（事实/事件参与/别名三类勾选），双方互写拆分禁令，
 *   全部迁走时原实体将因拆空自动删除（弹窗内预告）。
 */

/** 实体档案里用于身份操作的关联内容切片（页面从图模型索引组装） */
export interface EntityIdentityContext {
  entity: MemoryEntityNode;
  /** 字面量事实（statementId 可迁移） */
  facts: LiteralFact[];
  /** 双向关系行（edgeId 可迁移） */
  relations: EntityRelation[];
  /** 事件参与（linkId 可迁移） */
  participations: { linkId: string; label: string; role: string | null }[];
}

const useResetOnOpen = (open: boolean, reset: () => void) => {
  useEffect(() => {
    if (open) reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
};

// ---------------- 合并（错分离 → 归一）----------------

interface MergeEntityDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  context: EntityIdentityContext;
  /** 目标候选（当前快照全部实体，弹窗内排除自身） */
  entities: MemoryEntityNode[];
  onSubmit: (targetRef: string) => Promise<EntityMergeResult | null>;
}

export const MergeEntityDialog: FC<MergeEntityDialogProps> = ({
  open,
  onOpenChange,
  context,
  entities,
  onSubmit,
}) => {
  const { entity } = context;
  const [targetRef, setTargetRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  useResetOnOpen(open, () => setTargetRef(""));

  const candidates = entities.filter((e) => e.id !== entity.id);
  const items: Record<string, string> = Object.fromEntries(
    candidates.map((e) => [e.id, e.name]),
  );
  const target = candidates.find((e) => e.id === targetRef);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!targetRef) return;
    setSubmitting(true);
    try {
      const result = await onSubmit(targetRef);
      if (result !== null) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>合并到其他实体</DialogTitle>
          <DialogDescription>
            「{entity.name}」的全部内容（含历史版本）将并入目标实体，原实体删除。
            适用于同一对象被错误地建成了两个实体。
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <Label>存留方实体</Label>
            <Select
              items={items}
              value={targetRef || null}
              onValueChange={(value) => setTargetRef(value ?? "")}
            >
              <SelectTrigger className="w-full" aria-label="存留方实体">
                <SelectValue placeholder="选择要并入的实体" />
              </SelectTrigger>
              <SelectContent>
                {candidates.map((e) => (
                  <SelectItem key={e.id} value={e.id}>
                    {e.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {target && (
            <div className="rounded-lg border border-border/60 bg-muted/50 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
              将把「{entity.name}」的名字与别名、
              {context.facts.length + context.relations.length} 条现存事实（含历史）、
              {context.participations.length} 个事件参与并入「{target.name}」，
              原实体删除。
            </div>
          )}

          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
            <Button type="submit" disabled={!targetRef || submitting}>
              {submitting && <Loader2Icon className="animate-spin" />}
              合并
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

// ---------------- 拆分（错合并 → 分离）----------------

interface SplitEntityDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  context: EntityIdentityContext;
  onSubmit: (payload: {
    name: string;
    entityType: string;
    aliases: string[];
    statementIds: string[];
    episodeLinkIds: string[];
  }) => Promise<EntitySplitResult | null>;
}

interface CheckRow {
  id: string;
  label: string;
  superseded?: boolean;
}

export const SplitEntityDialog: FC<SplitEntityDialogProps> = ({
  open,
  onOpenChange,
  context,
  onSubmit,
}) => {
  const { entity, facts, relations, participations } = context;
  const [name, setName] = useState("");
  const [entityType, setEntityType] = useState("OTHER");
  const [pickedStatements, setPickedStatements] = useState<Set<string>>(new Set());
  const [pickedLinks, setPickedLinks] = useState<Set<string>>(new Set());
  const [pickedAliases, setPickedAliases] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);

  useResetOnOpen(open, () => {
    setName("");
    setEntityType("OTHER");
    setPickedStatements(new Set());
    setPickedLinks(new Set());
    setPickedAliases(new Set());
  });

  const statementRows: CheckRow[] = useMemo(
    () => [
      ...facts.map((f) => ({
        id: f.statementId,
        label: `${f.predicate} → ${f.objectText}`,
        superseded: f.state !== "ACTIVE",
      })),
      ...relations.map((r) => ({ id: r.edgeId, label: `${r.predicate} → ${r.targetName}` })),
    ],
    [facts, relations],
  );

  const toggle = (
    set: Set<string>,
    apply: (next: Set<string>) => void,
    id: string,
  ) => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    apply(next);
  };

  const pickedCount =
    pickedStatements.size + pickedLinks.size + pickedAliases.size;
  const totalCount = statementRows.length + participations.length + entity.aliases.length;
  const allPicked = totalCount > 0 && pickedCount === totalCount;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName || pickedCount === 0) return;
    setSubmitting(true);
    try {
      const result = await onSubmit({
        name: trimmedName,
        entityType,
        aliases: entity.aliases.filter((a) => pickedAliases.has(a)),
        statementIds: [...pickedStatements],
        episodeLinkIds: [...pickedLinks],
      });
      if (result !== null) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  const CheckList: FC<{ rows: CheckRow[]; picked: Set<string>; onToggle: (id: string) => void }> =
    ({ rows, picked, onToggle }) => (
      <div className="flex max-h-32 flex-col gap-1 overflow-auto rounded-lg border border-border/60 p-2">
        {rows.map((row) => (
          <label
            key={row.id}
            className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-xs hover:bg-muted/60"
          >
            <input
              type="checkbox"
              checked={picked.has(row.id)}
              onChange={() => onToggle(row.id)}
              className="accent-[var(--primary)]"
            />
            <span className={row.superseded ? "text-muted-foreground/60 line-through" : ""}>
              {row.label}
            </span>
          </label>
        ))}
      </div>
    );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>拆分实体</DialogTitle>
          <DialogDescription>
            勾选属于另一个对象的内容，迁移到新实体——适用于多个对象被错误
            合并进「{entity.name}」的情况。拆分后双方互写禁令，消歧不会再
            把它们合回去。
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid grid-cols-[1fr_130px] gap-3">
            <div className="grid gap-2">
              <Label htmlFor="split-name">新实体名称</Label>
              <Input
                id="split-name"
                value={name}
                maxLength={100}
                placeholder="例如：广州市卫生职业技术学院"
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="grid gap-2">
              <Label>类型</Label>
              <Select
                items={ENTITY_TYPE_LABELS}
                value={entityType}
                onValueChange={(value) => setEntityType(value ?? "OTHER")}
              >
                <SelectTrigger className="w-full" aria-label="新实体类型">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(ENTITY_TYPE_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {statementRows.length > 0 && (
            <div className="grid gap-2">
              <Label>事实（{pickedStatements.size}/{statementRows.length}）</Label>
              <CheckList
                rows={statementRows}
                picked={pickedStatements}
                onToggle={(id) => toggle(pickedStatements, setPickedStatements, id)}
              />
            </div>
          )}

          {participations.length > 0 && (
            <div className="grid gap-2">
              <Label>事件参与（{pickedLinks.size}/{participations.length}）</Label>
              <CheckList
                rows={participations.map((p) => ({ id: p.linkId, label: p.label }))}
                picked={pickedLinks}
                onToggle={(id) => toggle(pickedLinks, setPickedLinks, id)}
              />
            </div>
          )}

          {entity.aliases.length > 0 && (
            <div className="grid gap-2">
              <Label>别名（{pickedAliases.size}/{entity.aliases.length}）</Label>
              <CheckList
                rows={entity.aliases.map((a) => ({ id: a, label: a }))}
                picked={pickedAliases}
                onToggle={(id) => toggle(pickedAliases, setPickedAliases, id)}
              />
            </div>
          )}

          {pickedCount > 0 && (
            <div className="rounded-lg border border-border/60 bg-muted/50 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
              将把 {pickedCount} 项内容迁往新实体「{name.trim() || "…"}」
              {allPicked ? "；原实体将因拆空自动删除" : "，其余内容保留在原实体"}。
            </div>
          )}

          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
            <Button type="submit" disabled={!name.trim() || pickedCount === 0 || submitting}>
              {submitting && <Loader2Icon className="animate-spin" />}
              执行拆分
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};
