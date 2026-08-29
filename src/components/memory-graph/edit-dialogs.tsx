import { useEffect, useState, type FC, type FormEvent } from "react";
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
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ENTITY_TYPE_LABELS } from "@/components/memory-graph/layout";
import type {
  EpisodeLinkUpdatePayload,
  EpisodeUpdatePayload,
  EntityUpdatePayload,
  MemoryEntityNode,
  MemoryEpisodeNode,
  MemoryStatementEdge,
  StatementWritePayload,
} from "@/services/memory-service";

/**
 * 记忆编辑弹窗（L1 事实/实体 + L3 事件）：纠正/补充/档案直改/参与改挂。
 *
 * 与 knowledge-form-dialog 同款约定：受控组件、open 时按 initial 重置、
 * 提交经 onSubmit（返回 true 才关闭），不引入表单库。纠正模式只提交
 * 变化了的字段——生效/发生时间仅在用户改动时携带，避免把未修改的日期
 * 重置成当天 00:00 触发无意义的取代链。
 */

const VALID_FROM_HINT = "格式 YYYY-MM-DD；留空保持原生效起点";

/** "e:3" → 3；非法形态返回 null（理论上快照 id 恒为该形态） */
const numRef = (id: string): number | null => {
  const n = Number(id.split(":")[1]);
  return Number.isFinite(n) ? n : null;
};

interface FactEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 纠正模式的目标陈述；null/缺省 = 补充模式（以 anchor 为主体） */
  statement?: MemoryStatementEdge | null;
  /** 补充模式的事实主体锚点 */
  anchorEntity?: MemoryEntityNode | null;
  /** 实体引用下拉的候选（主体自身会被排除） */
  entities: MemoryEntityNode[];
  /** 提交（前端校验通过后）；返回 true 才关闭弹窗 */
  onSubmit: (payload: StatementWritePayload) => Promise<boolean>;
}

export const FactEditDialog: FC<FactEditDialogProps> = ({
  open,
  onOpenChange,
  statement = null,
  anchorEntity = null,
  entities,
  onSubmit,
}) => {
  const isCorrect = statement != null;
  const [predicate, setPredicate] = useState("");
  const [objectMode, setObjectMode] = useState<"entity" | "literal">("entity");
  const [objectEntityId, setObjectEntityId] = useState("");
  const [objectText, setObjectText] = useState("");
  const [summary, setSummary] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // 客体候选：排除主体自身（自指事实无意义）
  const subjectId = statement?.source ?? anchorEntity?.id ?? null;
  const objectCandidates = entities.filter((e) => e.id !== subjectId);
  const entityItems: Record<string, string> = Object.fromEntries(
    objectCandidates.map((e) => [e.id, e.name]),
  );

  useEffect(() => {
    if (!open) return;
    setPredicate(statement?.predicate ?? "");
    const fromEntity = statement?.target != null;
    setObjectMode(statement ? (fromEntity ? "entity" : "literal") : "entity");
    setObjectEntityId(statement?.target ?? "");
    setObjectText(statement?.objectText ?? "");
    setSummary("");
    setValidFrom(statement?.validFrom?.slice(0, 10) ?? "");
    setNote("");
  }, [open, statement]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmedPredicate = predicate.trim();
    if (!trimmedPredicate) return;
    if (objectMode === "entity" && !objectEntityId) return;
    if (objectMode === "literal" && !objectText.trim()) return;

    const payload: StatementWritePayload = { predicate: trimmedPredicate };
    if (objectMode === "entity") {
      const ref = numRef(objectEntityId);
      if (ref == null) return;
      payload.objectEntityId = ref;
    } else {
      payload.objectText = objectText.trim();
    }
    if (summary.trim()) payload.summary = summary.trim();
    if (note.trim()) payload.note = note.trim();
    // 生效起点只在改动时携带（留空/未改 = 沿用原值，见组件头注释）
    const initialValidFrom = statement?.validFrom?.slice(0, 10) ?? "";
    if (validFrom && validFrom !== initialValidFrom) payload.validFrom = validFrom;

    setSubmitting(true);
    try {
      const ok = await onSubmit(payload);
      if (ok) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{isCorrect ? "纠正事实" : "补充事实"}</DialogTitle>
          <DialogDescription>
            {isCorrect
              ? `旧值将保留为「已取代」历史，可在时点回放中追溯。主体：${
                  entities.find((e) => e.id === subjectId)?.name ?? statement?.source ?? ""
                }`
              : `以「${anchorEntity?.name ?? ""}」为主体补充一条长期记忆（人工维护，抽取不会自动改写）。`}
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <Label htmlFor="fact-predicate">谓词</Label>
            <Input
              id="fact-predicate"
              value={predicate}
              maxLength={50}
              placeholder="例如：居住地 / 认识 / 就职于"
              onChange={(e) => setPredicate(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label>客体</Label>
            <div className="flex items-center gap-1.5">
              {(["entity", "literal"] as const).map((mode) => (
                <Button
                  key={mode}
                  type="button"
                  variant={objectMode === mode ? "secondary" : "outline"}
                  size="xs"
                  onClick={() => setObjectMode(mode)}
                >
                  {mode === "entity" ? "实体引用" : "字面量"}
                </Button>
              ))}
            </div>
            {objectMode === "entity" ? (
              <Select
                items={entityItems}
                value={objectEntityId || null}
                onValueChange={(value) => setObjectEntityId(value ?? "")}
              >
                <SelectTrigger className="w-full" aria-label="客体实体">
                  <SelectValue placeholder="选择实体" />
                </SelectTrigger>
                <SelectContent>
                  {objectCandidates.map((e) => (
                    <SelectItem key={e.id} value={e.id}>
                      {e.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                value={objectText}
                maxLength={200}
                placeholder="例如：Python / 后端开发"
                onChange={(e) => setObjectText(e.target.value)}
              />
            )}
          </div>

          <div className="grid gap-2">
            <Label htmlFor="fact-summary">整句表述（可选）</Label>
            <Input
              id="fact-summary"
              value={summary}
              maxLength={300}
              placeholder="留空则按新值自动组装"
              onChange={(e) => setSummary(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="fact-valid-from">生效起点（可选）</Label>
            <Input
              id="fact-valid-from"
              type="date"
              value={validFrom}
              aria-label="生效起点"
              onChange={(e) => setValidFrom(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">{VALID_FROM_HINT}</p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="fact-note">备注（可选）</Label>
            <Input
              id="fact-note"
              value={note}
              maxLength={300}
              placeholder="纠正原因等，落为证据留档"
              onChange={(e) => setNote(e.target.value)}
            />
          </div>

          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
            <Button type="submit" disabled={submitting}>
              {submitting && <Loader2Icon className="animate-spin" />}
              {isCorrect ? "提交纠正" : "补充"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

interface EntityEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  entity: MemoryEntityNode;
  /** 提交（前端校验通过后）；返回 true 才关闭弹窗 */
  onSubmit: (payload: EntityUpdatePayload) => Promise<boolean>;
}

export const EntityEditDialog: FC<EntityEditDialogProps> = ({
  open,
  onOpenChange,
  entity,
  onSubmit,
}) => {
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState("");
  const [entityType, setEntityType] = useState("OTHER");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(entity.name);
    setAliases(entity.aliases.join("、"));
    setEntityType(
      entity.entityType in ENTITY_TYPE_LABELS ? entity.entityType : "OTHER",
    );
  }, [open, entity]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) return;
    setSubmitting(true);
    try {
      const ok = await onSubmit({
        name: trimmedName,
        aliases: aliases
          .split(/[,，、]/)
          .map((a) => a.trim())
          .filter(Boolean),
        entityType,
      });
      if (ok) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑实体</DialogTitle>
          <DialogDescription>
            名称与别名是实体消歧的依据：改名撞上其他实体会被拒绝（3005）。
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <Label htmlFor="entity-name">名称</Label>
            <Input
              id="entity-name"
              value={name}
              maxLength={100}
              onChange={(e) => setName(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="entity-aliases">别名</Label>
            <Input
              id="entity-aliases"
              value={aliases}
              maxLength={500}
              placeholder="多个别名用顿号/逗号分隔；全量替换"
              onChange={(e) => setAliases(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label>类型</Label>
            <Select
              items={ENTITY_TYPE_LABELS}
              value={entityType}
              onValueChange={(value) => setEntityType(value ?? "OTHER")}
            >
              <SelectTrigger className="w-full" aria-label="实体类型">
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

          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
            <Button type="submit" disabled={submitting}>
              {submitting && <Loader2Icon className="animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

// ---------------- 事件档案直改（L3）----------------

interface EpisodeEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  episode: MemoryEpisodeNode;
  /** 提交（前端校验通过后）；返回 true 才关闭弹窗 */
  onSubmit: (payload: EpisodeUpdatePayload) => Promise<boolean>;
}

export const EpisodeEditDialog: FC<EpisodeEditDialogProps> = ({
  open,
  onOpenChange,
  episode,
  onSubmit,
}) => {
  const [summary, setSummary] = useState("");
  const [scene, setScene] = useState("");
  const [occurredAt, setOccurredAt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const initialOccurredAt = episode.occurredAt?.slice(0, 10) ?? "";

  useEffect(() => {
    if (!open) return;
    setSummary(episode.summary);
    setScene(episode.scene ?? "");
    setOccurredAt(episode.occurredAt?.slice(0, 10) ?? "");
  }, [open, episode]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const trimmed = summary.trim();
    if (!trimmed) return;
    const payload: EpisodeUpdatePayload = { summary: trimmed, scene: scene.trim() };
    if (occurredAt && occurredAt !== initialOccurredAt) payload.occurredAt = occurredAt;
    setSubmitting(true);
    try {
      const ok = await onSubmit(payload);
      if (ok) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑事件</DialogTitle>
          <DialogDescription>
            摘要是事件召回的检索源，修改后检索索引同步更新；删除事件请在档案页操作（不可恢复）。
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <Label htmlFor="episode-summary">事件梗概</Label>
            <Textarea
              id="episode-summary"
              value={summary}
              rows={3}
              maxLength={500}
              onChange={(e) => setSummary(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">{summary.length}/500</p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="episode-scene">场景（可选）</Label>
            <Input
              id="episode-scene"
              value={scene}
              maxLength={50}
              placeholder="例如：商业谈判；清空即移除场景标签"
              onChange={(e) => setScene(e.target.value)}
            />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="episode-occurred">发生时间</Label>
            <Input
              id="episode-occurred"
              type="date"
              value={occurredAt}
              onChange={(e) => setOccurredAt(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">改动后才会提交，留空保持原值。</p>
          </div>

          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
            <Button type="submit" disabled={submitting}>
              {submitting && <Loader2Icon className="animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};

// ---------------- 参与改挂（L3）----------------

export interface EpisodeLinkTarget {
  linkId: string;
  entityId: string;
  entityName: string;
  role: string | null;
}

interface EpisodeLinkEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  link: EpisodeLinkTarget;
  entities: MemoryEntityNode[];
  /** 提交（前端校验通过后）；返回 true 才关闭弹窗 */
  onSubmit: (payload: EpisodeLinkUpdatePayload) => Promise<boolean>;
}

export const EpisodeLinkEditDialog: FC<EpisodeLinkEditDialogProps> = ({
  open,
  onOpenChange,
  link,
  entities,
  onSubmit,
}) => {
  const [entityRef, setEntityRef] = useState(link.entityId);
  const [role, setRole] = useState(link.role ?? "");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setEntityRef(link.entityId);
    setRole(link.role ?? "");
  }, [open, link]);

  const candidates = entities.filter((e) => e.id !== link.entityId);
  const items: Record<string, string> = Object.fromEntries(
    candidates.map((e) => [e.id, e.name]),
  );

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const payload: EpisodeLinkUpdatePayload = { role: role.trim() };
    if (entityRef !== link.entityId) {
      const ref = numRef(entityRef);
      if (ref == null) return;
      payload.entityId = ref;
    }
    setSubmitting(true);
    try {
      const ok = await onSubmit(payload);
      if (ok) onOpenChange(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑事件参与</DialogTitle>
          <DialogDescription>
            参与角色当前挂在「{link.entityName}」上；换实体即改挂，角色清空即移除标签。
          </DialogDescription>
        </DialogHeader>

        <form className="grid gap-4" onSubmit={(e) => void handleSubmit(e)}>
          <div className="grid gap-2">
            <Label>参与实体</Label>
            <Select
              items={{ ...items, [link.entityId]: link.entityName }}
              value={entityRef}
              onValueChange={(value) => setEntityRef(value ?? link.entityId)}
            >
              <SelectTrigger className="w-full" aria-label="参与实体">
                <SelectValue />
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

          <div className="grid gap-2">
            <Label htmlFor="link-role">角色</Label>
            <Input
              id="link-role"
              value={role}
              maxLength={50}
              placeholder="例如：参与者 / 涉及物；清空即移除角色标签"
              onChange={(e) => setRole(e.target.value)}
            />
          </div>

          <DialogFooter>
            <DialogClose render={<Button variant="outline" />}>取消</DialogClose>
            <Button type="submit" disabled={submitting}>
              {submitting && <Loader2Icon className="animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
};
