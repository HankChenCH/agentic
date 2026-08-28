import "@xyflow/react/dist/style.css";

import { useEffect, useMemo, useState, type FC } from "react";
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
  RefreshCwIcon,
  XIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ENTITY_TYPE_COLOR_FALLBACK,
  ENTITY_TYPE_COLORS,
  buildGraphModel,
  isEmptySnapshot,
} from "@/components/memory-graph/layout";
import { memoryNodeTypes } from "@/components/memory-graph/node-types";
import {
  MemoryDetailPanel,
  type MemorySelection,
} from "@/components/memory-graph/detail-panel";
import { MemoryGraphLegend } from "@/components/memory-graph/legend";
import { useMemoryGraph } from "@/hooks/use-memory-graph";
import type {
  MemoryEntityNode,
  MemoryEpisodeNode,
} from "@/services/memory-service";

/**
 * 记忆图谱页（/admin/memory-graph，管理侧记忆模块）。
 *
 * 消费 GET /memory/graph 快照：实体/情节为节点、陈述为带谓词边，
 * 力导向交给确定性径向布局（layout.ts）。顶栏提供手动刷新与「时点回放」
 * ——选日期后后端按双时间轴切片，历史事实（含已被取代的旧值）以虚线
 * 入图。点击节点/陈述边打开右侧档案面板。
 */

const GRAPH_LIMIT = 300;

export const MemoryGraphPage: FC = () => {
  const navigate = useNavigate();
  const [at, setAt] = useState<string | null>(null);
  const [selection, setSelection] = useState<MemorySelection | null>(null);
  const { snapshot, isLoading, refresh } = useMemoryGraph({ at, limit: GRAPH_LIMIT });

  const model = useMemo(
    () => (snapshot ? buildGraphModel(snapshot) : null),
    [snapshot],
  );

  // React Flow 受控状态：布局结果变化（刷新/切换时点）时整体重置
  const [nodes, setNodes, onNodesChange] = useNodesState(model?.flowNodes ?? []);
  const [edges, setEdges, onEdgesChange] = useEdgesState(model?.flowEdges ?? []);
  useEffect(() => {
    if (!model) return;
    setNodes(model.flowNodes);
    setEdges(model.flowEdges);
  }, [model, setNodes, setEdges]);

  // 切换时点后清空选中：旧选中对象可能已不在切片里
  useEffect(() => setSelection(null), [at]);

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
      });
    } else {
      setSelection({
        kind: "episode",
        node: memory as MemoryEpisodeNode,
        roles: (model.rolesByEpisode.get(node.id) ?? []).map((role) => ({
          entityName: role.entityName,
          role: role.role,
        })),
      });
    }
  };

  const handleEdgeClick = (_: unknown, edge: { id: string }) => {
    if (!model) return;
    const statement = model.statementById.get(edge.id);
    if (!statement) return; // episode_link 边不单独建档案，点击其事件节点查看
    const endpoints = model.endpointNamesByStatement.get(edge.id);
    setSelection({
      kind: "statement",
      edge: statement,
      sourceName: endpoints?.source ?? statement.source,
      targetName: endpoints?.target ?? null,
    });
  };

  const stats = snapshot?.stats;
  const empty = isEmptySnapshot(snapshot) && !isLoading;

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

        {stats && (
          <div className="hidden items-center gap-1.5 md:flex">
            <Badge variant="outline" className="rounded-full font-normal">
              实体 {stats.entityNodes}
            </Badge>
            <Badge variant="outline" className="rounded-full font-normal">
              事件 {stats.episodeNodes}
            </Badge>
            <Badge variant="outline" className="rounded-full font-normal">
              事实 {stats.statementEdges}
            </Badge>
          </div>
        )}

        {/* 时点回放：日期粒度切片（后端按双时间轴取该日仍在效的事实） */}
        <div className="flex items-center gap-1.5">
          <CalendarIcon className="size-4 text-muted-foreground" />
          <Input
            type="date"
            className="h-8 w-40 text-xs"
            value={at ?? ""}
            onChange={(event) => setAt(event.target.value || null)}
            aria-label="时点回放日期"
          />
          {at && (
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              onClick={() => setAt(null)}
              aria-label="退出回放，回到当前态"
            >
              <XIcon className="size-3.5" />
            </Button>
          )}
        </div>

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
        {empty && (
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

        {!snapshot && isLoading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3">
              <Skeleton className="size-24 rounded-xl" />
              <Skeleton className="h-4 w-40" />
            </div>
          </div>
        )}

        {model && !empty && (
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
              className="!bottom-12"
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
              />
            )}
          </ReactFlow>
        )}
      </div>
    </div>
  );
};
