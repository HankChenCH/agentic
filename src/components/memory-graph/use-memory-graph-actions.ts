import { useCallback, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { toast } from "sonner";

import type { EpisodeLinkTarget } from "@/components/memory-graph/edit-dialogs";
import type { GraphModel } from "@/components/memory-graph/layout";
import type { MemorySelection } from "@/components/memory-graph/detail-panel";
import { BizError } from "@/lib/http";
import { memoryService } from "@/services/memory-service";
import type {
  PurgeRequest,
  PurgePreview,
  PurgeResult,
  EntityMergeResult,
  EntitySplitPayload,
  EntitySplitResult,
  EntityUpdatePayload,
  EpisodeLinkUpdatePayload,
  EpisodeUpdatePayload,
  MemoryEntityNode,
  MemoryEpisodeNode,
  MemoryGraphNode,
  MemoryStatementEdge,
  StatementWritePayload,
} from "@/services/memory-service";

/** "e:3" → 3（快照 id → 数据库主键载荷） */
const numRef = (id: string): number | null => {
  const n = Number(id.split(":")[1]);
  return Number.isFinite(n) ? n : null;
};

const bizMessage = (err: unknown): string =>
  err instanceof BizError ? err.message : "操作失败，请稍后重试";

/** 详情面板触发的编辑弹窗（L1 事实纠错 + L2 身份纠错 + L3 事件编辑） */
export type EditDialogState =
  | { kind: "correct"; statement: MemoryStatementEdge }
  | { kind: "add-fact"; entity: MemoryEntityNode }
  | { kind: "edit-entity"; entity: MemoryEntityNode }
  | { kind: "merge"; entity: MemoryEntityNode }
  | { kind: "split"; entity: MemoryEntityNode }
  | { kind: "edit-episode"; node: MemoryEpisodeNode }
  | { kind: "edit-link"; link: EpisodeLinkTarget }
  | null;

export type MaintenanceKind = "day" | "thread" | "reset" | null;

interface MemoryGraphActionsOptions {
  /** 当前图模型（供身份纠错切片与标签解析） */
  model: GraphModel | null;
  /** 快照实体清单（编辑弹窗的实体引用候选） */
  snapshotNodes: MemoryGraphNode[];
  /** 静默刷新快照（变更成功后调用） */
  refresh: (silent?: boolean) => Promise<unknown>;
  /** 面板选中态（变更成功后按需清空/同步替换） */
  selection: MemorySelection | null;
  setSelection: Dispatch<SetStateAction<MemorySelection | null>>;
}

/**
 * 记忆图谱页的编辑/维护动作集（L1–L4）：弹窗托管、各变更提交处理与
 * 确认目标状态。统一约定：提交成功 → toast + 同步面板选中 + 静默刷新
 * 快照；失败 → error toast 且弹窗保持打开。
 */
export const useMemoryGraphActions = ({
  model,
  snapshotNodes,
  refresh,
  setSelection,
}: MemoryGraphActionsOptions) => {
  // ---------------- 编辑（L1）：弹窗托管 + 变更后静默刷新 ----------------

  const [dialog, setDialog] = useState<EditDialogState>(null);
  const [archiveTarget, setArchiveTarget] = useState<MemoryStatementEdge | null>(null);

  const entities = useMemo(
    () => snapshotNodes.filter((n): n is MemoryEntityNode => n.kind === "entity"),
    [snapshotNodes],
  );

  const statementLabel = useCallback(
    (edge: MemoryStatementEdge): string => {
      if (!edge.target) return edge.objectText ?? "（字面量）";
      const node = model?.nodeById.get(edge.target);
      return node?.kind === "entity" ? node.name : edge.target;
    },
    [model],
  );

  const episodeLabelOf = useCallback(
    (episodeId: string, role: string | null): string => {
      const ep = model?.nodeById.get(episodeId);
      if (ep?.kind !== "episode") return role ?? "参与";
      const date = (ep.occurredAt ?? "").slice(0, 10);
      return `${date ? `${date} · ` : ""}${ep.summary}${role ? `（${role}）` : ""}`;
    },
    [model],
  );

  const handleCorrectSubmit = async (
    statement: MemoryStatementEdge,
    payload: StatementWritePayload,
  ): Promise<boolean> => {
    try {
      const { old, new: next } = await memoryService.correctStatement(statement.id, payload);
      toast.success(
        `已纠正：${old.predicate} ${statementLabel(old)} → ${statementLabel(next)}，历史可在时点回放查看`,
      );
      setSelection(null); // 旧陈述已 SUPERSEDED 退出当前图，面板随之关闭
      await refresh(true);
      return true;
    } catch (err) {
      toast.error(bizMessage(err));
      return false;
    }
  };

  const handleAddFactSubmit = async (
    entity: MemoryEntityNode,
    payload: StatementWritePayload,
  ): Promise<boolean> => {
    const subjectEntityId = numRef(entity.id);
    if (subjectEntityId == null) return false;
    try {
      const row = await memoryService.addStatement({ subjectEntityId, ...payload });
      toast.success(`已补充事实：${row.summary}`);
      await refresh(true);
      return true;
    } catch (err) {
      toast.error(bizMessage(err));
      return false;
    }
  };

  const handleEntitySubmit = async (
    entity: MemoryEntityNode,
    payload: EntityUpdatePayload,
  ): Promise<boolean> => {
    try {
      const updated = await memoryService.updateEntity(entity.id, payload);
      toast.success("实体档案已更新");
      // 面板选中数据同步替换，名称/别名/类型即时生效（免二次点击）
      setSelection((prev) =>
        prev?.kind === "entity" && prev.node.id === updated.id
          ? { ...prev, node: updated }
          : prev,
      );
      await refresh(true);
      return true;
    } catch (err) {
      toast.error(bizMessage(err));
      return false;
    }
  };

  const handleArchiveConfirm = async (): Promise<boolean> => {
    if (!archiveTarget) return false;
    try {
      await memoryService.archiveStatement(archiveTarget.id);
      toast.success("已归档：不再出现在当前图谱（时点回放仍可追溯）");
      setSelection(null); // 被归档陈述退出当前图，面板随之关闭
      await refresh(true);
      return true;
    } catch (err) {
      toast.error(bizMessage(err));
      return false;
    }
  };

  // ---------------- 身份纠错（L2）：合并 / 拆分 / 孤立清理 ----------------

  const [deleteTarget, setDeleteTarget] = useState<MemoryEntityNode | null>(null);

  /** 弹窗打开期间的实时关联切片（取自当前图模型索引，勾选/计数据此渲染） */
  const identityContext = useMemo(() => {
    if (dialog?.kind !== "merge" && dialog?.kind !== "split") return null;
    const node = dialog.entity;
    return {
      entity: node,
      facts: model?.factsByEntity.get(node.id) ?? [],
      relations: model?.relationsByEntity.get(node.id) ?? [],
      participations: (model?.linksByEntity.get(node.id) ?? []).map((l) => ({
        linkId: l.linkId,
        role: l.role,
        label: episodeLabelOf(l.episodeId, l.role),
      })),
    };
  }, [dialog, model, episodeLabelOf]);

  const handleMergeSubmit = async (
    entity: MemoryEntityNode,
    targetRef: string,
  ): Promise<EntityMergeResult | null> => {
    try {
      const result = await memoryService.mergeEntity(entity.id, { targetRef });
      toast.success(
        `已合并：「${entity.name}」并入「${result.target.name}」` +
        `（${result.movedStatements} 条事实、${result.movedLinks} 个事件参与），原实体已删除`,
      );
      setSelection(null); // source 已删除，面板随之关闭
      await refresh(true);
      return result;
    } catch (err) {
      toast.error(bizMessage(err));
      return null;
    }
  };

  const handleSplitSubmit = async (
    entity: MemoryEntityNode,
    payload: EntitySplitPayload,
  ): Promise<EntitySplitResult | null> => {
    try {
      const result = await memoryService.splitEntity(entity.id, payload);
      toast.success(
        `已拆分：新实体「${result.entity.name}」` +
        `（${result.movedStatements} 条事实、${result.movedLinks} 个事件参与迁入）` +
        (result.sourceDeleted ? "，原实体已因拆空自动删除" : "，双方已互写合并禁令"),
      );
      setSelection(null); // 原实体内容已变化，重新点选查看
      await refresh(true);
      return result;
    } catch (err) {
      toast.error(bizMessage(err));
      return null;
    }
  };

  const handleDeleteConfirm = async (): Promise<boolean> => {
    if (!deleteTarget) return false;
    try {
      await memoryService.deleteEntity(deleteTarget.id);
      toast.success(`已删除孤立实体「${deleteTarget.name}」`);
      setSelection(null);
      await refresh(true);
      return true;
    } catch (err) {
      toast.error(bizMessage(err));
      return false;
    }
  };

  // ---------------- 事件编辑（L3）：直改 / 删除 / 参与改挂 ----------------

  const [deleteEpisodeTarget, setDeleteEpisodeTarget] = useState<MemoryEpisodeNode | null>(null);

  const handleEpisodeSubmit = async (
    node: MemoryEpisodeNode,
    payload: EpisodeUpdatePayload,
  ): Promise<boolean> => {
    try {
      const updated = await memoryService.updateEpisode(node.id, payload);
      toast.success("事件档案已更新");
      setSelection((prev) =>
        prev?.kind === "episode" && prev.node.id === updated.id
          ? { ...prev, node: updated }
          : prev,
      );
      await refresh(true);
      return true;
    } catch (err) {
      toast.error(bizMessage(err));
      return false;
    }
  };

  const handleEpisodeDeleteConfirm = async (): Promise<boolean> => {
    if (!deleteEpisodeTarget) return false;
    try {
      await memoryService.deleteEpisode(deleteEpisodeTarget.id);
      toast.success("已删除事件及其全部参与（不可恢复）");
      setSelection(null);
      await refresh(true);
      return true;
    } catch (err) {
      toast.error(bizMessage(err));
      return false;
    }
  };

  const handleLinkSubmit = async (
    link: EpisodeLinkTarget,
    payload: EpisodeLinkUpdatePayload,
  ): Promise<boolean> => {
    try {
      await memoryService.updateEpisodeLink(link.linkId, payload);
      toast.success("事件参与已更新");
      await refresh(true);
      return true;
    } catch (err) {
      toast.error(bizMessage(err));
      return false;
    }
  };

  // ---------------- 危险操作区（L4）：当日清除 / 会话遗忘 / 整体重置 ----------------

  const [maintenance, setMaintenance] = useState<MaintenanceKind>(null);

  const handlePurgePreview = async (payload: PurgeRequest): Promise<PurgePreview | null> => {
    try {
      return await memoryService.purgePreview(payload);
    } catch (err) {
      toast.error(bizMessage(err));
      return null;
    }
  };

  const handlePurgeSubmit = async (payload: PurgeRequest): Promise<PurgeResult | null> => {
    try {
      const result = await memoryService.purgeMemory(payload);
      const scopeLabel = payload.scope === "day" ? "当日清除" : "会话遗忘";
      toast.success(
        `${scopeLabel}完成：归档 ${result.archivedStatements} 条事实、` +
        `删除 ${result.deletedEpisodes} 个事件、清理 ${result.deletedEntities} 个孤立实体`,
      );
      setSelection(null); // 清除范围内的选中对象可能已不在图上
      await refresh(true);
      return result;
    } catch (err) {
      toast.error(bizMessage(err));
      return null;
    }
  };

  const downloadExport = async () => {
    const bytes = await memoryService.exportMemory();
    const blob = new Blob([bytes as unknown as BlobPart], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchorEl = document.createElement("a");
    anchorEl.href = url;
    anchorEl.download = `memory-export-${new Date().toISOString().slice(0, 10)}.json`;
    anchorEl.click();
    URL.revokeObjectURL(url);
  };

  const handleResetConfirm = async (exportFirst: boolean): Promise<boolean> => {
    if (exportFirst) {
      try {
        await downloadExport();
        toast.success("备份已导出");
      } catch (err) {
        toast.error(bizMessage(err));
        return false; // 备份失败不重置
      }
    }
    try {
      const counts = await memoryService.resetMemory("重置");
      toast.success(
        `记忆已重置：清除实体 ${counts.memory_entity ?? 0}、事实 ${counts.memory_statement ?? 0}、` +
        `事件 ${counts.memory_episode ?? 0}`,
      );
      setSelection(null);
      await refresh(true);
      return true;
    } catch (err) {
      toast.error(bizMessage(err));
      return false;
    }
  };

  return {
    dialog,
    setDialog,
    archiveTarget,
    setArchiveTarget,
    deleteTarget,
    setDeleteTarget,
    deleteEpisodeTarget,
    setDeleteEpisodeTarget,
    maintenance,
    setMaintenance,
    entities,
    statementLabel,
    episodeLabelOf,
    identityContext,
    handleCorrectSubmit,
    handleAddFactSubmit,
    handleEntitySubmit,
    handleArchiveConfirm,
    handleMergeSubmit,
    handleSplitSubmit,
    handleDeleteConfirm,
    handleEpisodeSubmit,
    handleEpisodeDeleteConfirm,
    handleLinkSubmit,
    handlePurgePreview,
    handlePurgeSubmit,
    handleResetConfirm,
  };
};
