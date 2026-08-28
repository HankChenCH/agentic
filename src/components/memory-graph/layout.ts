import type { Edge, Node } from "@xyflow/react";

import type {
  MemoryEntityNode,
  MemoryEpisodeNode,
  MemoryGraphNode,
  MemoryGraphSnapshot,
  MemoryStatementEdge,
} from "@/services/memory-service";

/**
 * 图模型构建：快照 JSON → React Flow 节点/边 + 查询索引。
 *
 * 布局取确定性径向排布（无Force仿真、无额外依赖）：
 * - 「用户」实体居中，其余实体按重要度排内/外双环；
 * - 情节（事件节点）铺最外环，按发生时间排序。
 * 字面量客体的事实（objectText，如图表没有对应实体节点）不画边，改为
 * 挂到主体节点的数据里以“事实行”呈现——避免悬空半边。
 */

// ---------------------------------------------------------------------------
// 实体类型配色（节点色点与图例共用同一映射）
// ---------------------------------------------------------------------------
export const ENTITY_TYPE_COLORS: Record<string, string> = {
  PERSON: "#d97706", // 琥珀：人
  PLACE: "#0284c7", // 天蓝：地点
  ORG: "#7c3aed", // 紫罗兰：组织
  OBJECT: "#059669", // 翠绿：物件
  CONCEPT: "#e11d48", // 玫红：概念
  OTHER: "#78716c", // 暖灰：未知
};

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  PERSON: "人物",
  PLACE: "地点",
  ORG: "组织",
  OBJECT: "物件",
  CONCEPT: "概念",
  OTHER: "其他",
};

export const ENTITY_TYPE_COLOR_FALLBACK = "#78716c";

/** 字面量事实：主体节点上内嵌展示的 (谓词 → 字面量) 行 */
export interface LiteralFact {
  statementId: string;
  predicate: string;
  objectText: string;
  state: MemoryStatementEdge["state"];
}

/** 实体节点的一跳关系（详情面板用） */
export interface EntityRelation {
  edgeId: string;
  predicate: string;
  targetName: string;
  state: MemoryStatementEdge["state"];
  origin: MemoryStatementEdge["origin"];
}

export interface MemoryEntityFlowData extends Record<string, unknown> {
  memory: MemoryEntityNode;
  /** 内嵌字面量事实行（最多展示 3 条，多余的在详情面板看） */
  facts: LiteralFact[];
}

export interface MemoryEpisodeFlowData extends Record<string, unknown> {
  memory: MemoryEpisodeNode;
  /** 参与角色（来自 episode_link 边，详情面板用；节点上只画边） */
  roles: { entityId: string; entityName: string; role: string | null }[];
}

/** React Flow 自定义节点类型：v12 泛型 = Node<数据, 节点名>（NodeProps 传整个节点类型） */
export type EntityFlowNode = Node<MemoryEntityFlowData, "entity">;
export type EpisodeFlowNode = Node<MemoryEpisodeFlowData, "episode">;

export type MemoryFlowNode =
  | EntityFlowNode
  | EpisodeFlowNode;

export type MemoryFlowEdge = Edge;

/** buildGraphModel 的产物：视图数据 + 详情面板所需的全部索引 */
export interface GraphModel {
  flowNodes: MemoryFlowNode[];
  flowEdges: MemoryFlowEdge[];
  nodeById: Map<string, MemoryGraphNode>;
  statementById: Map<string, MemoryStatementEdge>;
  factsByEntity: Map<string, LiteralFact[]>;
  relationsByEntity: Map<string, EntityRelation[]>;
  /** 情节参与角色（episode_link 边聚合，key = ep:{id}） */
  rolesByEpisode: Map<string, MemoryEpisodeFlowData["roles"]>;
  /** 边两端名称（边详情面板用，key = s:{id}） */
  endpointNamesByStatement: Map<string, { source: string; target: string | null }>;
}

const ENTITY_SIZE = { width: 176, height: 84 } as const;
const EPISODE_SIZE = { width: 240, height: 96 } as const;

const MAX_EMBEDDED_FACTS = 3;

export function buildGraphModel(snapshot: MemoryGraphSnapshot): GraphModel {
  const entities = snapshot.nodes.filter(
    (n): n is MemoryEntityNode => n.kind === "entity",
  );
  const episodes = snapshot.nodes.filter(
    (n): n is MemoryEpisodeNode => n.kind === "episode",
  );
  const statements = snapshot.edges.filter(
    (e): e is MemoryStatementEdge => e.kind === "statement",
  );

  // ---- 索引：名称 / 内嵌事实 / 一跳关系 --------------------------------
  const nameById = new Map<string, string>(
    entities.map((e) => [e.id, e.name] as const),
  );
  const factsByEntity = new Map<string, LiteralFact[]>();
  const relationsByEntity = new Map<string, EntityRelation[]>();
  const endpointNames = new Map<
    string,
    { source: string; target: string | null }
  >();

  for (const s of statements) {
    endpointNames.set(s.id, {
      source: nameById.get(s.source) ?? s.source,
      target: s.target ? (nameById.get(s.target) ?? s.target) : s.objectText,
    });
    const factRow: LiteralFact | null =
      s.target === null && s.objectText
        ? {
            statementId: s.id,
            predicate: s.predicate,
            objectText: s.objectText,
            state: s.state,
          }
        : null;
    if (factRow) {
      const bucket = factsByEntity.get(s.source) ?? [];
      bucket.push(factRow);
      factsByEntity.set(s.source, bucket);
    }
    if (s.target) {
      const row: EntityRelation = {
        edgeId: s.id,
        predicate: s.predicate,
        targetName: nameById.get(s.target) ?? (s.target.split(":")[1] ?? s.target),
        state: s.state,
        origin: s.origin,
      };
      const subjectBucket = relationsByEntity.get(s.source) ?? [];
      subjectBucket.push(row);
      relationsByEntity.set(s.source, subjectBucket);
      const objectBucket = relationsByEntity.get(s.target) ?? [];
      objectBucket.push({
        ...row,
        predicate: `被${s.predicate}`,
        targetName: nameById.get(s.source) ?? s.source,
      });
      relationsByEntity.set(s.target, objectBucket);
    }
  }

  const rolesByEpisode = new Map<
    string,
    MemoryEpisodeFlowData["roles"]
  >();
  for (const edge of snapshot.edges) {
    if (edge.kind !== "episode_link") continue;
    const bucket = rolesByEpisode.get(edge.source) ?? [];
    bucket.push({
      entityId: edge.target,
      entityName: nameById.get(edge.target) ?? edge.target,
      role: edge.role,
    });
    rolesByEpisode.set(edge.source, bucket);
  }

  // ---- 节点布局：用户居中 → 实体双环 → 情节外环 ------------------------
  const flowNodes: MemoryFlowNode[] = [];

  const sortedEntities = [...entities].sort((a, b) => {
    if (a.isUser !== b.isUser) return a.isUser ? -1 : 1;
    if (b.importance !== a.importance) return b.importance - a.importance;
    return a.name.localeCompare(b.name, "zh-Hans-CN");
  });
  const user = sortedEntities.find((e) => e.isUser);
  const others = sortedEntities.filter((e) => !e.isUser);

  if (user) {
      flowNodes.push({
        id: user.id,
        type: "entity" as const,
        position: { x: -ENTITY_SIZE.width / 2, y: -ENTITY_SIZE.height / 2 },
        data: {
          memory: user,
          facts: (factsByEntity.get(user.id) ?? []).slice(0, MAX_EMBEDDED_FACTS),
        },
        style: { width: ENTITY_SIZE.width, height: ENTITY_SIZE.height },
      });
  }

  const placeOnRing = (
    items: MemoryEntityNode[],
    radius: number,
    startAngleDeg: number,
  ) => {
    items.forEach((entity, index) => {
      const angle =
        ((startAngleDeg + (360 / Math.max(items.length, 1)) * index) *
          Math.PI) /
        180;
      flowNodes.push({
        id: entity.id,
        type: "entity" as const,
        position: {
          x: radius * Math.cos(angle) - ENTITY_SIZE.width / 2,
          y: radius * Math.sin(angle) - ENTITY_SIZE.height / 2,
        },
        data: {
          memory: entity,
          facts: (factsByEntity.get(entity.id) ?? []).slice(0, MAX_EMBEDDED_FACTS),
        },
        style: { width: ENTITY_SIZE.width, height: ENTITY_SIZE.height },
      });
    });
  };

  const innerCount = others.length <= 8 ? others.length : Math.min(6, others.length);
  const outerCount = others.length - innerCount;
  placeOnRing(others.slice(0, innerCount), 300, -90);
  if (outerCount > 0) {
    placeOnRing(others.slice(innerCount), 470, -65);
  }

  const sortedEpisodes = [...episodes].sort((a, b) =>
    (a.occurredAt ?? "").localeCompare(b.occurredAt ?? ""),
  );
  const episodeRadius = others.length > 8 ? 640 : 520;
  sortedEpisodes.forEach((episode, index) => {
    const angle =
      ((-90 + (360 / Math.max(sortedEpisodes.length, 1)) * index) * Math.PI) /
      180;
    flowNodes.push({
      id: episode.id,
      type: "episode" as const,
      position: {
        x: episodeRadius * Math.cos(angle) - EPISODE_SIZE.width / 2,
        y: episodeRadius * Math.sin(angle) - EPISODE_SIZE.height / 2,
      },
      data: { memory: episode, roles: rolesByEpisode.get(episode.id) ?? [] },
      style: { width: EPISODE_SIZE.width, height: EPISODE_SIZE.height },
    });
  });

  // ---- 边 --------------------------------------------------------------
  const flowEdges: MemoryFlowEdge[] = [];
  for (const s of statements) {
    if (!s.target) continue; // 字面量事实已内嵌到主体节点
    const superseded = s.state !== "ACTIVE";
    flowEdges.push({
      id: s.id,
      source: s.source,
      target: s.target,
      type: "default",
      label: superseded ? `${s.predicate}（已取代）` : s.predicate,
      labelStyle: { fontSize: 11, fill: "#8a7f70" },
      labelBgStyle: { fill: "#fffdf8" },
      labelBgPadding: [6, 3],
      labelBgBorderRadius: 6,
      style: {
        stroke: s.origin === "MANUAL" ? "#d97706" : "#c9bfae",
        strokeWidth: superseded ? 1.2 : 1.6,
        strokeDasharray: superseded ? "6 4" : undefined,
      },
    });
  }
  for (const edge of snapshot.edges) {
    if (edge.kind !== "episode_link") continue;
    flowEdges.push({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "default",
      label: edge.role ?? "涉及",
      labelStyle: { fontSize: 11, fill: "#6366f1" },
      labelBgStyle: { fill: "#eef2ff" },
      labelBgPadding: [6, 3],
      labelBgBorderRadius: 6,
      style: { stroke: "#a5b4fc", strokeWidth: 1.4, strokeDasharray: "5 4" },
    });
  }

  return {
    flowNodes,
    flowEdges,
    nodeById: new Map(snapshot.nodes.map((n) => [n.id, n] as const)),
    statementById: new Map(statements.map((s) => [s.id, s] as const)),
    factsByEntity,
    relationsByEntity,
    rolesByEpisode,
    endpointNamesByStatement: endpointNames,
  };
}

/** 空图判定：快照里什么都没有 */
export function isEmptySnapshot(snapshot: MemoryGraphSnapshot | null): boolean {
  if (!snapshot) return true;
  return (
    snapshot.stats.entityNodes === 0 &&
    snapshot.stats.episodeNodes === 0 &&
    snapshot.stats.statementEdges === 0 &&
    snapshot.stats.episodeLinkEdges === 0
  );
}
