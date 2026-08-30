import "@xyflow/react/dist/style.css";

import { type FC, type ReactNode } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type NodeTypes,
  type OnEdgesChange,
  type OnNodesChange,
} from "@xyflow/react";
import { BrainIcon, FilterXIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ENTITY_TYPE_COLOR_FALLBACK,
  ENTITY_TYPE_COLORS,
  type MemoryFlowEdge,
  type MemoryFlowNode,
} from "@/components/memory-graph/layout";
import { MemoryGraphLegend } from "@/components/memory-graph/legend";

interface GraphCanvasProps {
  /** 快照为空（没有任何记忆） */
  rawEmpty: boolean;
  /** 快照非空但当前筛选/时点下画布无内容 */
  viewEmpty: boolean;
  /** 首屏加载（尚无快照数据） */
  isLoading: boolean;
  /** 图模型已就绪（渲染 ReactFlow） */
  hasModel: boolean;
  nodes: MemoryFlowNode[];
  edges: MemoryFlowEdge[];
  onNodesChange: OnNodesChange<MemoryFlowNode>;
  onEdgesChange: OnEdgesChange<MemoryFlowEdge>;
  nodeTypes: NodeTypes;
  onNodeClick: (event: unknown, node: { id: string }) => void;
  onEdgeClick: (event: unknown, edge: { id: string }) => void;
  onPaneClick: () => void;
  /** 画布内叠加层（详情面板），由页面传入 */
  children?: ReactNode;
  /** 空态引导按钮（跳对话页）；不传则不显示 */
  onGoChat?: () => void;
}

/**
 * 记忆图谱画布区：空态引导卡（无记忆 / 筛选为空）、首屏骨架与
 * ReactFlow 画布（点阵背景、控件、小地图、图例）。详情面板由页面以
 * children 形式叠加，保证其仍渲染在 ReactFlow 内部以获得定位上下文。
 */
export const GraphCanvas: FC<GraphCanvasProps> = ({
  rawEmpty,
  viewEmpty,
  isLoading,
  hasModel,
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  nodeTypes,
  onNodeClick,
  onEdgeClick,
  onPaneClick,
  children,
  onGoChat,
}) => (
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
          {onGoChat && (
            <Button size="sm" onClick={onGoChat}>
              去对话页聊聊
            </Button>
          )}
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

    {isLoading && !hasModel && (
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <Skeleton className="size-24 rounded-xl" />
          <Skeleton className="h-4 w-40" />
        </div>
      </div>
    )}

    {hasModel && !viewEmpty && (
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
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
        {children}
      </ReactFlow>
    )}
  </div>
);
