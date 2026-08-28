import { getJson } from "@/lib/http";

/**
 * 记忆图谱 REST 服务（对应后端 app/api/v1/endpoints/memory.py）。
 *
 * 图快照是记忆系统 v2（server/docs/memory-v2-design.md）管理侧只读投影：
 * 实体/情节为节点、陈述为带谓词边。注意本接口的字段是 camelCase（后端
 * graph_snapshot.py 的对外契约），与其余 snake_case 镜像类型（types.ts）不同源。
 */

export type MemoryEntityNodeType =
  | "PERSON"
  | "OBJECT"
  | "PLACE"
  | "ORG"
  | "CONCEPT"
  | "OTHER";

export interface MemoryEntityNode {
  id: string;
  kind: "entity";
  entityType: MemoryEntityNodeType | string;
  name: string;
  aliases: string[];
  isUser: boolean;
  importance: number;
  accessCount: number;
  lastAccessedAt: string | null;
}

export interface MemoryEpisodeNode {
  id: string;
  kind: "episode";
  summary: string;
  scene: string | null;
  occurredAt: string | null;
  accessCount: number;
}

export type MemoryGraphNode =
  | MemoryEntityNode
  | MemoryEpisodeNode;

export interface MemoryStatementEdge {
  id: string;
  kind: "statement";
  source: string;
  /** 客体为实体时的节点 id；字面量客体（objectText）时为 null */
  target: string | null;
  objectText: string | null;
  predicate: string;
  summary: string;
  state: "ACTIVE" | "SUPERSEDED" | "ARCHIVED";
  validFrom: string | null;
  validTo: string | null;
  confidence: number;
  origin: "EXTRACTED" | "MANUAL";
  sourceThreadId: string | null;
  sourceTurnId: string | null;
}

export interface MemoryEpisodeLinkEdge {
  id: string;
  kind: "episode_link";
  source: string;
  target: string;
  role: string | null;
}

export type MemoryGraphEdge =
  | MemoryStatementEdge
  | MemoryEpisodeLinkEdge;

export interface MemoryGraphStats {
  entityNodes: number;
  episodeNodes: number;
  statementEdges: number;
  episodeLinkEdges: number;
}

export interface MemoryGraphSnapshot {
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
  /** 时点回放锚（null = 当前态快照） */
  at: string | null;
  generatedAt: string;
  stats: MemoryGraphStats;
}

export const memoryService = {
  /**
   * 拉取记忆图谱快照。
   * GET /memory/graph?at=&limit=
   * - at 缺省返回当前态（仅 ACTIVE 事实）；传 ISO 日期做时点回放
   *   （含该时刻仍生效的已取代历史事实）。
   */
  async graphSnapshot(params: { at?: string | null; limit?: number } = {}) {
    return getJson<MemoryGraphSnapshot>("/memory/graph", {
      params: { at: params.at ?? undefined, limit: params.limit },
    });
  },
};
