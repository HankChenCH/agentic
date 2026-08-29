# 记忆编辑实施计划（memory edit plan）

> 状态：**M1–M6 全部完成（L1–L4 全栈）**（2026-08-29）。
> L1：事实补充/取代式纠正/归档 + 实体改名；L2：合并/拆分/孤立清理 +
> 拆分禁令（消歧优先于余弦）；L3：事件直改/删除/参与改挂（3007 唯一约束）；
> L4：当日清除/按会话遗忘/导出/整体重置（确认文字「重置」、导出先行、
> 影响面预览先行；时区由前端按本机折算）。后端 234 测试全绿；全部链路
> 浏览器/API 实测（重置在 DB_DSN 隔离实例上 E2E 验证，未触碰真实数据）。
> 既知边界：实体被归档行引用时不可物理删除（防悬挂 FK），空卡可见性归
> L5 快照过滤打磨；事件删除是物理删（无状态机），文案已明示不可恢复。
> 上游契约：`docs/memory-v2-design.md`（四表模型、双时间轴、巩固管线、§11 预告）。

记忆图谱目前是只读投影（`GET /memory/graph`）。本计划把记忆纠错从 CLI
（`app/commands/memory.py` 的 repair/rebuild-index）扩展为产品化编辑能力，
覆盖前后端。底层大部分机制已预留：`supersede_statement` 取代链闭环、
`MANUAL` 全链路保护就位但零写入方、`ARCHIVED` 枚举零使用、
`MemoryVectorIndex.delete()` 已实现未调用。

## 0. 两条语义铁律（所有场景的实现骨架）

1. **内容纠错 = 续记（取代链）**：事实的值/时间/指向错了 → 旧行永不改写，
   置 `SUPERSEDED` 并补 `valid_to` + `invalidated_at`，新行 ACTIVE 接续
   （origin=MANUAL）。时点回放永远成立。
2. **身份纠错 = 追改（重新归属）**：实体合并/拆分判错了 → 陈述/参与/别名
   改挂到正确实体并重写受影响 summary，语义是「本来就属于那里」，允许追溯。
   需处理全状态行（含 SUPERSEDED，防悬挂 FK）+ 定向向量重写。

## 1. 场景全景（已确认，18 项）

| # | 纠错场景 | 操作语义 | 级 |
| --- | --- | --- | --- |
| 1 | 事实值错 | 续记取代链 | L1 |
| 2 | 事实指向错（单条改挂） | 续记：编辑客体/主体字段 | L1 |
| 3 | 事实时间错 | 续记：编辑 valid_from/time_remark | L1 |
| 4 | 多余/重复事实 | 归档 ARCHIVED | L1 |
| 5 | 缺失事实 | 手工补充 origin=MANUAL | L1 |
| 6 | 实体名/别名/类型错 | 追改直改 | L1 |
| 7 | 实体错合并 → 拆分 | 追改：勾选内容改挂新实体 | L2 |
| 8 | 实体错分离 → 合并 | 追改：全量改挂存留方，旧壳删除 | L2 |
| 9 | 孤立/残渣实体 | 删除（无引用守卫） | L2 |
| 10 | 事件摘要/场景/时间错 | 直改 + 向量重写 | L3 |
| 11 | 多余/重复事件 | 物理删除（含参与+向量） | L3 |
| 12 | 事件参与挂错 | 改挂单条 episode_link | L3 |
| 13 | 当日清除 | 批量归档+删除 | L4 |
| 14 | 按会话遗忘 | 同上，按 source_thread_id | L4 |
| 15 | 整体重置 | 物理清空 + 向量 drop，可选导出 | L4 |
| 16 | 可疑信号提示（污染别名标黄等） | 打磨 | L5 |
| 17 | 编辑历史审计（身份追改操作日志） | 打磨；事实层时点回放已覆盖 | L5 |
| 18 | 防再错（MANUAL 禁改 ✅ / merge_blocklist） | blocklist 随 L2 落地 | L2 |

## 2. 跨切面约定（各级共用）

- **分层与依赖**：领域服务 `services/domain/memory/admin_service.py`
  （`MemoryAdminService`，`@injectable @dataclass`）做业务规则与校验；用例级
  事务由端口实现承担。domain 不可 import components → 新端口
  `MemoryEditor`（Protocol）放 `services/domain/memory/ports.py`，实现
  `components/memory/editor.py`（`@injectable(as_type=MemoryEditor)`，
  仿 `graph_reader.py` 的回填模式），内部组合 `MemoryRepository` +
  `MemoryVectorIndex`（components→domain 合法边）。wireup 自动扫描，无需手工注册。
- **端口方法 = 用例级粗粒度事务**（一次 Session 内完成多行写，如
  「supersede 旧行 + 插入新行 + 向量同步清单返回」），不是细粒度 repo 调用。
  向量写在 DB 提交后 best-effort 执行，失败不回滚（SQL 是事实源，
  `rebuild-index` CLI 可兜底）。
- **端点**：扩 `app/api/v1/endpoints/memory.py`（prefix 已是 `/memory`），
  端点零业务逻辑，一律 `return Response.success(...).to_dict()`。请求模型
  新建 `app/models/schema/request/memory.py`（仿 knowledge.py 的部分更新
  语义：未提供字段=None 保持原值）。**字段 camelCase**（与 graph 契约同域一致）。
- **错误码**：扩 `app/exceptions/memory.py`（3xxx 段，现有 3000/3001）：
  `3002` 目标对象不存在、`3003` 受保护对象不可操作（用户节点等）、
  `3005` 名称/目标冲突（拆分重名、link 唯一约束等）、`3006` 未选择任何
  分离内容、`3007` 约束冲突、`3008` 参数非法。全局映射已由
  `api/exception_handlers.py` 的 BusinessError 处理器覆盖，只需继承定义。
- **summary 组装**：客体侧改挂/新建事实的 summary 必须复用抽取侧同一
  规范句式（`components/memory/renderer.py` 的 `_fact_summary`，注意
  单值谓词「的/是」与多值谓词的差异）——放在端口实现（components 侧）完成。
- **前端刷新模式**：任何变更成功后 `useMemoryGraph.refresh(silent)` 静默
  重拉快照 + sonner toast；被取代的陈述 id 已消失 → 自动关闭对应详情面板；
  实体/事件面板数据可能过期，v1 接受（下次点击刷新）。
- **测试**：纯单元级（SQLite 临时库 + `tests/fakes_memory.py` 的
  FakeMemoryVectorIndex/CannedLLM），每级补对应测试文件；
  `tests/test_layer_boundaries.py` 必须保持通过。
- **文档同步**：每级落地后更新 `README.md` API 清单、两份 AGENTS.md 的
  endpoint 摘要行、本文件状态勾选。无表结构变更（事件删除走物理删，
  不加列；`create_all` 不会迁移已有表，避免 schema 变更）。

## 3. L1 · 事实纠错闭环 + 实体改名（场景 #1–6）

### 后端

端口 `MemoryEditor` 方法（用例级）：

- `add_statement(subject: SubjectRef, predicate, object: ObjectRef, summary?, valid_from?, note?) -> StatementPair`
  - `SubjectRef = {entity_id} | {name, entity_type}`（不存在则建，MANUAL）
  - `ObjectRef = {entity_id} | {object_text}` 二选一，校验 XOR
  - 新行 `origin=MANUAL`、`confidence=1.0`、`evidence=[{"quote": note}]`（note 可空）
- `correct_statement(statement_id, 同上可变字段) -> {old, new}`
  - 旧行任意 origin 均可被人工纠正（MANUAL 禁改只约束抽取裁决，不约束用户）
  - 无变化（新值与旧值等价）→ `3008` 提示无需纠正
- `archive_statement(statement_id) -> old`：置 `ARCHIVED` + `valid_to=now` +
  `invalidated_at=now`；向量删该陈述条目（`delete()` 首个真实调用方）
- `update_entity(entity_id, name?, aliases?, entity_type?) -> entity`：
  - 改名/新别名不得与**其他**实体的名称或别名冲突（`3005`）
  - `is_user` 实体可改名（锚点保留），不可改 `is_user`

端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/memory/statements` | 手工补充事实 |
| PATCH | `/memory/statements/{id}` | 取代式纠正（docstring 写明语义） |
| DELETE | `/memory/statements/{id}` | 归档（软删） |
| PATCH | `/memory/entities/{id}` | 名称/别名/类型 |

### 前端

- `services/memory-service.ts`：补四个方法与请求/响应类型（camelCase）。
- `components/memory-graph/`：新增 `edit-dialogs.tsx`
  （事实纠正/补充表单：谓词、客体切换实体引用/字面量、摘要、生效时间；
  实体编辑表单：名称/别名/类型）。
- `detail-panel.tsx`：事实档案加「纠正」「归档」（归档走共享
  `confirm-dialog`，文案注明「时点回放仍可追溯」）；实体档案加「编辑」与
  「补充事实」（以该实体为预填主体）。
- 纠正成功 toast：「已纠正：{谓词} {旧值} → {新值}，历史可在时点回放查看」。

### 验收

- 单测：取代链字段正确性（旧行 valid_to/invalidated_at、新行 MANUAL ACTIVE）、
  归档后向量删除、补充事实的 SubjectRef 建实体路径、重名 3005、XOR 校验
  3008、客体侧 summary 规范句式。
- 手测：图谱上纠正一条事实 → 旧边消失、新边橙色（人工维护）、时点回放切
  到昨日可见旧值虚线；归档一条 → 图上消失；随后聊天提及冲突事实 → 抽取
  裁决放弃（MANUAL 保护回归，`test_memory_adjudication` 扩用例）。

## 4. L2 · 实体身份纠错（#7 拆分 / #8 合并 / #9 孤立清理 / #18 blocklist）

### 后端

端口新增（全部单事务 + 定向向量重写，机制对齐 `app/commands/memory.py` repair
六步的泛化）：

- `merge_entity(source_id, target_id) -> {moved_counts}`
  - 校验：存在（3002）、source≠target（3008）、source 为用户节点（3003，
    用户节点只能作存留方）
  - A 的**全部状态**陈述（subject/object 两侧）与 episode_link 改挂 B；
    客体侧 summary 重写为 `{主体}{谓词}{B名}`
  - B.aliases ∪= {A.name} ∪ A.aliases（去重、去等于 B 名的项）
  - 清除双向 `attributes.merge_blocklist` 中的 A/B 记录（人工合并意图覆盖
    旧拆分禁令）
  - 删除 A 行；向量：删 KIND_ENTITY(A)、upsert KIND_ENTITY(B)（别名变了）、
    upsert 被改挂陈述
- `split_entity(source_id, {name, entity_type, aliases?, statement_ids, episode_link_ids}) -> {new_entity, moved_counts}`
  - 校验：至少选一项（3006）；新名不与任何现存实体名/别名冲突（3005，
    拆成已存在的名字等于变相合并）
  - 建新实体（MANUAL）→ 改挂所选（客体侧 summary 重写）→ 迁移所选别名
  - 拆空的 source（任意状态陈述引用=0 且参与=0 且别名=0）自动删除 + 向量删
  - **blocklist 互写**：双方 `attributes.merge_blocklist` 追加对方
    name+aliases，`_resolve_entities` 在余弦判定前查黑名单命中即拒
    （消歧改动现居 components/memory/resolution.py）
- `delete_orphan_entity(entity_id)`：任意状态陈述引用=0 且参与=0 才可删
  （3003/3005 区分报错）；is_user 拒绝；向量删

端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/memory/entities/{id}/merge` | body `{targetId}` |
| POST | `/memory/entities/{id}/split` | body `{name, entityType, aliases?, statementIds, episodeLinkIds}` |
| DELETE | `/memory/entities/{id}` | 仅孤立实体 |

### 前端

- 实体档案面板加：「合并到…」（目标实体搜索下拉，来自当前快照）、「拆分
  实体」（向导：① 勾选要分离的陈述[主体+客体侧]/参与/别名——数据从
  `model.relationsByEntity`/`factsByEntity`/episode_link 边就地聚合，不发新
  请求；② 新实体名/类型；③ 计数预览确认，含「原实体将自动删除」提示）、「
  删除」（非孤立时禁用 + tooltip 原因）。
- 合并确认文案固定含「含历史版本」字样（快照只有 ACTIVE，预览计数为现存
  事实，实际迁移含 SUPERSEDED 行）。

### 验收

- 单测：合并后 FK 零悬挂（含 SUPERSEDED 行改挂）、blocklist 清理、拆分
  空壳删除判定（三条件）、拆分重名 3005、用户节点守卫、
  FakeMemoryVectorIndex 断言删/写清单；`_resolve_entities` 黑名单命中拒绝
  合并的新用例。
- 手测：复现事故形态（把学校事实手工挂到公司实体）→ 拆分向导拉开 →
  再聊学校相关话题不再被合并（blocklist 生效）→ 误拆后合并归一。

## 5. L3 · 事件层编辑（#10–12）

### 后端

- `update_episode(episode_id, summary?, scene?, occurred_at?)`：直改 +
  向量 upsert（KIND_EPISODE，content=summary）
- `delete_episode(episode_id)`：删参与 → 删行 → 向量删
- `update_episode_link(link_id, entity_id?, role?)`：直改；撞
  `(episode, entity, role)` 唯一约束 → `3007`

端点：`PATCH /memory/episodes/{id}`、`DELETE /memory/episodes/{id}`、
`PATCH /memory/episode-links/{id}`。

### 前端

事件档案面板加「编辑」「删除」（删除文案注明「从所有时间视图移除，不可
恢复」——与事实归档的可逆性差异必须区分）；参与角色行内「编辑」小弹窗
（实体下拉 + 角色输入）。

### 验收

单测三方法 + 唯一约束冲突；手测编辑事件摘要后图上卡片与向量召回同步
（`timeline` 工具可验）。

## 6. L4 · 危险操作区（#13–15）

### 后端

- `GET /memory/maintenance/purge-preview?scope=day|thread&from=&to=|threadId=`
  → `{statements, episodes, entities}` 计数（实体=将被清理的当日/该会话
  孤立实体）。**时区决定：前端传本地自然日起止 ISO 时刻**（from/to），
  服务端不做时区假设。
- `POST /memory/maintenance/purge` body `{scope, from?, to?, threadId?}`：
  事实（任意状态）→ ARCHIVED（软删保回放）；事件 → 物理删（含参与）；
  随后清理变孤立的实体；向量逐类删除。
- `GET /memory/maintenance/export` → JSON 备份文件下载
  （Content-Disposition；四表全量）。
- `POST /memory/maintenance/reset` body `{confirmation, }`：
  `confirmation !== "重置"` → `3008`；成功 = 四表清空 + 向量 collection
  重建为空（drop+空写，仿 rebuild-index）。导出由前端在 reset 前调
  export 接口完成，服务端不耦合。

端点四个，均挂 `/memory/maintenance/*`。

### 前端

顶栏「记忆管理」下拉（扫帚/警示图标）三项：当日清除（日期默认今天 →
预览弹窗展示计数 → confirm-dialog）、按会话遗忘（会话下拉，数据来自
`use-conversation-list`）、整体重置（红色危险弹窗 + 输入「重置」两字
解锁 + 「导出 JSON 备份」勾选项，勾选则先触发下载再执行）。

### 验收

单测：purge 日界（含头含尾）、reset 确认串校验、export 形状、purge 后
孤立实体清理；手测全流程 + 重置后图谱空态卡与对话不再召回旧记忆。

## 7. 排期与里程碑

| 里程碑 | 内容 | 依赖 |
| --- | --- | --- |
| M1 | L1 后端（端口/服务/仓储/端点/单测） | 无 |
| M2 | L1 前端（service/弹窗/面板按钮/刷新） | M1 |
| M3 | L2 后端（合并/拆分/孤立/blocklist） | M1 |
| M4 | L2 前端（合并下拉/拆分向导/删除守卫） | M3 |
| M5 | L3 全栈 | M1 |
| M6 | L4 全栈 | M1（purge 复用归档） |
| M7 | 文档同步收尾（README/AGENTS×2/本文件勾选） | 每级随做 |

## 8. 风险与既知取舍

- **事件删除是物理删**（episode 无 state 字段，加列需迁移，`create_all`
  不迁移已有表）→ 事件从历史回放视图消失，事实层不受影响；文案必须明示。
- **快照 limit 截断**（前端取 300 条）下，拆分向导的勾选清单可能不全 →
  极端数据量下以「按会话遗忘/CLI 兜底」，界面提示「仅列出当前快照内容」。
- **身份追改无历史**：合并/拆分不进时点回放（回放的是事实内容时间线）；
  如需审计走 L5 操作日志。
- **MANUAL 语义边界**：人工事实不随对话自动演化（抽取禁 REPLACE MANUAL，
  冲突事实放弃）——编辑弹窗与文档明示；将来若放开改裁决 prompt 一处。
- **空实体节点可见性**：只剩历史引用的实体会在当前快照里渲染为孤立空卡
  （低频），如困扰可在快照服务过滤「无 ACTIVE 事实且无参与且非用户」的
  实体，属 L5 打磨。
