import "@xyflow/react/dist/style.css";

import { useCallback, useEffect, useMemo, useState, type FC } from "react";
import { useNavigate } from "react-router";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import {
  ArrowLeftIcon,
  BrainIcon,
  CalendarIcon,
  FilterXIcon,
  RefreshCwIcon,
  SettingsIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  EntityEditDialog,
  FactEditDialog,
} from "@/components/memory-graph/edit-dialogs";
import {
  MergeEntityDialog,
  SplitEntityDialog,
} from "@/components/memory-graph/identity-dialogs";
import {
  EpisodeEditDialog,
  EpisodeLinkEditDialog,
  type EpisodeLinkTarget,
} from "@/components/memory-graph/edit-dialogs";
import {
  DayPurgeDialog,
  ResetMemoryDialog,
  ThreadForgetDialog,
} from "@/components/memory-graph/maintenance-dialogs";
import {
  ENTITY_TYPE_COLOR_FALLBACK,
  ENTITY_TYPE_COLORS,
  buildFocusSets,
  buildGraphModel,
  filterSnapshot,
  isEmptySnapshot,
  type GraphKindFilters,
} from "@/components/memory-graph/layout";
import { memoryNodeTypes } from "@/components/memory-graph/node-types";
import {
  MemoryDetailPanel,
  type MemorySelection,
} from "@/components/memory-graph/detail-panel";
import { MemoryGraphLegend } from "@/components/memory-graph/legend";
import { useMemoryGraph } from "@/hooks/use-memory-graph";
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
  MemoryStatementEdge,
  StatementWritePayload,
} from "@/services/memory-service";

/**
 * 记忆图谱页（/admin/memory-graph，管理侧记忆模块）。
 *
 * 消费 GET /memory/graph 快照：实体/情节为节点、陈述为带谓词边，
 * 力导向交给确定性径向布局（layout.ts）。顶栏提供手动刷新、「时点回放」
 * ——选日期后后端按双时间轴切片，历史事实（含已被取代的旧值）以虚线
 * 入图——以及「实体/事件/事实」类型筛选开关（可多选，默认全开）。
 * 点击节点/陈述边打开右侧档案面板；点节点同时以它为核 1 跳聚焦，
 * 集合外的节点/边淡出，点淡出节点可换核继续扩散。
 * 面板内可编辑（L1–L3）：事实纠正/归档、实体改名/合并/拆分/删除、
 * 补充事实、事件摘要/场景/时间直改与删除、参与改挂——变更后静默刷新
 * 快照，toast 反馈结果。
 */

const GRAPH_LIMIT = 300;

/** 聚焦淡出的基础过渡类（节点/边共用），淡出时再叠加透明度/去饱和 */
const FADE_TRANSITION_CLASS = "transition-opacity duration-300";

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

/** 详情面板触发的编辑弹窗（L1 事实纠错 + L2 身份纠错 + L3 事件编辑） */
type EditDialogState =
  | { kind: "correct"; statement: MemoryStatementEdge }
  | { kind: "add-fact"; entity: MemoryEntityNode }
  | { kind: "edit-entity"; entity: MemoryEntityNode }
  | { kind: "merge"; entity: MemoryEntityNode }
  | { kind: "split"; entity: MemoryEntityNode }
  | { kind: "edit-episode"; node: MemoryEpisodeNode }
  | { kind: "edit-link"; link: EpisodeLinkTarget }
  | null;

/** "e:3" → 3（快照 id → 数据库主键载荷） */
const numRef = (id: string): number | null => {
  const n = Number(id.split(":")[1]);
  return Number.isFinite(n) ? n : null;
};

const bizMessage = (err: unknown): string =>
  err instanceof BizError ? err.message : "操作失败，请稍后重试";

export const MemoryGraphPage: FC = () => {
  const navigate = useNavigate();
  const [at, setAt] = useState<string | null>(null);
  const [kinds, setKinds] = useState<GraphKindFilters>({
    entity: true,
    episode: true,
    statement: true,
  });
  const [selection, setSelection] = useState<MemorySelection | null>(null);
  const { snapshot, isLoading, refresh } = useMemoryGraph({ at, limit: GRAPH_LIMIT });

  // 类型筛选 → 图模型：字面量事实行随 statement 过滤一并消失
  const filteredSnapshot = useMemo(
    () => (snapshot ? filterSnapshot(snapshot, kinds) : null),
    [snapshot, kinds],
  );
  const model = useMemo(
    () => (filteredSnapshot ? buildGraphModel(filteredSnapshot) : null),
    [filteredSnapshot],
  );

  // React Flow 受控状态：布局结果变化（刷新/切换时点/切筛选）时整体重置
  const [nodes, setNodes, onNodesChange] = useNodesState(model?.flowNodes ?? []);
  const [edges, setEdges, onEdgesChange] = useEdgesState(model?.flowEdges ?? []);
  useEffect(() => {
    if (!model) return;
    setNodes(model.flowNodes);
    setEdges(model.flowEdges);
  }, [model, setNodes, setEdges]);

  // 聚焦淡出：选中节点后以它为核 1 跳扩散。就地覆盖 className 而非整体
  // 重建，保留用户拖拽过的节点位置；清空选中（focus=null）即整体恢复。
  // 声明须在上面的整体重置 effect 之后：模型重建后先落位、再打淡出。
  const focus = useMemo(() => {
    if (!model || !selection || selection.kind === "statement") return null;
    return buildFocusSets(model, selection.node.id);
  }, [model, selection]);
  useEffect(() => {
    setNodes((prev) =>
      prev.map((node) => {
        const className =
          focus && !focus.nodeIds.has(node.id)
            ? `${FADE_TRANSITION_CLASS} opacity-25 saturate-50`
            : FADE_TRANSITION_CLASS;
        return node.className === className ? node : { ...node, className };
      }),
    );
    setEdges((prev) =>
      prev.map((edge) => {
        const className =
          focus && !focus.edgeIds.has(edge.id)
            ? `${FADE_TRANSITION_CLASS} opacity-15`
            : FADE_TRANSITION_CLASS;
        return edge.className === className ? edge : { ...edge, className };
      }),
    );
  }, [focus, setNodes, setEdges]);

  // 时点/筛选变化后同步清空选中：旧选中对象可能已不在切片里
  const changeAt = (value: string | null) => {
    setAt(value);
    setSelection(null);
  };

  /** 切换类型筛选；允许全部关闭——画布会给出「筛选为空」提示卡 */
  const toggleKind = (kind: keyof GraphKindFilters) => {
    setKinds((prev) => ({ ...prev, [kind]: !prev[kind] }));
    setSelection(null); // 选中对象可能已被过滤出图
  };

  // ---------------- 编辑（L1）：弹窗托管 + 变更后静默刷新 ----------------

  const [dialog, setDialog] = useState<EditDialogState>(null);
  const [archiveTarget, setArchiveTarget] = useState<MemoryStatementEdge | null>(null);

  const entities = useMemo(
    () => snapshot?.nodes.filter((n): n is MemoryEntityNode => n.kind === "entity") ?? [],
    [snapshot],
  );

  const entityNameOf = (id: string | null | undefined): string => {
    if (!id) return "";
    const node = model?.nodeById.get(id);
    return node?.kind === "entity" ? node.name : id;
  };
  const statementLabel = (edge: MemoryStatementEdge): string =>
    edge.target ? entityNameOf(edge.target) : edge.objectText ?? "（字面量）";

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

  const [maintenance, setMaintenance] = useState<null | "day" | "thread" | "reset">(null);

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

  const handleNodeClick = (_: unknown, node: { id: string }) => {
    if (!model) return;
    const memory = model.nodeById.get(node.id);
    if (!memory) return;
    if (memory.kind === "entity") {
      setSelection({
        kind: "entity",
        node: memory as MemoryEntityNode,
        facts: model.factsByEntity.get(node.id) ?? [],
        relations: model.relationsByEntity.get(node.id) ?? [],
        participations: (model.linksByEntity.get(node.id) ?? []).map((l) => ({
          linkId: l.linkId,
          role: l.role,
          label: episodeLabelOf(l.episodeId, l.role),
        })),
      });
    } else {
      setSelection({
        kind: "episode",
        node: memory as MemoryEpisodeNode,
        roles: model.rolesByEpisode.get(node.id) ?? [],
      });
    }
  };

  const openStatement = (statementId: string) => {
    if (!model) return;
    const statement = model.statementById.get(statementId);
    if (!statement) return; // episode_link 边不单独建档案，点击其事件节点查看
    const endpoints = model.endpointNamesByStatement.get(statementId);
    setSelection({
      kind: "statement",
      edge: statement,
      sourceName: endpoints?.source ?? statement.source,
      targetName: endpoints?.target ?? null,
    });
  };

  const handleEdgeClick = (_: unknown, edge: { id: string }) => openStatement(edge.id);

  const stats = snapshot?.stats;
  const rawEmpty = isEmptySnapshot(snapshot) && !isLoading;
  const viewEmpty =
    rawEmpty ||
    (model !== null &&
      model.flowNodes.length === 0 &&
      model.flowEdges.length === 0);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background">
      {/* 顶栏：返回 / 标题 / 统计 / 时点回放 / 刷新 */}
      <header className="flex flex-wrap items-center gap-3 border-b border-border/60 px-6 py-3">
        <Button
          variant="ghost"
          size="sm"
          className="-ml-2 w-fit text-muted-foreground"
          onClick={() => void navigate("/admin")}
        >
          <ArrowLeftIcon />
          返回控制台
        </Button>
        <div className="mr-auto">
          <h1 className="flex items-center gap-2 font-heading text-lg font-semibold tracking-tight">
            <BrainIcon className="size-5 text-primary" />
            记忆图谱
          </h1>
          <p className="text-xs text-muted-foreground">
            跨会话长期记忆的可视化：人/物、事件与事实关系
          </p>
        </div>

        {/* 类型筛选开关：色点与图例同源；点击切换该类节点/边是否入图 */}
        {stats && (
          <div className="hidden items-center gap-1.5 md:flex">
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
                    "h-7 gap-1.5 rounded-full px-2.5 text-xs font-normal",
                    active ? "" : "text-muted-foreground",
                  ].join(" ")}
                  onClick={() => toggleKind(kind)}
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
            onChange={(event) => changeAt(event.target.value || null)}
            aria-label="时点回放日期"
          />
          {at && (
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => changeAt(null)}
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
            <DropdownMenuItem onClick={() => setMaintenance("day")}>当日清除…</DropdownMenuItem>
            <DropdownMenuItem onClick={() => setMaintenance("thread")}>按会话遗忘…</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-destructive data-highlighted:text-destructive"
              onClick={() => setMaintenance("reset")}
            >
              整体重置…
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>

        <Button
          variant="outline"
          size="sm"
          disabled={isLoading}
          onClick={() => void refresh()}
        >
          <RefreshCwIcon className={isLoading ? "animate-spin" : ""} />
          刷新
        </Button>
      </header>

      {at && (
        <div className="border-b border-indigo-100 bg-indigo-50/70 px-6 py-1.5 text-xs text-indigo-600">
          时点回放 · <span className="font-medium">{at}</span>
          （23:59:59 切片）：该日期仍在效的事实全部入图，含此后被新值取代的
          历史记录（虚线、标注“已取代”）
        </div>
      )}

      {/* 画布区 */}
      <div className="relative flex-1">
        {rawEmpty && (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <div className="flex max-w-sm flex-col items-center gap-3 rounded-xl border border-border/70 bg-card px-8 py-10 text-center shadow-card">
              <BrainIcon className="size-10 text-primary/60" />
              <div className="font-medium">还没有可展示的记忆</div>
              <p className="text-sm text-muted-foreground">
                回到对话页聊几句你的背景、偏好或正在做的事，
                系统会在每轮结束后自动沉淀长期记忆。
              </p>
              <Button size="sm" onClick={() => void navigate("/")}>
                去对话页聊聊
              </Button>
            </div>
          </div>
        )}

        {viewEmpty && !rawEmpty && (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <div className="flex max-w-sm flex-col items-center gap-3 rounded-xl border border-border/70 bg-card px-8 py-10 text-center shadow-card">
              <FilterXIcon className="size-10 text-muted-foreground/60" />
              <div className="font-medium">当前筛选下没有可显示的内容</div>
              <p className="text-sm text-muted-foreground">
                顶栏「实体 / 事件 / 事实」至少开启一项，
                对应的记忆就会回到图上。
              </p>
            </div>
          </div>
        )}

        {!snapshot && isLoading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <Skeleton className="size-24 rounded-xl" />
              <Skeleton className="h-4 w-40" />
            </div>
          </div>
        )}

        {model && !viewEmpty && (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={memoryNodeTypes}
            onNodeClick={handleNodeClick}
            onEdgeClick={handleEdgeClick}
            onPaneClick={() => setSelection(null)}
            fitView
            fitViewOptions={{ padding: 0.22, duration: 400 }}
            minZoom={0.15}
            maxZoom={1.75}
            proOptions={{ hideAttribution: false }}
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1.4} />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              position="top-left"
              nodeColor={(node) => {
                if (node.type === "episode") return "#a5b4fc";
                const memory = (node.data as { memory?: { entityType?: string; isUser?: boolean } })
                  .memory;
                if (memory?.isUser) return "var(--primary, #c2622d)";
                return (
                  (memory?.entityType &&
                    ENTITY_TYPE_COLORS[memory.entityType]) ||
                  ENTITY_TYPE_COLOR_FALLBACK
                );
              }}
            />
            <MemoryGraphLegend />
            {selection && (
              <MemoryDetailPanel
                selection={selection}
                onClose={() => setSelection(null)}
                onEditEntity={(node) => setDialog({ kind: "edit-entity", entity: node })}
                onAddFact={(node) => setDialog({ kind: "add-fact", entity: node })}
                onCorrectStatement={(edge) => setDialog({ kind: "correct", statement: edge })}
                onArchiveStatement={(edge) => setArchiveTarget(edge)}
                onOpenStatement={openStatement}
                onMergeEntity={(node) => setDialog({ kind: "merge", entity: node })}
                onSplitEntity={(node) => setDialog({ kind: "split", entity: node })}
                onDeleteEntity={(node) => setDeleteTarget(node)}
                onEditEpisode={(node) => setDialog({ kind: "edit-episode", node })}
                onDeleteEpisode={(node) => setDeleteEpisodeTarget(node)}
                onEditLink={(link) => setDialog({ kind: "edit-link", link })}
              />
            )}
          </ReactFlow>
        )}
      </div>

      {/* 编辑弹窗（Portal 渲染，挂在页面根避免随画布卸载） */}
      {dialog?.kind === "correct" && (
        <FactEditDialog
          open
          statement={dialog.statement}
          entities={entities}
          onSubmit={(payload) => handleCorrectSubmit(dialog.statement, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "add-fact" && (
        <FactEditDialog
          open
          anchorEntity={dialog.entity}
          entities={entities}
          onSubmit={(payload) => handleAddFactSubmit(dialog.entity, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "edit-entity" && (
        <EntityEditDialog
          open
          entity={dialog.entity}
          onSubmit={(payload) => handleEntitySubmit(dialog.entity, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "merge" && identityContext && (
        <MergeEntityDialog
          open
          context={identityContext}
          entities={entities}
          onSubmit={(targetRef) => handleMergeSubmit(dialog.entity, targetRef)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "split" && identityContext && (
        <SplitEntityDialog
          open
          context={identityContext}
          onSubmit={(payload) => handleSplitSubmit(dialog.entity, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "edit-episode" && (
        <EpisodeEditDialog
          open
          episode={dialog.node}
          onSubmit={(payload) => handleEpisodeSubmit(dialog.node, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "edit-link" && (
        <EpisodeLinkEditDialog
          open
          link={dialog.link}
          entities={entities}
          onSubmit={(payload) => handleLinkSubmit(dialog.link, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      <DayPurgeDialog
        open={maintenance === "day"}
        onOpenChange={(open) => {
          if (!open) setMaintenance(null);
        }}
        onPreview={handlePurgePreview}
        onSubmit={handlePurgeSubmit}
      />
      <ThreadForgetDialog
        open={maintenance === "thread"}
        onOpenChange={(open) => {
          if (!open) setMaintenance(null);
        }}
        onPreview={handlePurgePreview}
        onSubmit={handlePurgeSubmit}
      />
      <ResetMemoryDialog
        open={maintenance === "reset"}
        onOpenChange={(open) => {
          if (!open) setMaintenance(null);
        }}
        onConfirm={handleResetConfirm}
      />
      <ConfirmDialog
        open={deleteEpisodeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteEpisodeTarget(null);
        }}
        title="删除这个事件？"
        description={`「${deleteEpisodeTarget?.summary.slice(0, 40) ?? ""}」及其全部参与将从所有时间视图移除，不可恢复。`}
        confirmText="删除"
        destructive
        onConfirm={handleEpisodeDeleteConfirm}
      />
      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
        title="删除孤立实体？"
        description={`「${deleteTarget?.name ?? ""}」没有任何事实与事件引用，删除后不可恢复。`}
        confirmText="删除"
        destructive
        onConfirm={handleDeleteConfirm}
      />
      <ConfirmDialog
        open={archiveTarget !== null}
        onOpenChange={(open) => {
          if (!open) setArchiveTarget(null);
        }}
        title="归档这条事实？"
        description={`「${archiveTarget ? `${archiveTarget.predicate} ${statementLabel(archiveTarget)}` : ""}」将退出当前图谱；时点回放中仍可追溯。`}
        confirmText="归档"
        destructive
        onConfirm={handleArchiveConfirm}
      />
    </div>
  );
};
