import { deleteJson, getBinary, getJson, patchJson, postJson } from "@/lib/http";

/**
 * 记忆图谱 REST 服务（对应后端 app/api/v1/endpoints/memory.py）。
 *
 * 图快照 + 编辑（L1 事实纠错闭环）是记忆系统 v2（server/docs/memory-v2-design.md）
 * 管理侧投影；编辑端点契约见 server/docs/memory-edit-plan.md。注意本域的
 * 字段是 camelCase（后端 graph_snapshot.py 的对外契约），与其余 snake_case
 * 镜像类型（types.ts）不同源。
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

// ---------------- 编辑（L1 事实纠错闭环）----------------

/**
 * 事实写入载荷：补充与纠正共用。
 * - 补充：subjectEntityId/subjectName 二选一；谓词、客体必填；
 * - 纠正：全部字段可选，未提供 = 保持原值（取代链新值）。
 * undefined 字段不会出现在请求体里（JSON.stringify 丢弃）。
 */
export interface StatementWritePayload {
  subjectEntityId?: number;
  subjectName?: string;
  subjectEntityType?: string;
  predicate?: string;
  objectEntityId?: number;
  objectText?: string;
  summary?: string;
  validFrom?: string;
  note?: string;
}

/** 取代式纠正结果：被取代旧行（state 已变 SUPERSEDED）+ 接续新行 */
export interface StatementMutationResult {
  old: MemoryStatementEdge;
  new: MemoryStatementEdge;
}

/** 实体档案直改载荷：未提供字段保持原值；aliases 为全量替换 */
export interface EntityUpdatePayload {
  name?: string;
  aliases?: string[];
  entityType?: string;
}

/** 拆分载荷：新实体定义 + 要迁移过去的陈述/参与/别名选择（图快照 id 形态） */
export interface EntitySplitPayload {
  name: string;
  entityType?: string;
  aliases?: string[];
  statementIds?: string[];
  episodeLinkIds?: string[];
}

export interface EntityMergeResult {
  movedStatements: number;
  movedLinks: number;
  target: MemoryEntityNode;
}

export interface EntitySplitResult {
  entity: MemoryEntityNode;
  movedStatements: number;
  movedLinks: number;
  sourceDeleted: boolean;
}

/** 事件档案直改载荷：未提供字段保持原值；scene 空串=清除场景 */
export interface EpisodeUpdatePayload {
  summary?: string;
  scene?: string;
  occurredAt?: string;
}

/** 参与改挂载荷：未提供字段保持原值；role 空串=清除角色 */
export interface EpisodeLinkUpdatePayload {
  entityId?: number;
  role?: string;
}

/** 范围清除载荷：day 用 from/to（本地 ISO 时刻），thread 用 threadId */
export interface PurgeRequest {
  scope: "day" | "thread";
  from?: string;
  to?: string;
  threadId?: string;
}

export interface PurgePreview {
  statements: number;
  activeStatements: number;
  episodes: number;
  entities: number;
}

export interface PurgeResult {
  archivedStatements: number;
  deletedEpisodes: number;
  deletedEntities: number;
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

  /** 手工补充事实（origin=MANUAL，后端抽取裁决恒不取代人工事实） */
  async addStatement(payload: StatementWritePayload) {
    return postJson<MemoryStatementEdge>("/memory/statements", payload);
  },

  /** 取代式纠正：旧行 SUPERSEDED、新行 ACTIVE 接续，返回前后两行 */
  async correctStatement(statementRef: string, payload: StatementWritePayload) {
    return patchJson<StatementMutationResult>(
      `/memory/statements/${statementRef}`,
      payload,
    );
  },

  /** 归档（软删）：退出当前图谱，时点回放仍可追溯 */
  async archiveStatement(statementRef: string) {
    return deleteJson<MemoryStatementEdge>(`/memory/statements/${statementRef}`);
  },

  /** 实体档案直改：名称/别名/类型（重名撞他实体会收到 3005 业务错误） */
  async updateEntity(entityRef: string, payload: EntityUpdatePayload) {
    return patchJson<MemoryEntityNode>(`/memory/entities/${entityRef}`, payload);
  },

  /** 错分离合并：source（含历史行）全量并入 target 后删除 source */
  async mergeEntity(entityRef: string, payload: { targetRef: string }) {
    return postJson<EntityMergeResult>(`/memory/entities/${entityRef}/merge`, payload);
  },

  /** 错合并拆分：所选事实/参与/别名迁往新实体，双方写拆分禁令 */
  async splitEntity(entityRef: string, payload: EntitySplitPayload) {
    return postJson<EntitySplitResult>(`/memory/entities/${entityRef}/split`, payload);
  },

  /** 孤立实体清理：无任何事实引用与事件参与才可删（用户节点 3003 / 非孤立 3008） */
  async deleteEntity(entityRef: string) {
    return deleteJson<MemoryEntityNode>(`/memory/entities/${entityRef}`);
  },

  /** 事件档案直改：摘要/场景/发生时间（scene 空串=清除） */
  async updateEpisode(episodeRef: string, payload: EpisodeUpdatePayload) {
    return patchJson<MemoryEpisodeNode>(`/memory/episodes/${episodeRef}`, payload);
  },

  /** 物理删除事件及其参与边（不可恢复） */
  async deleteEpisode(episodeRef: string) {
    return deleteJson<MemoryEpisodeNode>(`/memory/episodes/${episodeRef}`);
  },

  /** 参与改挂：换实体/改角色（role 空串=清除；撞唯一组合 → 3007） */
  async updateEpisodeLink(linkRef: string, payload: EpisodeLinkUpdatePayload) {
    return patchJson<MemoryGraphEdge>(`/memory/episode-links/${linkRef}`, payload);
  },

  // ---------------- 危险操作区（L4）----------------

  /** 清除影响面预览：from/to 为本地自然日起止 ISO 时刻（时区由前端决定） */
  async purgePreview(payload: PurgeRequest) {
    return getJson<PurgePreview>("/memory/maintenance/purge-preview", {
      params: payload,
    });
  },

  /** 范围清除：在效事实归档（软删可回放）、事件物理删除、孤立实体清理 */
  async purgeMemory(payload: PurgeRequest) {
    return postJson<PurgeResult>("/memory/maintenance/purge", payload);
  },

  /** 四表全量导出（JSON 备份字节，含已取代/已归档历史行） */
  async exportMemory() {
    return getBinary("/memory/maintenance/export");
  },

  /** 整体重置：清空记忆四表并 drop 记忆向量 collection（confirmation 须为「重置」） */
  async resetMemory(confirmation: string) {
    return postJson<Record<string, number>>("/memory/maintenance/reset", {
      confirmation,
    });
  },
};
