# AGENTS.md — `server/`

Guide for the Python backend of the `agentic` monorepo. Workspace overview and
cross-cutting contract: [`../AGENTS.md`](../AGENTS.md). Frontend guide:
[`../client/agentic-client/AGENTS.md`](../client/agentic-client/AGENTS.md).

Python 3.12 backend (**FastAPI + Celery + LangChain/LangGraph + ag-ui**) serving
`POST /agentic/run` as an SSE stream of ag-ui events to the React client.

## Layout

```
server/                          # this directory is its own git repo (the workspace root is not)
├── pyproject.toml               # uv-managed deps (langchain, langchain-deepseek, wireup, ag-ui-protocol, celery[redis], langchain-weaviate, prometheus-client)
├── alembic.ini                  # Alembic 配置：script_location 用 %(here)s 锚定（不依赖 CWD）；
│                                #   sqlalchemy.url 留空——URL 由 migrations/env.py 从应用配置链解析
├── migrations/                  # Alembic 迁移：env.py（import app.models.domain 收集 SQLModel.metadata 作
│                                #   target_metadata；URL 走 AppConfig → DatabaseFactory → engine.url，不手工拼）
│                                #   + versions/ 迁移脚本（初始基线 = 11 张表全量建表）
├── docker-compose.yaml          # Middleware stack: PostgreSQL + Weaviate + RustFS + Redis (local dev)
├── .python-version              # 3.12
├── .env                         # 环境变量：APP_ENV、SQLITE_DB_PATH、WEAVIATE_*、DEEPSEEK_API_KEY、OPENAI_API_KEY（占位，OpenAI 兼容网关）、MINERU_API_KEY
├── .gitignore
└── app/
    ├── cmd/                     # Entrypoints — one subdir per launchable app
    │   ├── http/                # main.py (create_app + server, uvicorn import string app.cmd.http.main:server;
    │   │                        #   CORS 白名单/请求体上限在装配期读 http.yaml 并接线中间件；限流为
    │   │                        #   fastapi-limiter 依赖按端点挂载，见 api/rate_limit.py)
    │   │                        #   + __main__.py (Typer → uvicorn.run，含 --host/--port/--reload)
    │   ├── task_executor/       # Celery app: main.py (sync wireup container + wireup.integration.celery.setup),
    │   │                        #   __main__.py (Typer，未知参数透传 → worker_main); tasks live in app/tasks/
    │   └── admin/                 # 维护管理命令行（Typer 复合入口）：python -m app.cmd.admin <域> <命令>；命令实现在 app/commands/
├── tasks/                   # Celery task layer: one module per task domain; __init__.py auto-imports
│                            #   every non-underscore module so tasks self-register (include=["app.tasks"])
├── commands/                # admin 命令行的领域命令层，一域一模块、各自暴露 typer.Typer，由 cmd/admin 入口
│                            #   add_typer 挂载：memory 域 repair（实体错合并存量修复，幂等，默认 dry-run，
│                            #   --apply 落库）+ rebuild-index（全量重建记忆向量索引，默认仅统计，--yes 真执行）
│                            #   + db 域：Alembic 迁移薄封装（upgrade/downgrade/revision/current/history/stamp）
├── api/                     # exception_handlers.py (global AOP handlers); middleware.py (纯 ASGI 边缘中间件，SSE
│                              #   友好：RequestIDMiddleware——X-Request-ID 生成/透传 + loguru 上下文注入，
│                              #   SSE 线程池日志同携带；BodySizeLimitMiddleware——请求体
│                              #   字节上限，Content-Length 超限直回 413，实收累计超限（分块/谎报长度）由
│                              #   receive 包装抛 HTTPException(413) 经全局 http_exception_handler 收信封；
│                              #   作用域与数值来自 http.yaml，接线在 cmd/http/main.py);
│                              #   cmd/task_executor/metrics.py -> Celery worker 指标（任务计数/耗时/
│                              #   在途 Gauge,multiprocess 模式,独立端口暴露;装配延迟到 worker_init——
│                              #   prometheus_client 的 values.ValueClass 在其模块导入期决定单/多进程
│                              #   实现,env 须先于该导入;且本模块被 app.tasks 导入链带入 HTTP 进程,
│                              #   导入必须无副作用）;
│                              #   health.py -> GET /health 就绪探针（db SELECT 1 + redis PING 逐项探测，
│                              #   全绿 200 / 任一 down 503 信封 + 组件明细；探测异常只记日志不穿透）
│                              #   + metrics.py -> GET /metrics Prometheus 采集端点（纯函数路由,不鉴权,
│                              #   设 PROMETHEUS_MULTIPROC_DIR 时走 MultiProcessCollector）+ MetricsMiddleware
│                              #   （纯 ASGI:请求计数/时延直方图/在途 Gauge,handler=路由模板,404 落 unmatched）; v1/endpoints/ — agentic.py -> POST /agentic/run (StreamingResponse)
│                              #   + POST /agentic/run/cancel 显式取消（fire thread 作用域取消信号，幂等）
│                              #   + GET/DELETE /agentic/conversation[/...] 会话列表/详情/历史/删除;
│                              #   deps.py -> require_user（JWT Bearer 无状态验签依赖，UserPrincipal 注入）;
│                              #   auth.py -> POST /auth/register|login（注册即登录，签发 JWT）+ GET /auth/me;
│                              #   认证挂载在 cmd/http/main.py 的 include_router 处按 router 声明
│                              #   （agentic/knowledge/agent_knowledge/memory 组，/auth、/health 公开）；
│                              knowledge.py -> /knowledge 管理侧 CRUD（multipart 上传——端点透传 UploadFile
│                              #   底层流，服务层流式转存对象存储边计数/摘要，不整读入内存，文件落 rustfs）;
│                              agent_knowledge.py -> /agent/{agent_id}/knowledge 绑定管理（GET/PUT 全量替换）;
│                              memory.py -> /memory/graph 记忆图快照（at 参数做时点回放，
│                              服务为 domain/memory/graph_snapshot.py 经 ports.MemoryGraphReader 端口）
│                              + POST/PATCH/DELETE /memory/statements、PATCH/POST/DELETE
│                              /memory/entities/{ref}[/merge|/split]、PATCH/DELETE
│                              /memory/episodes/{ref}、PATCH /memory/episode-links/{ref}、
│                              /memory/maintenance/{purge-preview,purge,export,reset}
│                              记忆编辑 L1–L4（取代式纠正/归档/手工补充/实体改名 +
│                              合并/拆分/孤立清理 + 事件直改/删除/参与改挂 + 当日清除/
│                              会话遗忘/导出/整体重置；服务为
│                              domain/memory/admin_service.py 经 ports.MemoryEditor 端口，
│                              实现在 components/memory/editor.py——用例级事务+定向向量同步；
│                              身份纠错的归属改写走仓储复合事务 absorb/split_entity，
│                              拆分双方互写 attributes.merge_blocklist，消歧遇禁令对优先于余弦）
├── core/container.py        # Shared wireup injectables + build_async_container()/build_sync_container()
    ├── core/config/             # AppConfig (@injectable pydantic model): llm.py (LLMConfig + LLMProviderEntry), db.py, vector_db.py, filesystem.py,
    │                            #   memory.py, redis.py (RedisConfig + RedisProviderEntry), task.py, logging.py, http.py (HttpConfig——CORS
    │                            #   白名单/请求体上限), auth.py, document_parser.py, metrics.py; get_environment()
    │                            #   (loader.py 负责 YAML 加载/环境变量插值)
    ├── core/exceptions/         # Framework baseline exceptions: AgenticError root → framework.py (FrameworkError, ConfigError,
    │                            #   InfrastructureError) + business.py (BusinessError base — the handlers' anchor); concrete
    │                            #   business exceptions are user-defined in app/exceptions/
    ├── core/logging/            # Logging infra package: base.py (AppLogger ABC, stdlib-compatible facade), loguru_backend.py
    │                            #   (LoguruAppLogger + InterceptHandler bridging stdlib "app" loggers), setup.py (setup_logging()
    │                            #   assembles sinks from logging.yaml), service.py (LoggerFactory @injectable singleton)
    ├── exceptions/              # Business exceptions, user-defined per domain (subclass core's BusinessError):
    │                            #   conversation 1xxx / agent 2xxx / memory 3xxx / knowledge 4xxx
├── agents/                  # BaseAgent + @register_agent registry, AgentFactory, builtin agents
│                            #   (builtin/{demo,rag}/ 一 agent 一包；rag 为自建 StateGraph，
│                            #   见 Gotchas「builtin:rag」)
├── components/              # 自内聚能力组件，统一范式（解剖学详见下方「components」条目）：
│                            #   base.py = 范式核心（ToolSpec/ComponentSpec/COMPONENT_REGISTRY/
│                            #   register_component/describe_capabilities）+ memory/ + knowledge/ + demo/。
│                            #   组件解剖学：__init__.py（公共面再导出）+ manifest.py（能力声明与
│                            #   工具构造，必有）+ ability/（能力模块，按能力命名持门面服务，必有）+
│                            #   internal/（跨能力共享机件，可选，app 层禁入）+ admin.py（管理面端口
│                            #   实现，可选）+ repositories/（自有存储，可选）。
│                            #   memory: ability/{recall,consolidation,user_node} + internal/{extraction,resolution,
│                            #   renderer,scoring,vocab} + admin.py（MemoryEditor/MemoryGraphReader 端口
│                            #   回填）+ repositories/ 图谱实现；knowledge: ability/{retrieval,
│                            #   navigation}（检索 + 定位读取——邻域窗口/范围读文/文档清单，
│                            #   守卫口径与检索一致：可见性 + KB/文档 enabled）+ manifest 契约模型
│                            #   KnowledgeSearchResult 与 KnowledgeSegmentWindow（LLM/前端共用 JSON 形状）；
│                            #   demo: ability/weather（演示工具 get_weather，mock 数据源收敛在
│                            #   门面内部，未来替换实现工具层不动）。
│                            #   能力（v1 仅 tools）经 manifest 静态登记注册表，装配器绑定服务产出
│                            #   StructuredTool；唯一显式组件清单点是 agents/toolbox.py。
│                            #   components may import domain/repositories/infra (单向)，严禁
│                            #   app.services.orchestration / app.agents / app.api
├── packages/                # 可复用能力库层：契约+后端内聚一包，领域无关，禁向上依赖
│                            #   services/components/agents/api/tasks/repositories/models
│                            #   （AST 强制）——居民 signal/：SignalStore 契约（key 寻址的
│                            #   分布式 Event：fire/is_fired/reset/wait/get，wait 轮询默认
│                            #   可被后端覆写；ttl 必选）+ RedisSignalStore（默认绑定）+
│                            #   InMemorySignalStore（测试/单机）；详见「packages」条目
├── services/                # 两层制（依赖箭头表见下「服务层两层制」）—— orchestration/: 用户侧行程
│                            #   （AgenticService run 编排 + translator/ 双翻译器 + TurnFinalizer 收尾）；
│                            #   domain/: 领域服务，按聚合分包—— conversation/（signals.py
│                            #   会话域信号 key/常量 + title_generator 裸模型标题生成）、
│                            #   knowledge/（KB/document/ingestion/binding 服务 +
│                            #   object_store/support 纯函数/document_chunker）与 memory/
│                            #   （记忆向量适配器 MemoryVectorIndex+collection 显式 schema）
├── models/
│   ├── schema/request/run.py     # RunRequest / RunMessage (camelCase fields for ag-ui)
│   ├── domain/agentic/      # SQLModel tables: conversation / turn / message（conversation 带 user_id FK→users.id，
│   │                        #   归属过滤在仓储查询条件内强制）
│   ├── domain/user/         # SQLModel table: users（username 唯一、PBKDF2 password_hash；DEFAULT_USER_ID 为
│   │                        #   迁移回填存量数据的不可登录占位用户）
│   ├── domain/knowledge/    # SQLModel tables: knowledge_base / knowledge_base_document(+segment) /
│                             #   knowledge_agent_binding (agent↔KB); KnowledgeStatus enum; segment.id doubles
│                             #   as the Weaviate object UUID; knowledge_base 带 user_id（FK→users，属主）+
│                             #   is_public（公开/私有标识，缺省私有），name 唯一性为每用户复合唯一 (user_id, name)
│   └── domain/memory/       # 记忆 v2 双层图谱四表: entity / statement(双时间轴·SUPERSEDED 不删除可回放)
│                             #   / episode / episode_link —— 设计定稿见 docs/memory-v2-design.md；
│                             #   entity/statement/episode 各带 user_id（用户级作用域，episode_link 经
│                             #   episode 继承）；向量侧每用户一 collection Memory_{uid.hex}
├── repositories/            # Data-access (@injectable) — ConversationRepository,
│                             #   UserRepository, KnowledgeBaseRepository, KnowledgeDocumentRepository (docs+segments),
│                             #   KnowledgeBindingRepository
└── infrastructures/         # Shared drivers: llm/ (ModelFactory + ModelBuilder registry), db/ (DatabaseFactory +
                                 #   DatabaseBuilder registry — sqlite / postgresql via psycopg3, create_default_db
                                 #   singleton Engine), document_parser/ (DocumentParserFactory + DocumentParserBuilder
                                 #   registry — mineru_cloud (MinerU 云端 PDF 解析，parse(bytes, filename) -> ParsedDocument
                                 #   + ParsedBlock), create_default_document_parser singleton), vector/ (VectorStoreFactory + VectorDBBuilder registry —
                                 #   Weaviate via langchain-weaviate; build_client/build + drop for collection
                                 #   deletion), filesystem/ (FilesystemFactory + FilesystemBuilder registry —
                                 #   local pathlib / S3 via obstore), redis/ (RedisClientFactory +
                                 #   RedisClientBuilder registry — standalone via REDIS_URL)
```

> **命名对照**：`cmd/` 借自 Go 布局惯例（Python 官方无目录级入口规范，入口机制是
> `[project.scripts]` + `__main__.py`/`python -m`；此为有意识的借用）。每个入口的
> "命令体"按产物类型分包——`cmd/http` → `api/`（HTTP 端点体）、`cmd/task_executor` →
> `tasks/`（Celery 任务体）、`cmd/admin` → `commands/`（CLI 命令体；内部分层参照
> pip `_internal/{cli,commands}` 与 poetry `console/commands` 的先例）。

## Commands

All commands run with CWD = `server/` (this directory).

**`uv` is not on the non-interactive shell's PATH** (installed at
`~/.local/bin/uv`; the PATH export lives in `~/.zshrc`, which agent shells
don't source). Run `export PATH="$HOME/.local/bin:$PATH"` first, or use
`~/.local/bin/uv` by absolute path.

### App

- Install/sync deps: `uv sync`
- Run dev server: `uv run uvicorn app.cmd.http.main:server --reload` → uvicorn on
  `0.0.0.0:8000`. Alternatively `uv run python -m app.cmd.http [--reload]
  [--host H] [--port P] [--config-dir PATH] [--env-file PATH]` (Typer
  entrypoint in `cmd/http/__main__.py`; config flags are bridged to
  `AGENTIC_CONFIG_DIR`/`AGENTIC_ENV_FILE` env vars).
- Run task executor (Celery worker; needs `docker compose up -d redis` first):
  `uv run celery -A app.cmd.task_executor.main worker` or
  `uv run python -m app.cmd.task_executor [--pool=solo]` (extra args pass
  through to the celery worker command). Broker/backend URLs are resolved at
  startup from `app/configs/task.yaml`, whose `broker`/`backend` each hold a
  driver+key reference (`driver: redis` + `provider` naming a `redis.yaml`
  providers entry; resolved by `app.core.config.resolve_url`, fail-fast on
  unknown key/driver — defaults both point at the `redis` entry, db0).
- Maintenance CLI (Typer): `uv run python -m app.cmd.admin memory repair`
  (dry-run; `--apply` to write) and `uv run python -m app.cmd.admin memory
  rebuild-index` (stats only; `--yes` to drop + re-embed). Top-level
  `--config-dir`/`--env-file` bridge to `AGENTIC_CONFIG_DIR`/`AGENTIC_ENV_FILE`
  (same convention as the http entrypoint).
- Database migrations (Alembic): `uv run python -m app.cmd.admin db upgrade`
  (creates tables on a fresh DB and applies增量; the http app does **not**
  create tables at startup). Subcommands: `downgrade <rev>`, `revision -m
  "..." [--autogenerate]`, `current`, `history`, `stamp [head]` — thin
  wrappers over `alembic.command`, URL resolved by `migrations/env.py` from
  the app config chain (`SQLITE_DB_PATH`/`AGENTIC_CONFIG_DIR`/`AGENTIC_ENV_FILE` all
  apply). Workflow: change `models/domain` → `db revision --autogenerate` →
  review the script → `db upgrade`. A pre-Alembic DB (created by the old
  startup `create_all`) is adopted once via `db stamp head`. Direct
  `uv run alembic <cmd>` from `server/` also works (same `alembic.ini`).
- Run unit tests: `uv run pytest tests/` (pure unit level — SQLite 临时库 +
  stub 向量索引，不碰 Weaviate/MinerU/Ollama). CI 里需为 `llm.yaml` 的必填
  占位符（`DEEPSEEK_API_KEY` 等）提供哑值，见 `.github/workflows/ci.yml`。
- Lint: `uv run ruff check .`（dev 依赖；规则集显式固定为 E4/E7/E9/F，见
  pyproject `[tool.ruff.lint]`，isort/pyupgrade 等风格规则未启用）。CI 同款：
  `.github/workflows/ci.yml`（lint + pytest 两个 job）。
- **Entrypoint modules import `from app...`, so run them as modules
  (`python -m app.cmd.http`), never as scripts from a different CWD.**

### Middleware

- Start: `docker compose up -d` — Stop: `docker compose down` — Wipe data:
  `docker compose down -v`
- Services (all bound to `127.0.0.1` only; dev creds overridable via env vars,
  see the file header):
  - **postgres** (`postgres:18-alpine`) → `127.0.0.1:5432`, user/pass/db
    `agentic`/`agentic`/`agentic`
- **weaviate** (`semitechnologies/weaviate:1.39.0`) → `127.0.0.1:8080` (HTTP,
  matches the reserved `app/configs/vector_db.yaml`) + `127.0.0.1:50052`
  (gRPC; 宿主侧刻意错开 50051——本机 Ollama 桌面版的 ui server 占用
  `127.0.0.1:50051` 且 loopback 精确绑定优先于 Docker 通配绑定，会截走
  gRPC 流量；`.env` 的 `WEAVIATE_GRPC_PORT=50052` 与之保持一致), anonymous access, `DEFAULT_VECTORIZER_MODULE=none` (vectors come
  from the app's embedding models), `ENABLE_TOKENIZER_GSE=true` (gse 中文
  分词——knowledge collection 的 content 属性按 gse 建倒排，BM25/混合检索
  的前提；不开则建 collection 时 422)
  - **rustfs** (`rustfs/rustfs:latest`, S3-compatible) → `127.0.0.1:9000` (S3
    API) + `127.0.0.1:9001` (web console port reserved; rustfs
    1.0.0-beta.12's console doesn't start — the S3 API answers on 9001 too),
    access/secret `agentic`/`agentic-secret`; the one-shot `rustfs-init`
    service (minio/mc) idempotently creates the default bucket `agentic`
    (`RUSTFS_BUCKET` overridable)
  - **redis** (`redis:8-alpine`) → `127.0.0.1:6379` — **in use** as the Celery
    broker/result backend by `app/cmd/task_executor` (task.yaml's broker/
    backend reference the `redis.yaml` `redis` entry by driver+key) and by
    the signal store's Redis backend (`app/configs/redis.yaml`, same entry by
    default, keys prefixed `agentic:signal:`)
- Redis is used by the task executor. The app's default db is still SQLite
  (`db.yaml` `default: sqlite`); the matching `postgres` entry is wired through
  `app/infrastructures/db/` (psycopg3 driver, lazy) — switch by changing
  `default`. Schema is managed by Alembic migrations (`admin db` commands,
  see Commands) — nothing creates tables at process startup, and DDL changes
  must go through a new migration. Weaviate is reachable via `app/infrastructures/vector/` and rustfs
  via `app/infrastructures/filesystem/` (both lazy — nothing connects until
  the first `create()`; neither has a consumer in the app so far).

## Architecture & boundaries

The client ⇄ server wire contract is the **ag-ui protocol** (see the workspace
root `AGENTS.md`): the endpoint streams ag-ui events over SSE and the frontend
maps them onto its UI — keep the event sequence/format consistent when changing
either side.

Server layer rules:

- **服务层两层制** → `services/` 分为 `orchestration/`（编排层：`AgenticService`
  run SSE 行程 + `TurnFinalizer` 收尾 + `translator/`）与 `domain/`（领域层：
  按聚合分包 `conversation/`、`knowledge/`）。消费方按受众分流——用户侧行程
  （POST /agentic/run）走 orchestration；管理侧端点（会话增删查 / knowledge /
  绑定）与 Celery 后台任务直接消费 domain。依赖箭头表（单向，严禁反向）：

  ```
  api ──► {orchestration | domain}          tasks ──► domain
  orchestration ──► {domain, components, agents}
  domain ──► {repositories, infrastructures}
  components ──► {domain?, repositories, infrastructures}
  agents ──► components
  packages ──► {core, infrastructures}      （能力库层；services/components/tasks 可向下消费）
  ```

  补充约束：① domain 禁止 import orchestration/components/agents/api——领域需要的外部事实用端口倒置（如
  `knowledge/ports.py` 的 `AgentCatalog`，实现在 agents 层 `catalog.py` 以 `as_type` 回填）；
  ② 编排不触碰 repositories（持久化一律经领域服务门面，如 `ConversationService` 是
  会话持久化规则的唯一归属）；③ 写入型组件工具必须过领域服务以遵守业务规则；
  检索组件消费领域向量适配器走的就是合法的 components→domain 边；④ services 根
  `__init__.py` 为 PEP 562 惰性再导出聚合面（急切导入会与 components 成环，见文件头注释）；
  ⑤ packages（能力库层）领域无关、禁向上依赖 services/components/agents/api/tasks/
  repositories/models，services/components/tasks 按需向下消费其契约（详见下方
  「packages」条目）。禁边由 `tests/test_layer_boundaries.py` AST 扫描强制（规则改动两处同步）。
- **packages（能力库层）** → 自内聚的可复用库：契约 + 后端同包、领域无关、禁向上依赖
  （见依赖箭头表 ⑤）。居民 `signal/`：`SignalStore` ABC = key 寻址的分布式 Event
  （行为协议对齐 `threading.Event`：fire 幂等覆盖 value 并重置 TTL——兼作 keepalive
  心跳续约；is_fired / reset / get 载荷；wait 为轮询默认实现、后端可覆写原生通知；
  `ttl_seconds` 必选 = 信号必短命、孤儿自过期；失败如实上抛，读/等的宽容策略归
  消费方）。key 为调用方构造的相对键，实现负责全局前缀（redis 侧 `agentic:signal:`，
  与 Celery broker 键空间隔离）。后端：`RedisSignalStore`（默认，wireup
  `as_type=SignalStore` 绑定，连接来自 redis.yaml）+ `InMemorySignalStore`
  （测试/单机，非 injectable）。会话域消费见 `services/domain/conversation/signals.py`
  ——cancel 为第一用途（key 布局 + TTL 常量 + 消费方流程；业务流程保持 cancel 命名，
  机制层通用）。扩展路径：① 新信号用途 = 领域模块加 key 构造 + 常量 + 流程，零新类
  （例 keepalive 心跳：编排循环周期 fire 刷存活、看门狗 wait/is_fired 判僵死收口）；
  ② 新后端（etcd）= 新实现类 as_type 换绑，部署级选择需求出现再升级 signal.yaml +
  Builder registry（filesystem 同款范式）；③ 原生通知 = 后端覆写 wait（redis
  pub/sub / etcd watch），消费方无感；④ 一次性消费（auto-reset，读后自动复位）为
  显式决策点，v1 sticky；⑤ SSE 流内信号（客户端可见 keepalive）独立于 SignalStore，
  属 ag-ui 协议议题（@ag-ui/client 丢弃未知事件类型，须走 CUSTOM 事件或 SSE 注释行）。
- **api** → only HTTP wiring; delegate to **services**. Besides the business
  routers, `api/health.py` mounts the `GET /health` readiness probe（db/redis
  依赖逐项探测,503 口径）and `api/metrics.py` the `GET /metrics` Prometheus
  endpoint + `MetricsMiddleware` (pure-ASGI request counters/histogram/gauge —
  handler 标签用路由模板而非原始 path 防标签基数爆炸;`/metrics`、`/health`
  自身不计数), plus `api/middleware.py` provides the pure-ASGI edge middlewares
  (see the gotchas for the full edge-policy rundown): `RequestIDMiddleware`
  (`X-Request-ID` 生成/透传 + `logger.contextualize(request_id=...)`,响应头
  回带;SSE 线程池日志经 ContextVar 拷贝同携带,格式串为
  `{extra[request_id]}`,请求外默认 "-") plus `BodySizeLimitMiddleware`
  (请求体上限;413 的 Content-Length 路径直发信封响应——本中间件在
  ExceptionMiddleware 之外,抛异常只会变 500,实收超限路径经 `HTTPException(413)`
  复用全局 http_exception_handler;add 顺序决定层级：后 add 者在外层,请求链
  CORS → Metrics → RequestID → BodySize → 路由,拒绝响应向外穿透时补齐
  X-Request-ID 与跨域头)。限流不在应用层：原 `api/rate_limit.py`
  （fastapi-limiter 端点依赖）已于 2026-08-31 移除——fastapi-limiter 0.2.0
  与 FastAPI 0.139 的 `_IncludedRouter` 路由重构不兼容，决定外移而非修补；
  限流职责归网关层（反向代理/API 网关），应用不再产生 429。The endpoint is a
  passthrough: `AgenticService.run` already yields encoded `data: {...}\n\n`
  frames (the `ag_ui.encoder.EventEncoder` lives in the orchestrator's
  translator), returned as
  `ClosingStreamingResponse(_stream_and_close(events), ...)`
  (see the SSE gotcha for why not plain `StreamingResponse`) — don't wrap the
  response again. Global exception
  handlers (AOP) also live here: `api/exception_handlers.py`, registered in
  `create_app()`.
- **core/exceptions (framework baseline) + app/exceptions (business) +
  api/exception_handlers** → three exception tiers. Base (built-in/3rd-party
  exceptions — no classes; handlers classify them as "unknown"), framework
  (`FrameworkError` → `ConfigError`, `InfrastructureError`), business
  (`BusinessError` base defined in core as the handlers' anchor; concrete
  classes are user-defined in `app/exceptions/` carrying `http_status` +
  int codes: 1xxx conversation, 2xxx agent, 3xxx memory, 4xxx knowledge,
  5xxx user —
  the memory one is `MemoryComponentError`, never `MemoryError`, which
  would shadow the built-in). Handlers return the `Response` envelope (`error_code`/
  `error_message`, plus `detail`+`trace` in dev/test only — endpoints must
  return `Response.success(...).to_dict()` so success bodies stay stable).
  Business errors pass their message through in every environment; framework/
  base/unknown errors become a plain「服务内部错误」500 in prod. Logs always
  carry full stacks regardless of environment (`core/logging/`). The
  environment comes from `app.yaml` `environment: ${APP_ENV:dev}` via
  `core.config.get_environment()` (dev/test/prod; invalid values fail fast
  with `ConfigError`).
- **services** → 两层制见本节首条；实现风格统一为 `@injectable` + `@dataclass`
  构造注入（编排层如 `AgenticService`，领域层如 `ConversationService`）。
  `AgUiTranslator` turns LangChain stream
  chunks into ag-ui events; it is the only place that should know both shapes.
- **knowledge domain split** → 管理侧在 `services/domain/knowledge/`（one service per
  aggregate root + pipeline: `KnowledgeBaseService` / `KnowledgeDocumentService` /
  `DocumentIngestionService` / `KnowledgeBindingService`），检索能力在
  `components/knowledge/`（拓扑见「服务层两层制」箭头表——检索编排既要被 agent
  工具消费又要碰 repositories/infra 与领域向量适配器，放 components 成环风险最小）。
  知识库归属与可见性：knowledge_base 带 user_id（端点层 UserPrincipal 注入）与
  is_public；读路径（详情/列表/文档读）经 `support.require_visible_kb` 属主或公开库
  可见，写路径（更新/删除/启停/上传/文档写/retry）经 `support.require_owned_kb`
  仅属主可操作，他人资源一律 404 不泄露存在性（与会话归属同口径）；名称唯一性每
  用户一作用域（`get_kb_by_name(name, user_id)` + 复合唯一索引）；Celery 摄取
  （`process_document`）无身份上下文，仍走存在性锚定 `require_kb`；绑定管理
  （`/agent/{agent_id}/knowledge`）仅要求登录——agent↔KB 绑定仅存于管理面，
  **检索侧不消费绑定**：`knowledge_list`/`knowledge_search` 按归属可见性圈定
  （私有=属主本人 + 公开库，走 `KnowledgeBaseRepository.list_visible_kbs`，
  身份经 langgraph `configurable.user_id` 注入读取）。
  向量细节统一收敛在领域侧 `services/domain/knowledge/vector_index.py`
  （`KnowledgeVectorIndex`，components→domain 合法引用；每库一 collection
  `Knowledge_{kb_id.hex}` + 显式 schema（content 用 gse 分词，见 collection.py）+
  幂等 ensure + 批量写/删 + 整库 drop + search/search_many（alpha=1 纯向量、<1 混合，
  多库扇出融合只此一处）；`KnowledgeRetrievalService` 做可见性圈定
  （`list_visible_knowledge`/`search_for_user`，`search_for_user`/`search` 透传
  alpha——builtin:rag 的检索节点以 alpha=0.5 混合检索消费）→enabled 收敛→
  嵌入模型一致性守卫（`KnowledgeBase.embedding_model` 的消费者）→文档 enabled 后滤→
  溯源组装；知识工具共五件，经 `AgentToolbox` 装配（LLM 先列库再自选 kb_ids
  检索）：`knowledge_list`/`knowledge_search` 检索双工具 + `knowledge_context`
  （命中片段邻域窗口）/`knowledge_document_read`（按 position 范围读文，段数
  与字符预算截断并附续读指引）/`knowledge_document_list`（库内文档清单）定位
  读取三工具——定位读取以检索结果自带的 `doc_id + position` 为句柄，门面在
  `components/knowledge/ability/navigation.py`（`KnowledgeNavigationService`，
  仓储读取通道 `get_document_by_id`/`list_segments_by_doc`），守卫口径与检索
  一致（可见性 + KB/文档 enabled；不涉向量故无嵌入模型守卫），工具使用引导
  收在各工具 description（随工具走，不散落 agent 系统提示词），工具与图节点
  共用的检索结果串组装收敛在 manifest 的
  `render_search_result`（`KnowledgeSearchResult` JSON 契约唯一组装点；定位
  读取同款为 `render_segment_window`）。`KnowledgeObjectStore` 仍在 services 侧拥有
  `knowledge/{kb_id}/{doc_id}/...` key 布局 + best-effort 清理——服务不直接触碰
  `VectorStoreFactory`/raw `Filesystem`。不变量在服务层强制
  (`support.require_kb`/`require_document` raise 4001/4004)，纯函数在
  `support.py`（状态机/上传策略；`complete_document` 在文档就绪时把 KB 从
  pending 提升为 ready——启用闸门要求），repos 按聚合拆分
  (`KnowledgeDocumentRepository` 管分段 + 跨聚合 `doc_num` 计数同事务；
  `KnowledgeBindingRepository` 全量替换语义)。删库三段式第二步 = drop
  collection + 清绑定行（不再全量分段 id 逐批删）。
- **core/logging** → unified logging on top of **loguru**, wrapped by the
  `AppLogger` ABC (`base.py`) whose method surface mirrors stdlib
  `logging.Logger` (lazy `%`-formatting, `exc_info`, `extra`, `exception`,
  `isEnabledFor`/`setLevel`/`getChild`) so the backend can be swapped by
  writing one more implementation. `setup_logging()` (called first thing in
  `create_app()`, before the container) assembles sinks from `logging.yaml`:
  per-sink `type` (console/file), `level`, `format`, `serialize` (JSON);
  file sinks add `rotation`/`retention`/`compression`/`enqueue`. Global level
  defaults by environment (dev/test=DEBUG, prod=INFO; override via
  `APP_LOG_LEVEL`). stdlib `logging.getLogger(__name__)` under the `app`
  domain is bridged into the same sinks via `InterceptHandler` — uvicorn &
  third-party loggers stay untouched; use this in module-level non-DI code
  (e.g. `api/exception_handlers.py`). Inside injectable classes, inject
  `LoggerFactory` (registered explicitly in `app/core/container.py`'s
  injectables — core is not a scanned package) and take a named logger in
  `__post_init__`:
  `self.logger = self.logger_factory.get_logger(__name__)`.
- **core/config** → config via `AppConfig`; each section is a YAML file in
  `app/configs/*.yaml` (loaded by `core/config/loader.py`) with env vars
  interpolated docker-compose-style in plain YAML (`${VAR}` required,
  `${VAR:default}`/`${VAR:-default}` fallback, `$$` → literal `$`; no tags,
  no pyaml-env) — never read env ad hoc elsewhere (missing vars fail fast at
  startup: lifespan eagerly resolves `AppConfig`). Value precedence: process
  env > `.env` > inline default; `.env` is loaded by the loader on first
  config read (`AGENTIC_ENV_FILE` points at an explicit path). Config dir
  overridable via `AGENTIC_CONFIG_DIR` (or `python -m app.cmd.http
  --config-dir`). Sections: `app`
  (`AppConfig` 顶层：`default_agentic_id`——会话默认绑定的智能体 id，经
  `AGENTIC_DEFAULT_AGENT_ID` 环境变量可覆盖（inline 默认 `builtin:demo`））、
  `auth`
  (`AuthConfig`: JWT 签发/验签——`jwt_secret` via `AUTH_JWT_SECRET`（HS256
  建议 ≥32 字节，过短 PyJWT 告警）、`jwt_algorithm`、`token_expire_minutes`
  默认 7 天）、`llm`
  (`LLMConfig`: default + providers dict; each entry declares `type` —
  provider `deepseek`/`openai`/`ollama` — and `task_type` —
  `chat`/`embedding`（`rerank` 为配置预留、工厂尚未支持构建）; entry 级 `timeout`（秒，默认 120）落地为模型客户端
  请求超时——deepseek/openai 走 `request_timeout`，ollama 走
  `client_kwargs={"timeout": ...}`；entry 级 `capabilities`（`thinkable` +
  `features`——支持的 `with_structured_output` method 白名单，Literal 未知值
  加载期 fail-fast，声明顺序即自发现优先级）描述模型/部署能力；deepseek
  builder 产物 `ThinkingAwareChatDeepSeek` 据此自发现/校验 method 并对思考
  互斥项自动对齐——`function_calling`/`json_schema`（后者被 langchain-deepseek
  重映射为前者）为强制 tool_choice 通道，与思考模式互斥（400）→ 关思考副本
  执行；`json_mode` 走 response_format，思考兼容无需关思考。调用方不指定
  method（记忆抽取/裁决、RAG understand/grade），规则归属供应商模型类；`deepseek-*` via `DEEPSEEK_API_KEY`,
  `openai-chat`/`openai-embedding` via `OPENAI_API_KEY` for
  OpenAI-compatible gateways——这两条 entry 当前在 `llm.yaml` 中
  **注释未启用**（代码侧 openai builder 已注册，启用时取消注释即可）, `ollama-embedding` (bge-m3) is local —
  `api_key` optional), `db`, `vector_db` (`VectorDBConfig`: default +
  providers dict with `type`-discriminated entries + top-level `embedding`
  naming the llm embedding entry; `weaviate` via `WEAVIATE_HOST/PORT/GRPC_PORT`),
  `filesystem` (`FilesystemConfig`: default + providers dict with
  `type`-discriminated entries — `local` / `s3`; the `rustfs` entry aligns
  with the compose service via `RUSTFS_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET`),
  `memory`, `logging`, `task` (`TaskConfig`: Celery
  broker/backend, each a driver+key reference resolved by `resolve_url` —
  the `redis` driver resolves against `redis.yaml` providers; plus
  `time_limit`/`soft_time_limit` (硬/软任务超时，默认 600/540 秒，soft<hard
  由校验保证，落到 worker conf `task_time_limit`/`task_soft_time_limit`,
  broker 连接套用被引 redis entry 的 socket 超时)), `redis`
  (`RedisConfig`: default + providers dict with
  `type`-discriminated entries — `standalone` via `REDIS_URL`, entry 级
  `socket_timeout`/`socket_connect_timeout`（默认 5/3 秒）传给
  `Redis.from_url`; direct-Redis
  capabilities such as the signal store's Redis backend, plus the Celery queue via
  task.yaml's references — the single source of Redis connection facts), `http`
  (`HttpConfig`: CORS `origins` 白名单（逗号分隔字符串→列表，精确
  `http(s)://host[:port]`，`CORS_ORIGINS` 整体覆盖）+ `allow_credentials`；
  `max_body_bytes` 按 run/upload 作用域——路由匹配在
  `cmd/http/main.py` 接线，中间件在 `api/middleware.py`。限流不在本节也
  不在应用内：由网关层实现（见 Gotchas「Edge limits」）), `metrics` (`MetricsConfig`: `enabled`（
  `METRICS_ENABLED`，关闭则不挂 /metrics 与 MetricsMiddleware）+
  `worker_metrics_port`（Celery worker 指标端口，`METRICS_WORKER_PORT`）——
  端点与中间件在 `api/metrics.py`，worker 侧装配在
  `cmd/task_executor/metrics.py`), `document_parser` (`DocumentParserConfig`:
  default + providers dict with `type`-discriminated entries — `mineru_cloud`
  （MinerU 云端 PDF 解析，`MINERU_API_KEY` 必填——留空时首次解析 fail-fast；
  `poll_interval`/`poll_timeout` 轮询参数）；实现见
  `app/infrastructures/document_parser/`).
  Connection-info modeling rule (pick ONE form per provider): write a full
  connection string (`url`/`dsn`) when the driver consumes a URL natively and
  deployment hands the connection over as one unit (`standalone` redis via
  `REDIS_URL`); use discrete fields assembled by the builder when the password
  is a standalone secret (`SecretStr`), deployment env vars are discrete
  (compose `POSTGRES_*` → `postgresql` entry), or a dialect string must be
  composed (`postgresql+psycopg`). Assembly lives only in the infrastructures
  builders — never compose URLs ad hoc in services. SDK-style clients (s3,
  weaviate) are naturally discrete. Do not offer both `url` and discrete
  fields for the same entry (doubled validation/error surface, no current
  consumer needs it).
- **models/schema** → wire request/response shapes (camelCase field names like
  `threadId`/`runId` for ag-ui compatibility). **models/domain** → internal
  domain models (SQLModel tables shared by services and components).
- **components** → 自内聚能力组件 + 统一范式（`tests/test_component_structure.py`
  机器守护结构合规）。**解剖学**（固定槽位）：`__init__.py`（公共面再导出，
  不写逻辑）；`manifest.py` 必有——`register_component(ComponentSpec)` 能力
  声明 + `ToolSpec.build` 工具构造 + 导出契约模型（如 `KnowledgeSearchResult`，
  LLM/前端共用 JSON 的单一 schema 源）；`ability/` 必有——能力模块按能力
  命名、持 `@injectable` 门面服务（禁 `service.py`/`utils.py` 式泛化命名）；
  `internal/` 可选——跨能力共享机件，app 层禁入（tests 与 `app/commands`
  白盒豁免，AST 扫描强制）；`admin.py` 可选——管理面端口实现
  （`@injectable(as_type=...)` 回填领域端口，如 `MemoryEditor`）；
  `repositories/` 可选——组件自有存储策略（ABC 经 `as_type` 绑定实现，
  存储引擎来自共享 `infrastructures`，表模型在 `models/domain`）。
  **能力范式**（v1 仅 tools）：spec 是静态数据——`COMPONENT_REGISTRY` 登记时
  fail-fast 校验组件重名/组内工具重名/空描述；装配是 DI 行为——manifest 内
  `@injectable` 装配器（如 `MemoryComponent`）持门面服务，把 `ToolSpec.build`
  实例化为声明式 StructuredTool（args_schema 显式定义；`config:
  RunnableConfig` 是运行时注入参数，不进 LLM schema）。`describe_capabilities()`
  无需实例化服务即可枚举导出能力清单（后端结构化；HTTP 端点待有消费方再加）。
  **新增组件三步**：① 建包按解剖学落位；② manifest.py 声明 spec + 装配器；
  ③ `agents/toolbox.py` 加一个装配器字段（全系统唯一显式组件清单点，构造期
  校验跨组件工具名冲突）——`AgentFactory` 与各 agent 的 `build_tools` 不变。
  包内模块间一律完整子模块路径互导，禁经包 `__init__` 取属性（防初始化环）。
  快速上下文注入的读路径装配归 agent 层：`BaseAgent._input` 每轮经
  `toolbox.memory.recall` 把快注块组装进 system prompt（RAG 因图节点按文本
  渲染历史，在其 `_input` 折进末条用户消息）。轮次收尾不在 v1 能力范式内：
  编排层直接 DI 组件门面服务（`TurnFinalizer` 注入
  `MemoryConsolidationService`）。
- **repositories** → data access for the conversation domain (`@injectable`),
  backed by the injected SQLAlchemy `Engine` (SQLModel sessions).
- **infrastructures** → shared drivers/connections for every layer:
  `llm/` — `ModelFactory` builds models from `AppConfig.llm` provider entries;
  an entry's `task_type` (chat / embedding) declares its purpose:
  `create()` accepts chat entries only, `create_embeddings(name)` embedding
  entries only (name required — `default` points at a chat entry); mismatch
  raises ValueError. Adding a provider = one `ModelBuilder` subclass +
  `@register` (side-effect import in `model_factory.py`), implementing
  `build_chat` and optionally `build_embedding`. Registered: deepseek (chat),
  openai (chat + embedding, for RAG), ollama (chat + embedding, local —
  `api_key` optional for local providers; auth via `api_url` userinfo).
  `db/` — `DatabaseFactory` builds SQLAlchemy `Engine` instances from
  `AppConfig.db` provider entries: `create(name=None)` (default falls back to
  `DBConfig.default`), engines cached per entry key (connection pools are heavy
  resources). Registered: sqlite (file path, parent dir auto-created,
  `check_same_thread=False`) and postgresql (discrete host/port/user/password/db
  fields → `postgresql+psycopg` URL, psycopg3 driver, `pool_pre_ping`; `password`
  is a `SecretStr`). The default entry is additionally bound as a singleton via
  `create_default_db` (same convention as `create_default_filesystem`) so
  repositories inject the `Engine` directly; inject the factory only when a
  specific entry is needed. Adding a provider = one `DatabaseBuilder` subclass +
  `@register` (side-effect import in `db_factory.py`). Lazy like vector.
  `vector/` — `VectorStoreFactory`
  builds LangChain `VectorStore` instances (the contract type; consumers never
  see the vendor client) from `AppConfig.vector_db` provider entries:
  `create(index_name=..., name=None, text_key="text", **overrides)`.
  Embeddings are config-driven — `vector_db.yaml`'s top-level `embedding`
  names an llm embedding entry, resolved via `ModelFactory.create_embeddings`
  (fail-fast ValueError on unknown key/task_type mismatch). Vendor clients
  (gRPC) are cached per entry key inside the factory (a wireup singleton);
  `VectorStore` wrappers are lightweight and created per call. Collections are
  auto-created by the vendor integration on first use (no vectorizer —
  vectors come from app-side embeddings). Adding a provider = one
  `VectorDBBuilder` subclass + `@register` (side-effect import in
  `vector_store_factory.py`), implementing `build_client` + `build`.
  Registered: weaviate (`langchain-weaviate`, weaviate-client v4 gRPC).
  Lazy: nothing connects until the first `create()`. `filesystem/` —
  `FilesystemFactory` builds `Filesystem` contract instances (read / put /
  delete / exists / list with object-storage semantics: posix-style relative
  keys, idempotent delete, prefix listing) from `AppConfig.filesystem`
  provider entries via `create(name=None)`; vendor clients cached per entry
  key. Registered: local (pathlib; key → root-relative path, escapes
  rejected) and s3 (obstore `S3Store` — custom endpoints for rustfs/MinIO;
  buckets must pre-exist, hence the compose `rustfs-init` service). The
  default entry is additionally bound as a singleton via
  `create_default_filesystem` (same convention as `create_default_db`) so
  consumers inject the `Filesystem` contract directly; inject the factory
  only when a specific entry is needed. Adding a
  provider = one `FilesystemBuilder` subclass + `@register` (side-effect
  import in `filesystem_factory.py`). Lazy like vector.

Server DI uses **wireup**. The shared registration lives in
`app/core/container.py` (`build_async_container()` /
`build_sync_container()`, injectables `[AppConfig, LoggerFactory, services,
components, packages, repositories, infrastructures, agents]` — classes like
`LoggerFactory` are passed directly since `core` is not a scanned package).
The two entrypoints each build their **own** container instance from it
(wireup's FastAPI integration requires an async container, its Celery
integration a sync one — one instance cannot serve both):

- `cmd/http/main.py` → async container +
  `wireup.integration.fastapi.setup` (endpoints take `Injected[...]`).
- `cmd/task_executor/main.py` → sync container +
  `wireup.integration.celery.setup` (manages per-task scoped containers via
  `task_prerun`/`task_postrun` signals and closes the container on worker
  shutdown); task definitions live in the `app/tasks/` package — its
  `__init__.py` auto-imports every non-underscore module, so adding a task
  file self-registers it (senders import the task object from the module,
  e.g. `from app.tasks.ping import ping`). Tasks get DI via the
  `@wireup.integration.celery.inject` decorator on top of `@celery_app.task`.

New injectables must be added to `app/core/container.py` to be resolvable
in **both** apps. Note `core/container.py` imports the business packages —
never import it from modules those packages depend on (core.config /
core.logging), or you create a cycle.

LLM stack: **LangChain `create_agent`**（预置 ReAct 循环，经 `BaseAgent.build_graph()`
扩展点，自建图智能体覆写之）+ `agent.stream_events(version="v3")`
with `stream.interleave("messages")`. The default chat model is **DeepSeek**
via `ChatDeepSeek` (`langchain-deepseek`), built by `app/infrastructures/llm/`
from `AppConfig.llm` (named provider entries like `deepseek-flash` /
`deepseek-pro`; `openai-chat` / `openai-embedding` entries target an
OpenAI-compatible gateway（当前在 `llm.yaml` 中注释未启用）; `ollama-embedding`
(bge-m3) 支撑知识库向量; `ollama-reranker` 仅为配置预留，工厂尚未支持 rerank 构建).

## Conventions

- **Python**: 3.12 (`uv`). Type hints + pydantic models; injectable classes are
  `@dataclass` with `@injectable`. Comments/docstrings are a mix of Chinese/English.

## Gotchas

- **`.env` IS gitignored**（`.gitignore` 忽略 `.env`，未入库）and holds a
  real **DeepSeek API key** — don't paste the key into commits/logs. CI 为
  `llm.yaml` 必填占位符提供哑值，见 `.github/workflows/ci.yml`。
- CORS is a whitelist (`http.yaml` `cors.origins`, default the two Vite dev-server
  loopback origins; override wholesale via `CORS_ORIGINS` comma-separated). Methods
  are pinned to GET/POST/PATCH/DELETE/OPTIONS and headers to Content-Type /
  X-Request-ID / Authorization. Keep origins exact (`http(s)://host[:port]`, no
  trailing slash, no `*` — the config validator enforces this).
- Edge limits: **rate limiting is NOT implemented in-app**（2026-08-31 移除原
  `api/rate_limit.py` fastapi-limiter 方案——其 0.2.0 实现遍历
  `request.app.routes` 取 `route.path` 计算限流 key，与 FastAPI 0.139
  `include_router` 不再摊平子路由（`_IncludedRouter` 包装对象无 `path`）的
  重构不兼容，include 出来的端点一进依赖即 500；决定外移而非修补）——
  限流由网关层（反向代理/API 网关）按部署策略实现；应用自身不产生 429，
  `fastapi-limiter`/`pyrate-limiter` 依赖已从 pyproject 移除。Body-size caps
  remain in `api/middleware.py` via `http.yaml`
  (run 1 MiB JSON, upload 64 MiB multipart > the 50 MiB service-level file cap).
  413 rejections use the standard `Response` envelope. Upload itself streams:
  the endpoint passes `UploadFile.file` through and
  `KnowledgeDocumentService.create_document` wraps it in a counting/sha256
  read-through reader (`_CountingDigestReader`) so the object-store `put` never
  holds the whole file in memory; seekable streams are size-probed up front,
  lying/chunked streams trip the reader mid-copy and the partial object is
  cleaned up.
- SSE (`/agentic/run`) is **outside** the global exception handlers: once the
  200/SSE headers are committed, in-stream errors can only surface as an ag-ui
  `RunErrorEvent`. `AgenticService.run` therefore yields `RunStarted` first,
  then wraps both setup (turn/message writes, agent creation, history load)
  and the streaming loop: any `Exception` logs the full stack (business errors
  as warning), marks the turn `FAILED` (best-effort store, secondary failures
  only logged via `_fail_turn_safely`), and emits a `RunErrorEvent` whose
  message follows the same policy as `api/exception_handlers.py`
  (`BusinessError` verbatim in every env; framework/base/unknown raw in
  dev/test, 「服务内部错误」 in prod — see `_run_error_message`; keep the two
  in sync). Client disconnects are a separate path: Starlette throws
  `GeneratorExit` (a `BaseException`) into the generator at its suspended
  `yield`; a dedicated `except GeneratorExit` in the streaming segment calls
  `_cancel_turn_safely` — which marks the turn `CANCELED` only while still
  `RUNNING` (a turn already `FAILED` keeps that status) — then re-raises.
  The handler must never yield (`RuntimeError: generator ignored
  GeneratorExit`); partial assistant messages are not persisted and
  `TurnFinalizer` doesn't run on cancel, mirroring the error path. Delivery
  is deterministic only because the endpoint wraps the generator with
  `_stream_and_close` + `ClosingStreamingResponse` (both in
  `api/v1/endpoints/agentic.py`): Starlette never closes the body iterator
  on disconnect — its `iterate_in_threadpool` has no `finally`, so the
  abandoned sync generator would otherwise wait for a cyclic-GC pass
  (minutes in practice, or never if the process exits first). The wrapper's
  `finally` closes the sync generator as soon as the response cycle unwinds.
  Cancel latency is bounded by the in-flight `next()`: sub-second while
  token deltas stream, but a disconnect during a tool call lands only when
  the tool returns (no yield boundary inside). The frontend
  Stop button reaches this path via `agent.abortRun()` wired to
  `useAgUiRuntime`'s `onCancel` (page refresh/close and network drops land
  here too). An explicit cancel channel complements disconnect-based cancel
  (transport-independent: proxies may swallow disconnect events, and
  `POST /agentic/run/cancel` works without dropping the connection): the
  endpoint fires a thread-scoped signal via the generic `SignalStore` contract
  (`app/packages/signal/`, a distributed-Event ABC: fire / is_fired / reset /
  wait / get, TTL mandatory; the default Redis backend connects via the
  independent `redis.yaml` section, by default the same db0 instance as the
  Celery broker, key `agentic:signal:conversation:{thread_id}:cancel`, TTL 1h —
  key layout and constants owned by `services/domain/conversation/signals.py`).
  The contract is fail-loud; the tolerance policy lives in
  `ConversationService` — reads/resets degrade instead of blocking the run
  (throttled warning, treated as not canceled), and the signal is reset
  defensively at `open_turn`. The same channel doubles as the **graceful
  shutdown** path: `AgenticService` registers in-flight streams per
  `thread_id` (`run` wrapper + `begin_shutdown()`), and the HTTP lifespan
  (`cmd/http/main.py`) installs SIGTERM/SIGINT handlers that first fire the
  cancel flag for every active stream (frame-level check closes each within
  ~0.5s — turn CANCELED, no partial message persisted) and then chain to
  uvicorn's own handler for the normal drain; handlers are restored on
  lifespan exit.
  The streaming loop re-checks it throttled at **frame level** — always on
  the first streamed frame, then at most every 0.5s
  (`CANCEL_CHECK_INTERVAL_SECONDS`) — and
  lands on the same `_cancel_turn_safely` path, silently closing the stream
  (no RUN_ERROR frame; the client's local abort state is the source of
  truth, since ag-ui has no server-side CANCELLED event). Tools check the
  flag at entry via the `cancel_check` closure carried in
  `AgentRunContext` → `_config`'s `configurable` (see `_cancel_guard` in
  `agents/base.py`; it synthesizes a `config: RunnableConfig` param for
  tools that don't declare one, so langchain injects + schema-excludes it —
  same pattern as the memory tools). Flag reads are best-effort: the store
  being down degrades to disconnect-only cancel and never blocks the run; the
  cancel endpoint's write failure propagates to the caller.
- Multi-turn history is server-authoritative: `ConversationService.replay_history`(由 `AgenticService.run` 调用) rebuilds
  the LLM context from the DB (`ConversationRepository.list_replay_messages`,
  last ~20 COMPLETED turns — FAILED/CANCELED/stray RUNNING turns are skipped)
  plus the latest user message — the rest of the ag-ui `messages` payload is
  ignored (only `messages[-1].content` is used). Replay is faithful:
  `TOOL_CALL`/`TOOL_RESULT` rows become `AIMessage(tool_calls)` +
  `ToolMessage` pairs (unpaired calls dropped — tool errors persist no result
  row, and OpenAI-compatible APIs 400 on orphan tool_calls); `THOUGHT`
  (reasoning) is never replayed. `AgentRunContext` carries `messages`, and
  `BaseAgent._input` injects the current time into the last user message.
  Long-term memory is the v2 graph model (`docs/memory-v2-design.md` is the
  design contract): four tables in `app/models/domain/memory/` (entity /
  bi-temporal statement / episode / episode-link), consolidated into the
  component at `app/components/memory/` by `TurnFinalizer`'s third step
  (extract→resolve entities→adjudicate ADD/REPLACE/SKIP→persist+vector upsert).
  Entity resolution is **two-stage**: `MemoryVectorIndex.search` only nominates
  candidates — its hit score is a Weaviate hybrid fusion score (relative,
  capped at 1.0; never compare it against absolute thresholds), and the merge
  decision uses `text_cosine` (client-side embedding cosine, same model as the
  writes) ≥ `resolution.similarity_threshold` plus an entity_type guard;
  renderer-internal trace refs (`#S13`/`§E3` shapes) are barred from entity
  names/aliases. The 2026-08-28 incident (a school merged into a company
  entity on fusion score 1.0 / true cosine 0.44) is documented in
  `docs/memory-v2-design.md` §8; polluted archives are repairable via
  `python -m app.cmd.admin memory repair --apply`.
  Recall has two tiers: `BaseAgent._input` composes a brief block into the
  head SystemMessage each run (`MemoryRecallService.build_fast_context`, pure
  SQL scoring, no LLM/embedding — a stable top-10 standing digest: it neither
  skips session-fed ids nor bumps access counts, but still marks ids so deep
  tools return only增量; `builtin:rag` folds the block into the last user
  message since its nodes render history as text and would drop a
  SystemMessage); deep recall = three tools (`timeline`/`expand`/`state_at`,
  declared and built in `components/memory/manifest.py`, reading the current
  thread from the langgraph-injected `RunnableConfig` — `BaseAgent._config` puts `thread_id`
  into `configurable`, and tools declare a `config: RunnableConfig` param that
  never reaches the LLM tool schema; do NOT use a ContextVar for this: the
  orchestrator is a sync generator resumed in a different context copy per
  `next()` and langgraph runs tools on its own executor threads, so a
  ContextVar is invisible to the tools and its token reset raises ValueError
  at stream end, severing the SSE connection). Sessions track injected
  ids in-process (`SessionInjectRegistry`) so tools return only增量.
  `MemoryVectorIndex` lives in `services/domain/memory/` (components consume
  it over the legal components→domain edge); vector facts源 is SQL — the index
  is rebuildable (`rebuild()`).
- **用户模块（认证 + 会话/记忆/知识库归属）** → 2026-08-30 引入，四块机制：
  ① **JWT 认证走 FastAPI 依赖而非中间件**：`api/deps.py` 的 `require_user`
  无状态验签（不查库）产出 `UserPrincipal(user_id, username)`，按 router
  在 `cmd/http/main.py` 的 `include_router` 处声明式挂载
  （agentic/knowledge/agent_knowledge/memory 组），
  `/auth`、`/health` 公开——无路径白名单。不用中间件的
  原因：现有 `api/middleware.py` 是纯 ASGI 且在全局异常处理器之外，401 得
  手写信封（BodySize 的 413 直发同因）；`BaseHTTPMiddleware` 对 SSE 有缓冲风险。
  注册/登录在 `services/domain/user/`（PBKDF2 stdlib 哈希 + pyjwt；错用户名
  与错密码统一 `InvalidCredentialsError` 5002 不泄露存在性；注册即登录直接
  签发 token）。② **会话归属**：`agentic_conversation.user_id`（FK→users），
  归属过滤下沉到仓储查询条件——`init_conversation` get-or-create 后由
  `ConversationService.open_turn` 比对归属，他人 thread_id 续聊/取消/查删
  一律 404 不泄露存在性。③ **记忆用户级作用域（设计反转）**：memory v2 原
  锁定「单用户全局」（docs/memory-v2-design.md §3.3），现 entity/statement/
  episode 三表带 `user_id`；`MemoryRepository.for_user(uid)` / 
  `MemoryVectorIndex.for_user(uid)` / `MemoryEditor·GraphReader.for_user(uid)`
  返回作用域视图（scope 过滤在 sqlite 仓储与 `_scope_rows` 内强制，id 直取
  命中他人行视为不存在；向量按用户分 collection `Memory_{uid.hex}`）；注入
  单例是无作用域的全局算子视角，**仅供维护 CLI 与 for_user 工厂使用**，
  用户侧行程必须先 for_user。作用域标记字段一律 `field(init=False)`——
  wireup 按 `__init__` 签名提取依赖，可选 UUID 构造参数会破坏其注册表校验
  （作为他人依赖时先于自身清理被递归验证 → KeyError）。深度回忆三件套经
  `configurable.user_id`（`BaseAgent._config`）取身份，`_user(config)` 同款
  读取模式。④ **知识库归属 + 公开/私有标识**：`knowledge_base.user_id`
  （FK→users，迁移回填默认用户）+ `is_public`（缺省私有；存量回填为公开
  保持迁移前全员可见行为）；读路径 `require_visible_kb`（属主或公开）、
  写路径 `require_owned_kb`（仅属主，公开不让渡管理权），他人资源 404 不
  泄露存在性；名称唯一性每用户一作用域（复合唯一 `(user_id, name)`）；
  Celery 摄取无身份走 `require_kb` 存在性锚定；绑定管理仅要求登录（绑定仅存
  于管理面；检索工具 `knowledge_list`/`knowledge_search` 按归属可见性圈定，
  不消费绑定）。前端配套：zustand auth-store（localStorage 持久化）+ axios 请求
  拦截器注 Bearer + 401 登出跳转 + HttpAgent fetch 覆盖（SSE 401 在 200 头
  之后只能 fetch 层拦截）。
- **builtin:rag（自建 LangGraph 图智能体）** → 与 `create_agent` 预置 ReAct
  循环的关键差异与机制（包在 `app/agents/builtin/rag/`：state/prompts/nodes/
  graph/stream/agent）：
  - **图拓扑**：understand（一次结构化调用双职：chitchat/knowledge 分路 + 多轮
    凝练 standalone question）→ retrieve（`KnowledgeRetrievalService`，alpha=0.5
    混合、top_k=6）→ grade（一次结构化调用批量二元过滤）→ generate（带 [n] 引用，
    chitchat 路由无资料直答）；grade 后未命中走 rewrite → retrieve 唯一循环边
    （配额 1 次），用尽走 fallback（LLM 硬约束兜底话术，保证流式/落库路径统一）。
    状态是显式 `RagState` TypedDict（`_input` 整体初始化），recursion_limit=12，
    无 checkpointer。
  - **中间调用抑制**：understand/grade/rewrite 的 LLM 调用（含
    `with_structured_output` 的内部调用——它同样进 messages 通道且 node 归属
    当前图节点）不进 UI 不落库：`RagRunStream` 包装 `interleave`，按
    `ChatModelStream.node` ∈ `INTERNAL_LLM_NODES` 滤除。跳过项不消费投影也不卡
    泵（mux 自驱拉流，投影仅缓冲）。generate/fallback 正常外流——invoke 靠
    generate/fallback 向 state.messages 追加 AIMessage 兼容基类取尾逻辑。
  - **检索步骤可见性**：图无模型发起的工具调用，原生 tools stream mode 恒空；
    retrieve 节点内 `get_stream_writer()` 发 tool-started/tool-result/
    tool-finished（tool 名复用 `knowledge_search`，result 内容经
    `render_search_result` 与工具同一 JSON），`ToolEventsTransformer`
    （required_stream_modes=("custom",) + `StreamChannel("tools")`）接进 tools
    通道——AgUiTranslator/前端/落库零改动。`StorageTranslator` 相应把
    tool-started 落 TOOL_CALL 行（按 tool_call_id 与模型 tool_call 去重，args
    取事件可选字段），否则 RAG 轮只剩孤儿 TOOL_RESULT 行。
  - **历史注入**：`RagAgent._input` 滤除历史中的 ToolMessage 与带 tool_calls 的
    AIMessage（凝练上下文只需 user/assistant 文本），检索痕迹不进下一轮 LLM
    上下文；时间前缀注入与 `BaseAgent._input` 同口径。
  - 检索异常降级：retrieve 捕获异常发 tool-error 并按未命中继续（改写重试→兜底），
    不炸整轮流（`KnowledgeVectorIndex.search` 自身已吞异常，此处兜服务层故障）。
- `README.md` is the human-facing overview (quickstart, config, API, layout);
  this file remains the deeper agent guide — keep both in sync when adding
  entrypoints/config sections/endpoints.
