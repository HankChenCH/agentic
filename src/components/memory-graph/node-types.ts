import type { NodeTypes } from "@xyflow/react";

import { EntityNodeView, EpisodeNodeView } from "@/components/memory-graph/nodes";

/**
 * React Flow 节点类型注册表。
 *
 * 单独成文件：nodes.tsx 若同时导出组件与该常量会触发 react-refresh 的
 * only-export-components 警告（fast-refresh 要求组件文件只出组件）。
 */
export const memoryNodeTypes: NodeTypes = {
  entity: EntityNodeView,
  episode: EpisodeNodeView,
};
