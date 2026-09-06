import { useEffect, useMemo, useState, type FC } from "react";
import { useNavigate } from "react-router";
import { useEdgesState, useNodesState } from "@xyflow/react";

import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import {
  EntityEditDialog,
  EpisodeEditDialog,
  EpisodeLinkEditDialog,
  FactEditDialog,
} from "@/components/memory-graph/edit-dialogs";
import {
  MergeEntityDialog,
  SplitEntityDialog,
} from "@/components/memory-graph/identity-dialogs";
import {
  DayPurgeDialog,
  ResetMemoryDialog,
  ThreadForgetDialog,
} from "@/components/memory-graph/maintenance-dialogs";
import {
  buildFocusSets,
  buildGraphModel,
  filterSnapshot,
  isEmptySnapshot,
  type GraphKindFilters,
} from "@/components/memory-graph/layout";
import { memoryNodeTypes } from "@/components/memory-graph/node-types";
import { MemoryDetailPanel, type MemorySelection } from "@/components/memory-graph/detail-panel";
import { GraphCanvas } from "@/components/memory-graph/graph-canvas";
import { GraphHeader } from "@/components/memory-graph/graph-header";
import { useMemoryGraphActions } from "@/components/memory-graph/use-memory-graph-actions";
import { useMemoryGraph } from "@/hooks/use-memory-graph";

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
 *
 * 组成：图状态（筛选/选中/聚焦淡出）留在本页，顶栏（graph-header）、
 * 画布（graph-canvas）与 L1–L4 变更动作（use-memory-graph-actions）各自成模块。
 */

const GRAPH_LIMIT = 300;

/** 聚焦淡出的基础过渡类（节点/边共用），淡出时再叠加透明度/去饱和 */
const FADE_TRANSITION_CLASS = "transition-opacity duration-300";

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

  // ---------------- L1–L4 变更动作（弹窗托管 + 提交处理） ----------------

  const actions = useMemoryGraphActions({
    model,
    snapshotNodes: snapshot?.nodes ?? [],
    refresh,
    selection,
    setSelection,
  });

  // 局部别名：供 JSX 判别窄化（actions.dialog 是可变属性，窄化不进回调）
  const dialog = actions.dialog;
  const { setDialog } = actions;

  const handleNodeClick = (_: unknown, node: { id: string }) => {
    if (!model) return;
    const memory = model.nodeById.get(node.id);
    if (!memory) return;
    if (memory.kind === "entity") {
      setSelection({
        kind: "entity",
        node: memory,
        facts: model.factsByEntity.get(node.id) ?? [],
        relations: model.relationsByEntity.get(node.id) ?? [],
        participations: (model.linksByEntity.get(node.id) ?? []).map((l) => ({
          linkId: l.linkId,
          role: l.role,
          label: actions.episodeLabelOf(l.episodeId, l.role),
        })),
      });
    } else {
      setSelection({
        kind: "episode",
        node: memory,
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

  const rawEmpty = isEmptySnapshot(snapshot) && !isLoading;
  const viewEmpty =
    rawEmpty ||
    (model !== null &&
      model.flowNodes.length === 0 &&
      model.flowEdges.length === 0);

  return (
    <div className="flex h-dvh flex-col overflow-hidden bg-background">
      <GraphHeader
        stats={snapshot?.stats ?? null}
        kinds={kinds}
        onToggleKind={toggleKind}
        at={at}
        onChangeAt={changeAt}
        isLoading={isLoading}
        onRefresh={() => void refresh()}
        onOpenMaintenance={actions.setMaintenance}
      />

      {at && (
        <div className="border-b border-indigo-100 bg-indigo-50/70 px-4 py-1.5 text-xs text-indigo-600 sm:px-6">
          时点回放 · <span className="font-medium">{at}</span>
          （23:59:59 切片）：该日期仍在效的事实全部入图，含此后被新值取代的
          历史记录（虚线、标注“已取代”）
        </div>
      )}

      <GraphCanvas
        rawEmpty={rawEmpty}
        viewEmpty={viewEmpty}
        isLoading={isLoading}
        hasModel={model !== null}
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={memoryNodeTypes}
        onNodeClick={handleNodeClick}
        onEdgeClick={(_, edge) => openStatement(edge.id)}
        onPaneClick={() => setSelection(null)}
        onGoChat={() => void navigate("/")}
      >
        {selection && (
          <MemoryDetailPanel
            selection={selection}
            onClose={() => setSelection(null)}
            onEditEntity={(node) => setDialog({ kind: "edit-entity", entity: node })}
            onAddFact={(node) => setDialog({ kind: "add-fact", entity: node })}
            onCorrectStatement={(edge) => setDialog({ kind: "correct", statement: edge })}
            onArchiveStatement={(edge) => actions.setArchiveTarget(edge)}
            onOpenStatement={openStatement}
            onMergeEntity={(node) => setDialog({ kind: "merge", entity: node })}
            onSplitEntity={(node) => setDialog({ kind: "split", entity: node })}
            onDeleteEntity={(node) => actions.setDeleteTarget(node)}
            onEditEpisode={(node) => setDialog({ kind: "edit-episode", node })}
            onDeleteEpisode={(node) => actions.setDeleteEpisodeTarget(node)}
            onEditLink={(link) => setDialog({ kind: "edit-link", link })}
          />
        )}
      </GraphCanvas>

      {/* 编辑弹窗（Portal 渲染，挂在页面根避免随画布卸载） */}
      {dialog?.kind === "correct" && (
        <FactEditDialog
          open
          statement={dialog.statement}
          entities={actions.entities}
          onSubmit={(payload) => actions.handleCorrectSubmit(dialog.statement, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "add-fact" && (
        <FactEditDialog
          open
          anchorEntity={dialog.entity}
          entities={actions.entities}
          onSubmit={(payload) => actions.handleAddFactSubmit(dialog.entity, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "edit-entity" && (
        <EntityEditDialog
          open
          entity={dialog.entity}
          onSubmit={(payload) => actions.handleEntitySubmit(dialog.entity, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "merge" && actions.identityContext && (
        <MergeEntityDialog
          open
          context={actions.identityContext}
          entities={actions.entities}
          onSubmit={(targetRef) => actions.handleMergeSubmit(dialog.entity, targetRef)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "split" && actions.identityContext && (
        <SplitEntityDialog
          open
          context={actions.identityContext}
          onSubmit={(payload) => actions.handleSplitSubmit(dialog.entity, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "edit-episode" && (
        <EpisodeEditDialog
          open
          episode={dialog.node}
          onSubmit={(payload) => actions.handleEpisodeSubmit(dialog.node, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      {dialog?.kind === "edit-link" && (
        <EpisodeLinkEditDialog
          open
          link={dialog.link}
          entities={actions.entities}
          onSubmit={(payload) => actions.handleLinkSubmit(dialog.link, payload)}
          onOpenChange={(open) => {
            if (!open) setDialog(null);
          }}
        />
      )}
      <DayPurgeDialog
        open={actions.maintenance === "day"}
        onOpenChange={(open) => {
          if (!open) actions.setMaintenance(null);
        }}
        onPreview={actions.handlePurgePreview}
        onSubmit={actions.handlePurgeSubmit}
      />
      <ThreadForgetDialog
        open={actions.maintenance === "thread"}
        onOpenChange={(open) => {
          if (!open) actions.setMaintenance(null);
        }}
        onPreview={actions.handlePurgePreview}
        onSubmit={actions.handlePurgeSubmit}
      />
      <ResetMemoryDialog
        open={actions.maintenance === "reset"}
        onOpenChange={(open) => {
          if (!open) actions.setMaintenance(null);
        }}
        onConfirm={actions.handleResetConfirm}
      />
      <ConfirmDialog
        open={actions.deleteEpisodeTarget !== null}
        onOpenChange={(open) => {
          if (!open) actions.setDeleteEpisodeTarget(null);
        }}
        title="删除这个事件？"
        description={`「${actions.deleteEpisodeTarget?.summary.slice(0, 40) ?? ""}」及其全部参与将从所有时间视图移除，不可恢复。`}
        confirmText="删除"
        destructive
        onConfirm={actions.handleEpisodeDeleteConfirm}
      />
      <ConfirmDialog
        open={actions.deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) actions.setDeleteTarget(null);
        }}
        title="删除孤立实体？"
        description={`「${actions.deleteTarget?.name ?? ""}」没有任何事实与事件引用，删除后不可恢复。`}
        confirmText="删除"
        destructive
        onConfirm={actions.handleDeleteConfirm}
      />
      <ConfirmDialog
        open={actions.archiveTarget !== null}
        onOpenChange={(open) => {
          if (!open) actions.setArchiveTarget(null);
        }}
        title="归档这条事实？"
        description={`「${actions.archiveTarget ? `${actions.archiveTarget.predicate} ${actions.statementLabel(actions.archiveTarget)}` : ""}」将退出当前图谱；时点回放中仍可追溯。`}
        confirmText="归档"
        destructive
        onConfirm={actions.handleArchiveConfirm}
      />
    </div>
  );
};
