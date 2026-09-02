# agentic · server

`agentic` 的 Python 3.12 后端:**FastAPI + Celery + LangChain/LangGraph**,通过
[ag-ui 协议](https://docs.ag-ui.com/)(SSE 流)为 React 前端提供流式 agentic run 能力,
并附带会话管理与知识库(RAG)的 REST API。

## 功能一览

- **流式 Agentic run**:`POST /agentic/run` 以 SSE 输出 ag-ui 事件(文本增量、推理过程、
  工具调用),前端零适配映射到 assistant-ui。
- **多轮会话**:历史由服务端权威落库(SQLite / PostgreSQL),每轮从 DB 重建 LLM
  上下文(含工具调用对的重放),而非信任客户端回传的 messages。
- **长期记忆**:每轮结束后由 LLM 抽取记忆存储,聊天中经 `memory_recall` 工具召回。
- **知识库(RAG)**:PDF 上传 → MinerU 云端解析 → 分块 → bge-m3 嵌入 → Weaviate
  向量检索(支持混合检索);`knowledge_list` / `knowledge_search` 检索双工具与
  `knowledge_context` / `knowledge_document_read` / `knowledge_document_list`
  定位读取三工具装配给 LLM:先列库再自选检索,命中片段可按 doc_id + position
  看邻域、整篇通读或浏览库内文档清单。
- **builtin:rag 图智能体**:自建 LangGraph 状态图的 RAG 专用智能体(非工具循环):
  查询理解(闲聊/知识分路 + 多轮凝练)→ 混合检索(分路直达,无需 LLM 自选工具)→
  相关性过滤 → 带 [n] 引用生成,未命中自动改写重试一次后如实兜底;检索步骤以
  工具事件流式呈现(`AGENTIC_DEFAULT_AGENT_ID=builtin:rag` 启用)。
- **异步任务**:Celery + Redis 承载文档摄取流水线,上传接口即刻返回、文档状态
  经轮询收敛。

## 技术栈

FastAPI · Celery · LangChain(`create_agent` + `stream_events`，自建图智能体
经 `BaseAgent.build_graph()` 扩展点)· LangGraph ·
SQLModel · wireup(DI)· loguru · ag-ui-protocol · Weaviate(langchain-weaviate)·
DeepSeek(默认聊天模型)· Ollama(本地嵌入 bge-m3)· MinerU(云端 PDF 解析)·
rustfs/obstore(S3 兼容对象存储)· uv(包管理)

## 环境要求

- Python 3.12,依赖由 [uv](https://docs.astral.sh/uv/) 管理
- Docker(中间件栈:postgres / weaviate / rustfs / redis)
- [Ollama](https://ollama.com/) 并拉取嵌入模型:`ollama pull bge-m3`
  (知识库向量化必需;不用知识库可不装)
- DeepSeek API Key(默认聊天模型必需)
- MinerU API Key(知识库 PDF 解析必需,不用知识库可不配)

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量(项目根 .env,最小可用只需 DEEPSEEK_API_KEY)
#    DEEPSEEK_API_KEY=sk-...          # 必需,默认聊天模型
#    MINERU_API_KEY=...               # 知识库 PDF 解析(可选)
#    其余变量均有面向本地开发的默认值,见下方「配置」

# 3. 启动中间件(知识库需要;纯聊天只需默认 SQLite,可跳过)
docker compose up -d

# 4. 数据库迁移(建表/升级,由 Alembic 管理;库指向由 db.yaml/SQLITE_DB_PATH 决定)
uv run python -m app.cmd.admin db upgrade

# 5. 启动 HTTP 服务
uv run uvicorn app.cmd.http.main:server --reload

# 6. 启动 Celery worker(知识库文档摄取需要;需先起 redis)
uv run celery -A app.cmd.task_executor.main worker

# 7. 启动 Celery beat(看门狗调度:卡死文档对账重投;可选,不跑则无自动恢复)
uv run celery -A app.cmd.task_executor.main beat
```

HTTP 服务监听 `0.0.0.0:8000`,run 端点即 `http://127.0.0.1:8000/agentic/run`。

## 命令

| 事项 | 命令(CWD = `server/`) |
| --- | --- |
| 安装/同步依赖 | `uv sync` |
| 运行 dev server | `uv run uvicorn app.cmd.http.main:server --reload` |
| 运行 Celery worker | `uv run celery -A app.cmd.task_executor.main worker` |
| 运行 Celery beat(看门狗调度) | `uv run celery -A app.cmd.task_executor.main beat` |
| 运行单元测试 | `uv run pytest tests/` |
| 数据库迁移(Alembic) | `uv run python -m app.cmd.admin db upgrade`(另有 `downgrade` / `revision` / `current` / `history` / `stamp`) |
| 启动/停止中间件 | `docker compose up -d` / `docker compose down` |
| 清空中间件数据 | `docker compose down -v` |

两个入口也都有 argparse 形式(以模块方式运行,不要当脚本跑):

```bash
uv run python -m app.cmd.http [--reload] [--config-dir PATH] [--env-file PATH]
uv run python -m app.cmd.task_executor [--pool=solo]   # 额外参数透传给 celery worker
```

## 配置

配置不散读环境变量,统一收敛在 `app/configs/*.yaml`,由 `core/config/loader.py`
加载并做 docker-compose 风格的环境变量插值(`${VAR}` 必填、`${VAR:default}` 兜底、
`$$` 转义字面量)。取值优先级:**进程环境变量 > `.env` > YAML 内联默认值**;缺失
的必填变量在启动时 fail-fast。

| 文件 | 内容 |
| --- | --- |
| `app.yaml` | 应用名、运行环境(`APP_ENV: dev/test/prod`)、默认 agent |
| `http.yaml` | HTTP 边缘策略:CORS 源白名单与请求体大小上限(限流不在应用内,由网关层实现) |
| `llm.yaml` | LLM 提供方注册表(deepseek / openai 兼容网关 / ollama),按 `task_type` 区分 chat / embedding;每个 entry 可配 `timeout`(单次请求超时,秒,默认 120)与 `capabilities`(能力声明:`thinkable` + `features`——支持的 `with_structured_output` method 白名单,声明顺序即自发现优先级;强制 tool_choice 通道与 DeepSeek 思考模式互斥,由 `ThinkingAwareChatDeepSeek` 自动关思考,json_mode 思考兼容) |
| `db.yaml` | 数据库,默认 `sqlite`(`data/agentic.db`),可切 `postgres` |
| `vector_db.yaml` | 向量库(weaviate)+ 顶层 `embedding` 指向 llm.yaml 的嵌入条目 |
| `filesystem.yaml` | 对象存储,默认 `rustfs`(S3 兼容),亦有 `local` 磁盘实现 |
| `document_parser.yaml` | MinerU 云端 PDF 解析(OCR / 公式 / 表格开关、轮询超时) |
| `memory.yaml` / `logging.yaml` / `task.yaml` | 记忆、日志、Celery broker/backend(驱动+key引用,指向 redis.yaml 的 entry)+ 任务 `time_limit`/`soft_time_limit`(硬/软超时,默认 600/540 秒) |
| `redis.yaml` | Redis 连接(default + providers,`standalone` 直连;取消信号存储与 Celery 队列共用,后者经 task.yaml 引用);可配 `socket_timeout`(命令读写,默认 5 秒)与 `socket_connect_timeout`(建连,默认 3 秒) |
| `auth.yaml` | 认证:JWT 签发/验签(`AUTH_JWT_SECRET`、算法 HS256、`token_expire_minutes`,默认 7 天) |

常用环境变量(`.env` 或进程环境均可):

| 变量 | 用途 | 默认 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 聊天模型 | 无,必填 |
| `MINERU_API_KEY` | MinerU PDF 解析 Bearer token | 空(首次解析报错) |
| `APP_ENV` | 运行环境(dev/test 附异常 detail+trace,prod 只回通用信息) | `dev` |
| `SQLITE_DB_PATH` | SQLite 数据库文件路径 | `data/agentic.db` |
| `WEAVIATE_HOST/PORT/GRPC_PORT` | Weaviate 地址 | `127.0.0.1:8080` / `50052` |
| `RUSTFS_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET` | 对象存储 | `http://127.0.0.1:9000` / `agentic` / `agentic-secret` / `agentic` |
| `REDIS_URL` | Redis 连接(取消信号存储与 Celery broker/backend 共用) | `redis://127.0.0.1:6379/0` |
| `AGENTIC_DEFAULT_AGENT_ID` | 会话默认智能体(当前内置:`builtin:demo` 通用助手、`builtin:rag` 知识库 RAG 图智能体) | `builtin:demo` |
| `CORS_ORIGINS` | CORS 源白名单(逗号分隔整体覆盖) | `http://localhost:5173,http://127.0.0.1:5173` |
| `AUTH_JWT_SECRET` | JWT 签名密钥(HS256 建议 ≥32 字节;生产必须显式设置) | `dev-only-secret-…`(仅开发) |
| `RUN_MAX_BODY_BYTES` / `UPLOAD_MAX_BODY_BYTES` | run / 上传请求体上限(字节) | 1 MiB / 64 MiB |
| `APP_LOG_LEVEL` / `APP_LOG_DIR` | 日志级别 / 目录 | 按环境(DEBUG/INFO) / `runtime/logs` |
| `METRICS_ENABLED` / `METRICS_WORKER_PORT` | 指标采集开关 / worker 指标端口 | `true` / `9091` |

配置目录与 `.env` 路径可整体覆盖:`AGENTIC_CONFIG_DIR`、`AGENTIC_ENV_FILE`
(或 `python -m app.cmd.http --config-dir/--env-file`)。

## 中间件栈(docker-compose.yaml)

全部只绑定 `127.0.0.1`,凭据可经环境变量覆盖(见文件头):

| 服务 | 地址 | 凭据 | 用途 |
| --- | --- | --- | --- |
| postgres 18 | `127.0.0.1:5432` | `agentic` / `agentic` / db `agentic` | 可选数据库(默认用 SQLite) |
| weaviate 1.39 | `127.0.0.1:8080`(HTTP)+ `:50052`(gRPC,宿主侧) | 匿名访问 | 知识库向量存储 |
| rustfs(S3 兼容) | `127.0.0.1:9000` | `agentic` / `agentic-secret` | 知识库文件存储(bucket `agentic` 由 `rustfs-init` 幂等创建) |
| redis 8 | `127.0.0.1:6379` | — | Celery broker / 结果库 + 取消标志存储(独立 `redis.yaml`) |

## API 一览

所有成功响应走统一信封 `{error_code, error_message, response}`;业务错误码分段:
会话 1xxx、agent 2xxx、记忆 3xxx、知识库 4xxx、用户 5xxx。每个响应带 `X-Request-ID`
头(沿用入站同名头,否则生成),同 ID 注入该请求全部日志(`request_id` 字段)。

**认证**:注册/登录签发 JWT(HS256),除 `/health`、`/auth/*` 与 `/metrics` 外,
全部路由要求 `Authorization: Bearer <token>`(router 级依赖声明式挂载,无路径白名单)。
会话、记忆与知识库是用户级数据:会话/记忆归属过滤在仓储查询条件内强制;知识库
带属主与公开/私有标识(`isPublic`,缺省私有)——读(详情/列表/文档读)属主或
公开库可见,写(更新/删除/启停/上传/文档写)仅属主可操作,越权访问与不存在同返回 404。

边缘策略:CORS 收敛为源白名单(默认仅 Vite dev server 两种 loopback 形态,
`CORS_ORIGINS` 覆盖,中间件为纯 ASGI、SSE 友好);**限流不在应用内实现,
由网关层(反向代理/API 网关)按部署策略实现**——应用内限流(原 fastapi-limiter
端点依赖方案)已于 2026-08-31 移除,原因是其与 FastAPI 0.139 新路由机制
(`_IncludedRouter`)不兼容,决定外移而非修补,应用不再产生 429;run 与
文档上传仍受请求体字节上限约束(Content-Length 超限直回 413,分块/谎报
长度在读取处拦截),上传转存为流式——边写对象存储边计数与 sha256 摘要,
内存占用与文件大小无关。

**运维监控(Prometheus)**:HTTP 应用暴露 `GET /metrics`(公开,抓取格式),
中间件按「方法 + 路由模板」记请求计数 `agentic_http_requests_total`、时延
直方图 `agentic_http_request_duration_seconds` 与在途数
`agentic_http_requests_in_progress`(404 等未匹配路由落 `unmatched`,
`/metrics`/`/health` 自身不计数)。Celery worker 在独立端口(默认 9091)
暴露任务指标 `agentic_celery_tasks_total{task,state}`、
`agentic_celery_task_duration_seconds{task}`、
`agentic_celery_tasks_currently_running`(multiprocess 模式聚合 prefork
子进程)。`METRICS_ENABLED=false` 整体关闭。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 就绪探针(逐一探测 db/redis,全绿 200,任一不可用 503 + 组件明细) |
| `GET` | `/metrics` | Prometheus 采集端点(公开;HTTP 请求计数/时延,`METRICS_ENABLED=false` 关闭) |
| `POST` | `/auth/register` | 注册(注册即登录:成功直接返回 token + 用户信息;重名 → 5001/409) |
| `POST` | `/auth/login` | 登录(错用户名/错密码统一 5002/401,不泄露用户存在性) |
| `GET` | `/auth/me` | 当前登录用户 |
| `POST` | `/agentic/run` | SSE 流式 agentic run(ag-ui 事件流) |
| `POST` | `/agentic/run/cancel` | 取消会话当前活跃轮次(幂等;触发 Redis 取消信号,流式边界与工具入口感知收口) |
| `GET` | `/agentic/conversation` | 会话列表 |
| `GET` | `/agentic/conversation/{thread_id}` | 会话详情 |
| `GET` | `/agentic/conversation/{thread_id}/history` | 会话历史消息 |
| `DELETE` | `/agentic/conversation/{thread_id}` | 删除会话(硬删除,连同轮次/消息;长期记忆保留) |
| `GET` | `/agentic/agents` | 智能体目录(id/展示名/描述/是否支持图片,默认智能体排首位;前端选择 UI 消费,选中项经 run 请求 `forwardedProps.agentId` 上送绑定/切换会话智能体) |
| `GET` | `/agentic/tool-catalog` | 工具能力目录(组件 → 工具的名/中文展示标题/描述/参数 schema,供前端 UI 标识化) |
| `POST` / `GET` | `/knowledge` | 创建(可带 `isPublic`,缺省私有) / 分页列出知识库(属主或公开库) |
| `GET` / `PATCH` / `DELETE` | `/knowledge/{kb_id}` | 知识库详情 / 更新 / 删除 |
| `POST` | `/knowledge/{kb_id}/enable` · `/disable` | 启用 / 停用 |
| `POST` | `/knowledge/{kb_id}/document` | 上传 PDF 文档(multipart,异步摄取) |
| `GET` | `/knowledge/{kb_id}/document` | 文档分页列表 |
| `GET` / `PATCH` / `DELETE` | `/knowledge/{kb_id}/document/{doc_id}` | 文档详情 / 更新 / 删除 |
| `POST` | `/knowledge/{kb_id}/document/{doc_id}/retry` · `/enable` · `/disable` | 重试 / 启停文档 |
| `GET` | `/knowledge/{kb_id}/document/{doc_id}/file` | 下载源文件 |
| `GET` | `/memory/graph` | 记忆图快照(`at` 做时点回放,含已取代历史) |
| `POST` | `/memory/statements` | 手工补充事实(origin=MANUAL,抽取裁决恒不取代) |
| `PATCH` / `DELETE` | `/memory/statements/{ref}` | 取代式纠正(旧行 SUPERSEDED 新行接续) / 归档(软删可回放) |
| `PATCH` | `/memory/entities/{ref}` | 实体改名/别名全量替换/类型修改 |
| `POST` | `/memory/entities/{ref}/merge` | 错分离合并:全量并入目标(含历史行)后删除本实体 |
| `POST` | `/memory/entities/{ref}/split` | 错合并拆分:所选事实/参与/别名迁往新实体,双方写拆分禁令 |
| `DELETE` | `/memory/entities/{ref}` | 孤立实体清理(无任何事实引用与事件参与;用户节点受保护) |
| `PATCH` / `DELETE` | `/memory/episodes/{ref}` | 事件档案直改(摘要/场景/时间) / 物理删除(含参与,不可恢复) |
| `PATCH` | `/memory/episode-links/{ref}` | 参与改挂:换实体/改角色(撞唯一组合 → 3007) |
| `GET` | `/memory/maintenance/purge-preview` | 清除影响面预览(scope=day 用 from/to;scope=thread 用 threadId) |
| `POST` | `/memory/maintenance/purge` | 范围清除:在效事实归档(可回放)、事件物理删除、孤立实体清理 |
| `GET` | `/memory/maintenance/export` | 四表全量导出 JSON 备份(含历史行) |
| `POST` | `/memory/maintenance/reset` | 整体重置(confirmation 须为「重置」;清空四表+重建向量) |

## 测试

```bash
uv run pytest tests/
```

纯单元级:SQLite 临时库 + stub 向量索引,不需要起 Weaviate / MinerU / Ollama /
Redis,也不消耗任何 API 额度。

## 项目结构

```
server/
├── app/
│   ├── cmd/               # 入口:http(FastAPI,async 容器)、task_executor(Celery,sync 容器)
│   ├── api/               # HTTP 装配 + 全局异常处理(AOP);v1/endpoints/ 按领域分模块
│   ├── services/          # 业务逻辑两层制:orchestration/(run 行程编排)与 domain/(conversation、knowledge 领域服务)
│   ├── components/        # 自包含能力组件:memory(长期记忆)、knowledge(检索)
│   ├── packages/          # 可复用能力库:signal(SignalStore 分布式 Event 契约 + Redis/InMemory 后端)
│   ├── agents/            # BaseAgent 注册表 + AgentFactory + 内置 agent
│   ├── tasks/             # Celery 任务(文档摄取)
│   ├── commands/          # admin 命令行的领域命令(memory repair/rebuild-index、db 迁移)
│   ├── repositories/      # 数据访问(SQLModel 会话)
│   ├── infrastructures/   # 共享驱动工厂:llm / db / vector / filesystem / document_parser
│   ├── models/            # schema(线格式,camelCase)+ domain(SQLModel 表)
│   ├── core/              # 配置加载、日志(loguru)、异常基线、wireup 容器
│   └── configs/           # 各节 YAML 配置
├── tests/                 # 单元测试
└── docker-compose.yaml    # 中间件栈
```

## 架构速览

- **分层**:`api → services{orchestration|domain} → components/repositories → infrastructures`,单向依赖;
  可复用能力库 `packages`(signal 信号库:契约+后端内聚一包)领域无关、禁向上依赖,被 services/components
  向下消费;api 只做 HTTP 装配;services 两层制——orchestration 编排用户侧行程(run),domain 承载
  领域服务(会话/知识库,管理侧端点与 Celery 任务直调);自包含能力(记忆、检索)在 components,
  写入型组件工具须经领域服务;检索组件消费领域向量适配器,走合法的 components→domain 边。
- **DI**:wireup。共享注册在 `app/core/container.py`,HTTP 与 Celery 两个入口各建
  各自的容器实例(FastAPI 集成要求 async 容器,Celery 集成要求 sync 容器)。
- **LLM 栈**:LangChain `create_agent` + `stream_events(version="v3")`,默认聊天
  模型 DeepSeek(`ChatDeepSeek`),由 `infrastructures/llm` 按 `llm.yaml` 构建;
  新增提供方 = 写一个 `ModelBuilder` 子类并 `@register`。
- **错误体系**:内置/框架(`core/exceptions`)/业务(`app/exceptions`)三层,全局
  handler 统一转信封;dev/test 附 detail + trace,prod 收敛为通用信息,日志始终带全栈。

更完整的开发约定、分层规则与坑位说明见 [`AGENTS.md`](AGENTS.md)。
