# AGENTS.md — `server/`

Guide for the Python backend of the `agentic` monorepo. Workspace overview and
cross-cutting contract: [`../AGENTS.md`](../AGENTS.md). Frontend guide:
[`../client/agentic-client/AGENTS.md`](../client/agentic-client/AGENTS.md).

Python 3.12 backend (**FastAPI + Celery + LangChain/LangGraph + ag-ui**) serving
`POST /agentic/chat` as an SSE stream of ag-ui events to the React client.

## Layout

```
server/                          # this directory is its own git repo (the workspace root is not)
├── pyproject.toml               # uv-managed deps (langchain, langchain-deepseek, wireup, ag-ui-protocol, celery[redis], langchain-weaviate)
├── docker-compose.yaml          # Middleware stack: PostgreSQL + Weaviate + RustFS + Redis (local dev)
├── .python-version              # 3.12
├── .env                         # Secrets: DEEPSEEK_API_KEY (live), OPENAI_API_KEY (placeholder, OpenAI 兼容网关)
├── .gitignore
└── app/
    ├── cmd/                     # Entrypoints — one subdir per launchable app
    │   ├── http/                # main.py (create_app + server, uvicorn import string app.cmd.http.main:server)
    │   │                        #   + __main__.py (argparse → uvicorn.run)
    │   └── task_executor/       # Celery app: main.py (sync wireup container + wireup.integration.celery.setup),
    │                            #   __main__.py (argparse → worker_main); tasks live in app/tasks/
    ├── tasks/                   # Celery task layer: one module per task domain; __init__.py auto-imports
    │                            #   every non-underscore module so tasks self-register (include=["app.tasks"])
├── api/                     # exception_handlers.py (global AOP handlers); v1/endpoints/ — agentic.py -> POST /agentic/chat (StreamingResponse)
│                              #   + GET/DELETE /agentic/conversation[/...] 会话列表/详情/历史/删除;
│                              knowledge.py -> /knowledge 管理侧 CRUD（multipart 上传，文件落 rustfs）;
│                              agent_knowledge.py -> /agent/{agent_id}/knowledge 绑定管理（GET/PUT 全量替换）;
│                              memory.py -> /memory/graph 记忆图快照（管理侧只读；at 参数做时点回放，
│                              服务为 domain/memory/graph_snapshot.py 经 ports.MemoryGraphReader 端口）
├── core/container.py        # Shared wireup injectables + build_async_container()/build_sync_container()
    ├── core/config/             # AppConfig (@injectable pydantic model): llm.py (LLMConfig + LLMProviderEntry), db.py, vector_db.py, filesystem.py, memory.py, task.py, logging.py; get_environment()
    ├── core/exceptions/         # Framework baseline exceptions: AgenticError root → framework.py (FrameworkError, ConfigError,
    │                            #   InfrastructureError) + business.py (BusinessError base — the handlers' anchor); concrete
    │                            #   business exceptions are user-defined in app/exceptions/
    ├── core/logging/            # Logging infra package: base.py (AppLogger ABC, stdlib-compatible facade), loguru_backend.py
    │                            #   (LoguruAppLogger + InterceptHandler bridging stdlib "app" loggers), setup.py (setup_logging()
    │                            #   assembles sinks from logging.yaml), service.py (LoggerFactory @injectable singleton)
    ├── exceptions/              # Business exceptions, user-defined per domain (subclass core's BusinessError):
    │                            #   conversation 1xxx / agent 2xxx / memory 3xxx / knowledge 4xxx
    ├── agents/                  # BaseAgent + @register_agent registry, AgentFactory, builtin agents (demo/summary)
├── components/              # Self-contained capability components: components/memory (MemoryService 门面：
│                            #   remember 巩固管线·build_fast_context 快速注入·timeline/expand/state_at 深度三件套、
│                            #   own repositories/ 图谱实现、renderer 模板契约/scoring/extraction/vocab；
│                            #   向量适配器在 services/domain/memory 经 components→domain 合法边引用)
│                            #   and components/knowledge (retrieval:
│                            #   KnowledgeRetrievalService + knowledge_list/knowledge_search tools) —
│                            #   components may import domain/repositories/infra (单向)，严禁
│                            #   app.services.orchestration / app.agents / app.api
├── services/                # 两层制（依赖箭头表见下「服务层两层制」）—— orchestration/: 用户侧行程
│                            #   （ChatOrchestrator chat 编排 + translator/ 双翻译器 + TurnFinalizer 收尾）；
│                            #   domain/: 领域服务，按聚合分包—— conversation/（title_generator 裸模型
│                            #   标题生成）、knowledge/（KB/document/ingestion/binding 服务 +
│                            #   object_store/support 纯函数/document_chunker）与 memory/
│                            #   （记忆向量适配器 MemoryVectorIndex+collection 显式 schema）
├── models/
│   ├── schema/request/chat.py   # ChatRequest / ChatMessage (camelCase fields for ag-ui)
│   ├── domain/agentic/      # SQLModel tables: conversation / turn / message
│   ├── domain/knowledge/    # SQLModel tables: knowledge_base / knowledge_base_document(+segment) /
│                             #   knowledge_agent_binding (agent↔KB); KnowledgeStatus enum; segment.id doubles
│                             #   as the Weaviate object UUID
│   └── domain/memory/       # 记忆 v2 双层图谱四表: entity / statement(双时间轴·SUPERSEDED 不删除可回放)
│                             #   / episode / episode_link —— 设计定稿见 docs/memory-v2-design.md
├── repositories/            # Data-access (@injectable) — ConversationRepository,
│                             #   KnowledgeBaseRepository, KnowledgeDocumentRepository (docs+segments),
│                             #   KnowledgeBindingRepository
└── infrastructures/         # Shared drivers: llm/ (ModelFactory + ModelBuilder registry), db/ (DatabaseFactory +
                                 #   DatabaseBuilder registry — sqlite / postgresql via psycopg3, create_default_db
                                 #   singleton Engine), vector/ (VectorStoreFactory + VectorDBBuilder registry —
                                 #   Weaviate via langchain-weaviate; build_client/build + drop for collection
                                 #   deletion), filesystem/ (FilesystemFactory + FilesystemBuilder registry —
                                 #   local pathlib / S3 via obstore)
```

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
  [--config-dir PATH] [--env-file PATH]` (argparse entrypoint in
  `cmd/http/__main__.py`; flags are bridged to
  `AGENTIC_CONFIG_DIR`/`AGENTIC_ENV_FILE` env vars).
- Run task executor (Celery worker; needs `docker compose up -d redis` first):
  `uv run celery -A app.cmd.task_executor.main worker` or
  `uv run python -m app.cmd.task_executor [--pool=solo]` (extra args pass
  through to the celery worker command). Broker/backend URLs come from
  `app/configs/task.yaml` (defaults `redis://127.0.0.1:6379/0|1`, overridable
  via `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`).
- Run unit tests: `uv run pytest tests/` (pure unit level — SQLite 临时库 +
  stub 向量索引，不碰 Weaviate/MinerU/Ollama).
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
    broker/result backend by `app/cmd/task_executor` (matches
    `app/configs/task.yaml` defaults)
- Redis is used by the task executor. The app's default db is still SQLite
  (`db.yaml` `default: sqlite`); the matching `postgres` entry is wired through
  `app/infrastructures/db/` (psycopg3 driver, lazy) — switch by changing
  `default`. Weaviate is reachable via `app/infrastructures/vector/` and rustfs
  via `app/infrastructures/filesystem/` (both lazy — nothing connects until
  the first `create()`; neither has a consumer in the app so far).

## Architecture & boundaries

The client ⇄ server wire contract is the **ag-ui protocol** (see the workspace
root `AGENTS.md`): the endpoint streams ag-ui events over SSE and the frontend
maps them onto its UI — keep the event sequence/format consistent when changing
either side.

Server layer rules:

- **服务层两层制** → `services/` 分为 `orchestration/`（编排层：`ChatOrchestrator`
  chat SSE 行程 + `TurnFinalizer` 收尾 + `translator/`）与 `domain/`（领域层：
  按聚合分包 `conversation/`、`knowledge/`）。消费方按受众分流——用户侧行程
  （POST /agentic/chat）走 orchestration；管理侧端点（会话增删查 / knowledge /
  绑定）与 Celery 后台任务直接消费 domain。依赖箭头表（单向，严禁反向）：

  ```
  api ──► {orchestration | domain}          tasks ──► domain
  orchestration ──► {domain, components, agents}
  domain ──► {repositories, infrastructures}
  components ──► {domain?, repositories, infrastructures}
  agents ──► components
  ```

  补充约束：① domain 禁止 import orchestration/components/agents/api——领域需要的外部事实用端口倒置（如
  `knowledge/ports.py` 的 `AgentCatalog`，实现在 agents 层 `catalog.py` 以 `as_type` 回填）；
  ② 编排不触碰 repositories（持久化一律经领域服务门面，如 `ConversationService` 是
  会话持久化规则的唯一归属）；③ 写入型组件工具必须过领域服务以遵守业务规则；
  检索组件消费领域向量适配器走的就是合法的 components→domain 边；④ services 根
  `__init__.py` 为 PEP 562 惰性再导出聚合面（急切导入会与 components 成环，见文件头注释）。
  禁边由 `tests/test_layer_boundaries.py` AST 扫描强制（规则改动两处同步）。
- **api** → only HTTP wiring; delegate to **services**. The endpoint wraps the
  service's generator with `ag_ui.encoder.EventEncoder` and returns a
  `StreamingResponse(media_type="text/event-stream")`. The encoder already emits
  `data: {...}\n\n` frames — don't wrap the response again. Global exception
  handlers (AOP) also live here: `api/exception_handlers.py`, registered in
  `create_app()`.
- **core/exceptions (framework baseline) + app/exceptions (business) +
  api/exception_handlers** → three exception tiers. Base (built-in/3rd-party
  exceptions — no classes; handlers classify them as "unknown"), framework
  (`FrameworkError` → `ConfigError`, `InfrastructureError`), business
  (`BusinessError` base defined in core as the handlers' anchor; concrete
  classes are user-defined in `app/exceptions/` carrying `http_status` +
  int codes: 1xxx conversation, 2xxx agent, 3xxx memory, 4xxx knowledge —
  the memory one is `MemoryComponentError`, never `MemoryError`, which
  would shadow the built-in). Handlers return the `Response` envelope (`error_code`/
  `error_message`, plus `detail`+`trace` in dev/test only — endpoints must
  return `Response.success(...).to_dict()` so success bodies stay stable).
  Business errors pass their message through in every environment; framework/
  base/unknown errors become a plain「服务内部错误」500 in prod. Logs always
  carry full stacks regardless of environment (`core/logging/`). The
  environment comes from `app.yaml` `environment: ${AGENTIC_ENV:dev}` via
  `core.config.get_environment()` (dev/test/prod; invalid values fail fast
  with `ConfigError`).
- **services** → 两层制见本节首条；实现风格统一为 `@injectable` + `@dataclass`
  构造注入（编排层如 `ChatOrchestrator`，领域层如 `ConversationService`）。
  `AGUIEventTranslator` turns LangChain stream
  chunks into ag-ui events; it is the only place that should know both shapes.
- **knowledge domain split** → 管理侧在 `services/domain/knowledge/`（one service per
  aggregate root + pipeline: `KnowledgeBaseService` / `KnowledgeDocumentService` /
  `DocumentIngestionService` / `KnowledgeBindingService`），检索能力在
  `components/knowledge/`（拓扑见「服务层两层制」箭头表——检索编排既要被 agent
  工具消费又要碰 repositories/infra 与领域向量适配器，放 components 成环风险最小）。
  向量细节统一收敛在领域侧 `services/domain/knowledge/vector_index.py`
  （`KnowledgeVectorIndex`，components→domain 合法引用；每库一 collection
  `Knowledge_{kb_id.hex}` + 显式 schema（content 用 gse 分词，见 collection.py）+
  幂等 ensure + 批量写/删 + 整库 drop + search/search_many（alpha=1 纯向量、<1 混合，
  多库扇出融合只此一处）；`KnowledgeRetrievalService` 做绑定解析→enabled 收敛→
  嵌入模型一致性守卫（`KnowledgeBase.embedding_model` 的消费者）→文档 enabled 后滤→
  溯源组装；`knowledge_list`/`knowledge_search` 双工具经 `AgentToolbox` 装配（LLM
  先列库再自选 kb_ids 检索）。`KnowledgeObjectStore` 仍在 services 侧拥有
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
  `AGENTIC_LOG_LEVEL`). stdlib `logging.getLogger(__name__)` under the `app`
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
  --config-dir`). Sections: `llm`
  (`LLMConfig`: default + providers dict; each entry declares `type` —
  provider `deepseek`/`openai`/`ollama` — and `task_type` —
  `chat`/`embedding`; `deepseek-*` via `DEEPSEEK_API_KEY`,
  `openai-chat`/`openai-embedding` via `OPENAI_API_KEY` for
  OpenAI-compatible gateways, `ollama-embedding` (bge-m3) is local —
  `api_key` optional), `db`, `vector_db` (`VectorDBConfig`: default +
  providers dict with `type`-discriminated entries + top-level `embedding`
  naming the llm embedding entry; `weaviate` via `WEAVIATE_HOST/PORT/GRPC_PORT`),
  `filesystem` (`FilesystemConfig`: default + providers dict with
  `type`-discriminated entries — `local` / `s3`; the `rustfs` entry aligns
  with the compose service via `RUSTFS_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET`),
  `memory`, `logging`, `task` (Celery
  broker/backend).
- **models/schema** → wire request/response shapes (camelCase field names like
  `threadId`/`runId` for ag-ui compatibility). **models/domain** → internal
  domain models (SQLModel tables shared by services and components).
- **components** → self-contained capability components (memory today;
  retriever and others follow the same shape): own service facade + tools +
  own `repositories/` managing storage/retrieval per strategy. A component's
  repository is bound to its ABC via `@injectable(as_type=...)` (see
  `SqliteMemoryRepository` → `MemoryRepository`); the storage engine comes from
  the shared `infrastructures` layer; table models stay in `models/domain`.
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
components, repositories, infrastructures, agents]` — classes like
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

LLM stack: **LangChain `create_agent`** + `agent.stream_events(version="v3")`
with `stream.interleave("messages")`. The default chat model is **DeepSeek**
via `ChatDeepSeek` (`langchain-deepseek`), built by `app/infrastructures/llm/`
from `AppConfig.llm` (named provider entries like `deepseek-flash` /
`deepseek-pro`; `openai-chat` / `openai-embedding` entries target an
OpenAI-compatible gateway — embedding via `create_embeddings` is the base for
future RAG).

## Conventions

- **Python**: 3.12 (`uv`). Type hints + pydantic models; injectable classes are
  `@dataclass` with `@injectable`. Comments/docstrings are a mix of Chinese/English.

## Gotchas

- **`.env` is NOT gitignored** and currently holds a **live DeepSeek API
  key**. Don't add secrets here without checking `.gitignore`, and don't paste
  the key into commits/logs.
- CORS is wide open (`allow_origins=["*"]`, credentials on).
- SSE (`/agentic/chat`) is **outside** the global exception handlers: once the
  200/SSE headers are committed, in-stream errors can only surface as an ag-ui
  `RunErrorEvent`. `ChatOrchestrator.chat` therefore yields `RunStarted` first,
  then wraps both setup (turn/message writes, agent creation, history load)
  and the streaming loop: any `Exception` logs the full stack (business errors
  as warning), marks the turn `FAILED` (best-effort store, secondary failures
  only logged via `_store_turn_safely`), and emits a `RunErrorEvent` whose
  message follows the same policy as `api/exception_handlers.py`
  (`BusinessError` verbatim in every env; framework/base/unknown raw in
  dev/test, 「服务内部错误」 in prod — see `_run_error_message`; keep the two
  in sync). Remaining known gap: client disconnects (`GeneratorExit`) never
  set `CANCELED`.
- Multi-turn history is server-authoritative: `ConversationService.replay_history`(由 `ChatOrchestrator.chat` 调用) rebuilds
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
  Recall has two tiers: `ChatOrchestrator.chat` auto-injects a brief block
  (`MemoryService.build_fast_context`, pure SQL scoring, no LLM/embedding)
  before streaming; deep recall = three tools (`timeline`/`expand`/`state_at`,
  built in `components/memory/tools.py`, reading the current thread from the
  langgraph-injected `RunnableConfig` — `BaseAgent._config` puts `thread_id`
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
- `README.md` is the human-facing overview (quickstart, config, API, layout);
  this file remains the deeper agent guide — keep both in sync when adding
  entrypoints/config sections/endpoints.
