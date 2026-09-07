# agentic · backend（v2 六边形布局迁移版）

`agentic` 的 Python 3.12 后端：**FastAPI + Celery + LangChain/LangGraph**，通过
[ag-ui 协议](https://docs.ag-ui.com/)（SSE 流）为 React 前端提供流式 agentic run 能力，
并附带会话管理与知识库（RAG）的 REST API。

> 本目录是 `../server/` 的**结构迁移版**：功能与行为等价（全部单测迁移并通过），
> 目录布局按六边形/整洁架构 v2 目标重构。原 `server/` 保留作迁移对照，不再演进。

## 功能一览

- **流式 Agentic run**：`POST /agentic/run` 以 SSE 输出 ag-ui 事件（文本增量、推理过程、
  工具调用），前端零适配映射到 assistant-ui。
- **多轮会话**：历史由服务端权威落库（SQLite / PostgreSQL），每轮从 DB 重建 LLM
  上下文（含工具调用对的重放），而非信任客户端回传的 messages。
- **用量统计**：`usage_record` 计量流水表（一次 LLM 调用一行，覆盖对话主链路、
  标题生成与记忆巩固）+ `GET /stats/usage/{summary,daily,records}` 个人用量查询
  （汇总/按天序列/流水分页，前端管理侧「我的用量」页消费）。
- **长期记忆**：记忆 v2 双层图谱（实体/双时间轴事实/事件），轮末巩固落库，
  聊天中快注注入 + 深召回三工具。
- **知识库（RAG）**：文档上传（PDF / xlsx / docx，按后缀路由解析器）→ MinerU 云端 /
  本地库解析 → 分块 → bge-m3 嵌入 → Weaviate
  混合检索（双通道召回 + RRF 融合）；`knowledge_list` / `knowledge_search` 检索双工具
  与 `knowledge_context` / `document_read` / `document_list` 定位读取三工具装配给 LLM。
- **内置智能体**：`builtin:demo` 演示助手（天气查询 + 长期记忆）、`builtin:rag`
  知识库问答（自建 StateGraph，回答带 [n] 溯源引用）——`AGENTIC_DEFAULT_AGENT_ID` 可切换。
- **异步任务**：Celery + Redis 承载文档摄取流水线（acks_late + 幂等 claim 门闸 +
  看门狗对账），上传接口即刻返回、文档状态经轮询收敛。

## v2 布局

```text
app/
├── cmd/                      # 可执行入口——纯启动器
│   ├── http/                 #   create_app + uvicorn + 中间件/路由接线 + lifespan
│   ├── task_executor/        #   Celery worker 启动器（容器/wireup/信号挂钩）
│   └── admin/                #   Typer 复合入口（命令体在 commands/）
├── api/                      # 驱动适配器① HTTP/SSE（限流归网关层，应用内无 429）
├── tasks/                    # 驱动适配器② Celery 任务体（process_document、看门狗）
├── commands/                 # 驱动适配器③ 维护 CLI 命令体（memory/human-agent/db）
├── application/              # 用例层（入口侧唯一消费面）：agentic_service、
│   │                         #   turn_finalizer、translator/（ag-ui 映射唯一归属地）、
│   │                         #   agent_catalog / tool_catalog + 管理面一域一门面
│   │                         #   *AppService（auth/conversation/knowledge/memory/usage/
│   │                         #   human_agent/ingestion）
├── domain/                   # 领域层：按聚合分包，零 adapters/框架机制依赖
│   ├── ports/                #   跨聚合基础契约：Filesystem、DocumentParser+Parsed*、
│   │                         #   LLM（ChatModel/ChatModelGateway）、
│   │                         #   RepositoryConflictError（仓储冲突的契约级信号）
│   ├── conversation/         #   领域服务 + ports.py（ConversationRepositoryPort）
│   │                         #   + attachments/branching/multimodal/signals/title
│   ├── knowledge/            #   领域服务 + ports.py（KB/Doc 仓储端口、
│   │                         #   KnowledgeVectorIndexPort/VectorHit）
│   ├── memory/               #   领域服务 + ports.py（MemoryEditor/MemoryGraphReader/
│   │                         #   MemoryGraphRepositoryPort/MemoryMaintenancePort/
│   │                         #   MemoryVectorIndexPort）+ vocab.py（规范文本唯一源）
│   └── user/                 #   领域服务 + ports.py（UserRepositoryPort、UserNodeSyncPort）
├── adapters/                 # 被驱动适配器：实现 domain 端口 + 持有机制
│   ├── persistence/          #   7 个仓储（conversation/user/kb/document/usage/
│   │                         #   human_agent/memory_graph+memory_maintenance），
│   │                         #   @injectable(as_type=<领域端口>)；驱动异常
│   │                         #   （IntegrityError）在此翻译为 RepositoryConflictError
│   ├── tasking/              #   celery_app 实例 + 队列 conf（tasks/与发送方共用）
│   ├── llm/                  #   ModelFactory + capabilities + DefaultChatModelGateway
│   ├── vector/               #   Weaviate 工厂 + 知识/记忆向量索引实现
│   │                         #   （knowledge_index/memory_index + 各自 collection schema）
│   ├── db/  redis/           #   引擎/客户端工厂（sqlite/postgresql、standalone redis）
│   ├── filesystem/           #   local/s3 实现（Filesystem 契约归 domain/ports）
│   └── document_parser/      #   mineru_cloud（云端 PDF）+ local_office（xlsx/docx）
│                             #   实现 + 按后缀路由（解析契约归 domain/ports）
├── components/               # 能力组件（manifest/ability/internal/admin 范式；
│   │                         #   不自持存储——数据访问经 domain 端口由 persistence 实现）
│   ├── knowledge/            #   retrieval（RRF 双通道检索编排）+ navigation
│   ├── memory/               #   抽取/巩固/召回/编辑（admin 回填 MemoryEditor 等端口）
│   └── demo/
├── agents/                   # base（build_graph 扩展点）、middleware（动态 system prompt）、
│   │                         # cancel（取消守卫）、toolbox、builtin/{demo,rag}
├── packages/                 # 领域无关能力库（signal/），禁向上
├── models/
│   ├── schema/               # 线格式（camelCase）
│   └── domain/               # SQLModel 表模型 = 全系统共享数据形状（既定取舍：
│                             #   实体即表不拆映射层；业务不变量只在领域服务强制）
├── core/                     # config / logging / exceptions / container（组装根保留原位）
└── exceptions/               # 业务异常 1xxx–6xxx
```

## 依赖箭头表（单向，`tests/test_layer_boundaries.py` AST 强制）

```
cmd ──► 全部
api ──► application                     tasks ──► application（+ adapters.tasking）
commands ──► application
application ──► {domain, components, agents}
domain ──► models/domain              ← 唯一下向依赖；零 adapters/供应商 SDK
components ──► domain 端口              （纯算法引擎，零机制依赖）
agents ──► components
adapters ──► {domain 端口（实现）, models, core/config}
packages ──► {core, adapters}
```

入口侧（api/tasks/commands）只消费 application 用例层（`*AppService` 门面；
任务派发经 domain 的 `IngestionDispatcher` 端口反转）——机制面豁免
（commands/db.py 的 Alembic 薄封装）在守卫测试 `EXEMPTED_IMPORTS` 登记制。

供应商红线：`domain`/`application`/`components` 禁 import weaviate / redis /
obstore / sqlalchemy / sqlmodel——机制只住 adapters（AST 扫描强制）。
langchain 消息信封类型（AIMessage 等）是领域侧允许的框架白名单。

## 端口反转约定

- 协议（`Protocol`/ABC）住 domain（`domain/ports/` 或各聚合 `ports.py`），
  实现住 adapters，经 wireup `@injectable(as_type=<Port>)` 回填；domain 与
  components 的构造注入一律注协议类型。
- 仓储方法的驱动级冲突（唯一约束并发窗口）在适配器内翻译为
  `domain.ports.RepositoryConflictError`，领域捕获后映射为业务异常——驱动
  异常不出适配器边界。

## 迁移对照（server → backend 路径映射）

| server（旧） | backend（v2） |
| --- | --- |
| `app/services/orchestration/` | `app/application/` |
| `app/services/domain/` | `app/domain/` |
| `app/repositories/` | `app/adapters/persistence/` |
| `app/infrastructures/{llm,db,redis,vector,filesystem,document_parser}/` | `app/adapters/<同名>/` |
| `app/services/domain/{knowledge,memory}/vector_index.py` | `app/adapters/vector/{knowledge_index,memory_index}.py`（端口在 domain） |
| `app/services/domain/{knowledge,memory}/collection.py` | `app/adapters/vector/{knowledge_collection,memory_collection}.py` |
| `app/infrastructures/filesystem/filesystem_provider.py` 的 `Filesystem` ABC | `app/domain/ports/filesystem.py` |
| `app/infrastructures/document_parser/{document_parser_provider,models}.py` 契约部分 | `app/domain/ports/parsing.py` |
| `app/cmd/task_executor/main.py` 的 celery_app + conf | `app/adapters/tasking/app.py` |
| `app/services/domain/conversation/title_generator.py` 直用 ModelFactory | 经 `ChatModelGateway` 端口（实现 `adapters/llm/gateway.py`） |

## 技术栈

FastAPI · Celery · LangChain（`create_agent` + `stream_events`）· LangGraph ·
SQLModel · wireup（DI）· loguru · ag-ui-protocol · Weaviate（langchain-weaviate）·
DeepSeek（默认聊天模型）· Ollama（本地嵌入 bge-m3）· MinerU（云端 PDF 解析）·
rustfs/obstore（S3 兼容对象存储）· uv（包管理）

## 命令

CWD = `backend/`（agent 非交互 shell 先 `export PATH="$HOME/.local/bin:$PATH"`）：

- 安装：`uv sync`
- HTTP：`uv run uvicorn app.cmd.http.main:server --reload`（默认 0.0.0.0:8000）
- Worker：`uv run python -m app.cmd.task_executor [--pool=solo]`
- 维护 CLI：`uv run python -m app.cmd.admin <域> <命令>`
- 迁移：`uv run python -m app.cmd.admin db upgrade`
- 单测：`uv run pytest tests/`（当前 460 项全绿；纯单元级，SQLite 临时库 + 替身，不碰中间件）
- Lint：`uv run ruff check .`
