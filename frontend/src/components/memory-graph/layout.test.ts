import { describe, expect, it } from "vitest";

import type {
  MemoryEntityNode,
  MemoryEpisodeNode,
  MemoryGraphEdge,
  MemoryGraphNode,
  MemoryGraphSnapshot,
  MemoryStatementEdge,
} from "@/services/memory-service";
import {
  buildFocusSets,
  buildGraphModel,
  filterSnapshot,
  isEmptySnapshot,
  type MemoryEntityFlowData,
} from "@/components/memory-graph/layout";

// ---------------------------------------------------------------------------
// fixture 构造器：补齐契约必填字段，测试只覆写关注项
// ---------------------------------------------------------------------------

let seq = 0;
const entity = (overrides: Partial<MemoryEntityNode> = {}): MemoryEntityNode => ({
  id: `e:${(seq += 1)}`,
  kind: "entity",
  entityType: "PERSON",
  name: `实体${seq}`,
  aliases: [],
  isUser: false,
  importance: 0.5,
  accessCount: 0,
  lastAccessedAt: null,
  ...overrides,
});

const episode = (overrides: Partial<MemoryEpisodeNode> = {}): MemoryEpisodeNode => ({
  id: `ep:${(seq += 1)}`,
  kind: "episode",
  summary: `事件${seq}`,
  scene: null,
  occurredAt: null,
  accessCount: 0,
  ...overrides,
});

const statement = (
  overrides: Partial<MemoryStatementEdge> = {},
): MemoryStatementEdge => ({
  id: `s:${(seq += 1)}`,
  kind: "statement",
  source: "e:1",
  target: "e:2",
  objectText: null,
  predicate: "喜欢",
  summary: "",
  state: "ACTIVE",
  validFrom: null,
  validTo: null,
  confidence: 1,
  origin: "EXTRACTED",
  sourceThreadId: null,
  sourceTurnId: null,
  ...overrides,
});

const episodeLink = (overrides: { id?: string; source?: string; target?: string; role?: string | null } = {}) => ({
  id: overrides.id ?? `l:${(seq += 1)}`,
  kind: "episode_link" as const,
  source: overrides.source ?? "ep:1",
  target: overrides.target ?? "e:2",
  role: overrides.role ?? null,
});

const makeSnapshot = (
  nodes: MemoryGraphNode[],
  edges: MemoryGraphEdge[],
): MemoryGraphSnapshot => ({
  nodes,
  edges,
  at: null,
  generatedAt: "2026-01-01T00:00:00Z",
  stats: {
    entityNodes: nodes.filter((n) => n.kind === "entity").length,
    episodeNodes: nodes.filter((n) => n.kind === "episode").length,
    statementEdges: edges.filter((e) => e.kind === "statement").length,
    episodeLinkEdges: edges.filter((e) => e.kind === "episode_link").length,
  },
});

// ---------------------------------------------------------------------------
// buildGraphModel：节点分型 / 布局 / 索引 / 边样式
// ---------------------------------------------------------------------------

describe("buildGraphModel（快照 → React Flow 模型 + 查询索引）", () => {
  it("节点按 kind 分型：entity/episode 各归其位", () => {
    const user = entity({ isUser: true, name: "我" });
    const other = entity({ name: "张三" });
    const ep = episode();
    const model = buildGraphModel(makeSnapshot([user, other, ep], []));

    const entityNodes = model.flowNodes.filter((n) => n.type === "entity");
    const episodeNodes = model.flowNodes.filter((n) => n.type === "episode");
    expect(entityNodes).toHaveLength(2);
    expect(episodeNodes).toHaveLength(1);
    expect(episodeNodes[0].id).toBe(ep.id);
  });

  it("用户实体居中（position = 节点尺寸一半取负）", () => {
    const user = entity({ isUser: true });
    const model = buildGraphModel(makeSnapshot([user], []));
    expect(model.flowNodes).toHaveLength(1);
    expect(model.flowNodes[0].position).toEqual({ x: -88, y: -42 });
  });

  it("内环排序门控：isUser 优先 → importance 降序 → name 升序", () => {
    // 同 importance：名字序决定环上位置；不同 importance：重要者排前
    const a = entity({ name: "阿甲", importance: 0.9 });
    const b = entity({ name: "张三", importance: 0.9 });
    const c = entity({ name: "李四", importance: 0.4 });
    const model = buildGraphModel(makeSnapshot([c, b, a], []));

    // 首个非用户实体从 -90°（正上方）起排，顺序 = 排序结果
    const ringEntities = model.flowNodes.filter((n) => n.type === "entity");
    expect(ringEntities[0].id).toBe(a.id);
    expect(ringEntities[1].id).toBe(b.id);
    expect(ringEntities[2].id).toBe(c.id);
    // 第一格在正上方：x ≈ -宽/2（cos(-90°) 浮点误差内 ≈ 0），y ≈ -半径-高/2
    expect(ringEntities[0].position.x).toBeCloseTo(-88, 5);
    expect(ringEntities[0].position.y).toBeCloseTo(-300 - 42, 5);
  });

  it("实体超过 8 个：内环取 6，其余上外环（半径 470）", () => {
    const others = Array.from({ length: 9 }, () => entity());
    const model = buildGraphModel(makeSnapshot([entity({ isUser: true }), ...others], []));

    const ringEntities = model.flowNodes.filter((n) => n.type === "entity");
    const [user, ...ring] = ringEntities;
    expect(user.data.memory.isUser).toBe(true);
    expect(ring).toHaveLength(9);
    // 内环半径 300（第 1 个）、外环半径 470（第 7 个，起始角 -65°）
    const firstInner = ring[0];
    const firstOuter = ring[6];
    expect(Math.hypot(firstInner.position.x + 88, firstInner.position.y + 42)).toBeCloseTo(300, 5);
    // 外环首格 -65°：x = 470·cos(-65°) - 88
    expect(firstOuter.position.x).toBeCloseTo(470 * Math.cos((-65 * Math.PI) / 180) - 88, 5);
  });

  it("事件铺最外环：按 occurredAt 升序；半径随实体数切换（520/640）", () => {
    const fewOthers = [entity(), entity()];
    const manyOthers = Array.from({ length: 9 }, () => entity());
    const ep1 = episode({ occurredAt: "2026-02-01", summary: "晚" });
    const ep2 = episode({ occurredAt: "2026-01-01", summary: "早" });

    const few = buildGraphModel(makeSnapshot([ep1, ep2, ...fewOthers], []));
    const fewEpisodes = few.flowNodes.filter((n) => n.type === "episode");
    expect(fewEpisodes[0].id).toBe(ep2.id); // 早的在前（-90° 起）
    expect(Math.hypot(fewEpisodes[0].position.x + 120, fewEpisodes[0].position.y + 48)).toBeCloseTo(520, 5);

    const many = buildGraphModel(makeSnapshot([ep1, ep2, ...manyOthers], []));
    const manyEpisodes = many.flowNodes.filter((n) => n.type === "episode");
    expect(Math.hypot(manyEpisodes[0].position.x + 120, manyEpisodes[0].position.y + 48)).toBeCloseTo(640, 5);
  });

  it("字面量事实聚合进 factsByEntity 并内嵌节点（截取前 3 条），不产边", () => {
    const user = entity({ isUser: true });
    const facts = ["蓝", "高", "快", "稳", "省"];
    const edges = facts.map((text, i) =>
      statement({ id: `s:f${i}`, source: user.id, target: null, objectText: text, predicate: "属性" }),
    );
    const model = buildGraphModel(makeSnapshot([user], edges));

    expect(model.factsByEntity.get(user.id)).toHaveLength(5);
    expect(model.flowEdges).toHaveLength(0); // 字面量事实不画边
    const userNode = model.flowNodes[0];
    expect(userNode.type).toBe("entity");
    const entityData = userNode.data as MemoryEntityFlowData;
    expect(entityData.facts).toHaveLength(3); // 内嵌上限
    expect(entityData.facts[0]).toMatchObject({ objectText: "蓝", predicate: "属性" });
  });

  it("实体宾语陈述：双向关系（反向谓词加「被」）+ 邻接互挂", () => {
    const alice = entity({ id: "e:a", name: "小明" });
    const bob = entity({ id: "e:b", name: "小红" });
    const s = statement({ id: "s:1", source: "e:a", target: "e:b", predicate: "喜欢" });
    const model = buildGraphModel(makeSnapshot([alice, bob], [s]));

    expect(model.relationsByEntity.get("e:a")).toEqual([
      { edgeId: "s:1", predicate: "喜欢", targetName: "小红", state: "ACTIVE", origin: "EXTRACTED" },
    ]);
    expect(model.relationsByEntity.get("e:b")).toEqual([
      { edgeId: "s:1", predicate: "被喜欢", targetName: "小明", state: "ACTIVE", origin: "EXTRACTED" },
    ]);
    expect(model.neighborsByNode.get("e:a")).toEqual([{ nodeId: "e:b", edgeId: "s:1" }]);
    expect(model.neighborsByNode.get("e:b")).toEqual([{ nodeId: "e:a", edgeId: "s:1" }]);
  });

  it("事件参与：episode_link 聚合 rolesByEpisode 与 linksByEntity，节点带 roles", () => {
    const ep = episode({ id: "ep:1" });
    const alice = entity({ id: "e:a", name: "小明" });
    const link = episodeLink({ id: "l:1", source: "ep:1", target: "e:a", role: "发起人" });
    const model = buildGraphModel(makeSnapshot([alice, ep], [link]));

    expect(model.rolesByEpisode.get("ep:1")).toEqual([
      { linkId: "l:1", entityId: "e:a", entityName: "小明", role: "发起人" },
    ]);
    expect(model.linksByEntity.get("e:a")).toEqual([{ linkId: "l:1", episodeId: "ep:1", role: "发起人" }]);
    const epNode = model.flowNodes.find((n) => n.id === "ep:1");
    expect(epNode?.data.roles).toEqual(model.rolesByEpisode.get("ep:1"));
    // 邻接双向：事件 ↔ 实体
    expect(model.neighborsByNode.get("ep:1")).toEqual([{ nodeId: "e:a", edgeId: "l:1" }]);
    expect(model.neighborsByNode.get("e:a")).toEqual([{ nodeId: "ep:1", edgeId: "l:1" }]);
  });

  it("端点名索引三形态：实体名 / 字面量 / 未知 id 原样回退", () => {
    const alice = entity({ id: "e:a", name: "小明" });
    const edges = [
      statement({ id: "s:1", source: "e:a", target: "e:b", predicate: "喜欢" }), // 宾语实体不在载荷
      statement({ id: "s:2", source: "e:a", target: null, objectText: "蓝色" }),
    ];
    const model = buildGraphModel(makeSnapshot([alice], edges));

    expect(model.endpointNamesByStatement.get("s:1")).toEqual({ source: "小明", target: "e:b" });
    expect(model.endpointNamesByStatement.get("s:2")).toEqual({ source: "小明", target: "蓝色" });
    // 关系行的 targetName 走另一条回退：e:b 按冒号截取尾段
    expect(model.relationsByEntity.get("e:a")?.[0].targetName).toBe("b");
  });

  it("边样式：SUPERSEDED 标注已取代 + 虚线细化；MANUAL 换琥珀色", () => {
    const alice = entity({ id: "e:a" });
    const bob = entity({ id: "e:b" });
    const edges = [
      statement({ id: "s:1", source: "e:a", target: "e:b", state: "SUPERSEDED" }),
      statement({ id: "s:2", source: "e:b", target: "e:a", origin: "MANUAL" }),
    ];
    const model = buildGraphModel(makeSnapshot([alice, bob], edges));

    const superseded = model.flowEdges.find((e) => e.id === "s:1")!;
    expect(superseded.label).toBe("喜欢（已取代）");
    expect(superseded.style?.strokeDasharray).toBe("6 4");
    expect(superseded.style?.strokeWidth).toBe(1.2);

    const manual = model.flowEdges.find((e) => e.id === "s:2")!;
    expect(manual.style?.stroke).toBe("#d97706"); // MANUAL 琥珀色
    expect(manual.label).toBe("喜欢");

    // episode_link：role 缺省回落「涉及」，虚线样式
    const link = episodeLink({ id: "l:1", source: "ep:1", target: "e:a" });
    const withEpisode = buildGraphModel(
      makeSnapshot([alice, episode({ id: "ep:1" })], [link]),
    );
    const linkEdge = withEpisode.flowEdges.find((e) => e.id === "l:1")!;
    expect(linkEdge.label).toBe("涉及");
    expect(linkEdge.style?.strokeDasharray).toBe("5 4");
  });
});

// ---------------------------------------------------------------------------
// filterSnapshot / buildFocusSets / isEmptySnapshot
// ---------------------------------------------------------------------------

describe("filterSnapshot（类型预过滤）", () => {
  const alice = entity({ id: "e:a" });
  const bob = entity({ id: "e:b" });
  const ep = episode({ id: "ep:1" });
  const stmt = statement({ id: "s:1", source: "e:a", target: "e:b" });
  const literal = statement({ id: "s:2", source: "e:a", target: null, objectText: "蓝" });
  const link = episodeLink({ id: "l:1", source: "ep:1", target: "e:a" });
  const baseSnapshot = () => makeSnapshot([alice, bob, ep], [stmt, literal, link]);
  const allOn = { entity: true, episode: true, statement: true };

  it("全开：原样透传", () => {
    const filtered = filterSnapshot(baseSnapshot(), allOn);
    expect(filtered.nodes).toHaveLength(3);
    expect(filtered.edges).toHaveLength(3);
  });

  it("statement 关：陈述边全消（含字面量行）——「事实」开关语义完整", () => {
    const filtered = filterSnapshot(baseSnapshot(), { ...allOn, statement: false });
    expect(filtered.edges.map((e) => e.id)).toEqual(["l:1"]);
    // 由 statement 派生的内嵌事实随之消失
    const model = buildGraphModel(filtered);
    expect(model.factsByEntity.size).toBe(0);
  });

  it("entity 关：悬空陈述与失去实体端的事件参与边一并丢弃", () => {
    const filtered = filterSnapshot(baseSnapshot(), { ...allOn, entity: false });
    expect(filtered.nodes.map((n) => n.id)).toEqual(["ep:1"]);
    expect(filtered.edges).toHaveLength(0);
  });

  it("episode 关：episode_link 需实体+事件双开才保留", () => {
    const filtered = filterSnapshot(baseSnapshot(), { ...allOn, episode: false });
    expect(filtered.nodes.map((n) => n.id)).toEqual(["e:a", "e:b"]);
    expect(filtered.edges.map((e) => e.id)).toEqual(["s:1", "s:2"]);
  });
});

describe("buildFocusSets（聚焦扩散）", () => {
  const alice = entity({ id: "e:a" });
  const bob = entity({ id: "e:b" });
  const carol = entity({ id: "e:c" });
  const model = buildGraphModel(
    makeSnapshot(
      [alice, bob, carol],
      [
        statement({ id: "s:1", source: "e:a", target: "e:b" }),
        statement({ id: "s:2", source: "e:b", target: "e:c" }),
      ],
    ),
  );

  it("depth=1：直接邻居与路径边", () => {
    const { nodeIds, edgeIds } = buildFocusSets(model, "e:a");
    expect([...nodeIds].sort()).toEqual(["e:a", "e:b"]);
    expect([...edgeIds]).toEqual(["s:1"]);
  });

  it("depth=2：二跳扩散覆盖全链", () => {
    const { nodeIds, edgeIds } = buildFocusSets(model, "e:a", 2);
    expect([...nodeIds].sort()).toEqual(["e:a", "e:b", "e:c"]);
    expect([...edgeIds].sort()).toEqual(["s:1", "s:2"]);
  });

  it("孤立/未知节点：只含自身、无边", () => {
    const { nodeIds, edgeIds } = buildFocusSets(model, "e:zzz");
    expect([...nodeIds]).toEqual(["e:zzz"]);
    expect(edgeIds.size).toBe(0);
  });
});

describe("isEmptySnapshot（空图判定）", () => {
  it("null 快照为空", () => {
    expect(isEmptySnapshot(null)).toBe(true);
  });

  it("stats 全零为空（判定只看 stats，不看 nodes/edges 载荷）", () => {
    const snapshot = makeSnapshot([], []);
    expect(isEmptySnapshot(snapshot)).toBe(true);
  });

  it("任一统计非零即非空", () => {
    const snapshot = makeSnapshot([], []);
    snapshot.stats.statementEdges = 1;
    expect(isEmptySnapshot(snapshot)).toBe(false);
  });
});
