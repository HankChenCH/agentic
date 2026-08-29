# agentic · server

`agentic` 的 Python 3.12 后端:**FastAPI + Celery + LangChain/LangGraph**,通过
[ag-ui 协议](https://docs.ag-ui.com/)(SSE 流)为 React 前端提供流式聊天能力,
并附带会话管理与知识库(RAG)的 REST API。

## 功能一览

- **流式聊天**:`POST /agentic/chat` 以 SSE 输出 ag-ui 事件(文本增量、推理过程、
  工具调用),前端零适配映射到 assistant-ui。
- **多轮会话**:历史由服务端权威落库(SQLite / PostgreSQL),每轮从 DB 重建 LLM
  上下文(含工具调用对的重放),而非信任客户端回传的 messages。
- **长期记忆**:每轮结束后由 LLM 抽取记忆存储,聊天中经 `memory_recall` 工具召回。
- **知识库(RAG)**:PDF 上传 → MinerU 云端解析 → 分块 → bge-m3 嵌入 → Weaviate
  向量检索(支持混合检索);`knowledge_list` / `knowledge_search` 双工具装配给
  LLM,先列库再自选检索。
- **异步任务**:Celery + Redis 承载文档摄取流水线,上传接口即刻返回、文档状态
  经轮询收敛。

## 技术栈

FastAPI · Celery · LangChain(`create_agent` + `stream_events`)· LangGraph ·
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

# 4. 启动 HTTP 服务
uv run uvicorn app.cmd.http.main:server --reload

# 5. 启动 Celery worker(知识库文档摄取需要;需先起 redis)
uv run celery -A app.cmd.task_executor.main worker
```

HTTP 服务监听 `0.0.0.0:8000`,聊天端点即 `http://127.0.0.1:8000/agentic/chat`。

## 命令

| 事项 | 命令(CWD = `server/`) |
| --- | --- |
| 安装/同步依赖 | `uv sync` |
| 运行 dev server | `uv run uvicorn app.cmd.http.main:server --reload` |
| 运行 Celery worker | `uv run celery -A app.cmd.task_executor.main worker` |
| 运行单元测试 | `uv run pytest tests/` |
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
| `llm.yaml` | LLM 提供方注册表(deepseek / openai 兼容网关 / ollama),按 `task_type` 区分 chat / embedding |
| `db.yaml` | 数据库,默认 `sqlite`(`data/agentic.db`),可切 `postgres` |
| `vector_db.yaml` | 向量库(weaviate)+ 顶层 `embedding` 指向 llm.yaml 的嵌入条目 |
| `filesystem.yaml` | 对象存储,默认 `rustfs`(S3 兼容),亦有 `local` 磁盘实现 |
| `document_parser.yaml` | MinerU 云端 PDF 解析(OCR / 公式 / 表格开关、轮询超时) |
| `memory.yaml` / `logging.yaml` / `task.yaml` | 记忆、日志、Celery broker/backend(驱动+key引用,指向 redis.yaml 的 entry) |
| `redis.yaml` | Redis 连接(default + providers,`standalone` 直连;取消信号存储与 Celery 队列共用,后者经 task.yaml 引用) |

常用环境变量(`.env` 或进程环境均可):

| 变量 | 用途 | 默认 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek 聊天模型 | 无,必填 |
| `MINERU_API_KEY` | MinerU PDF 解析 Bearer token | 空(首次解析报错) |
| `APP_ENV` | 运行环境(dev/test 附异常 detail+trace,prod 只回通用信息) | `dev` |
| `DB_DSN` | SQLite 数据库文件路径 | `data/agentic.db` |
| `WEAVIATE_HOST/PORT/GRPC_PORT` | Weaviate 地址 | `127.0.0.1:8080` / `50052` |
| `RUSTFS_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET` | 对象存储 | `http://127.0.0.1:9000` / `agentic` / `agentic-secret` / `agentic` |
| `REDIS_URL` | Redis 连接(取消信号存储与 Celery broker/backend 共用) | `redis://127.0.0.1:6379/0` |
| `AGENTIC_LOG_LEVEL` / `AGENTIC_LOG_DIR` | 日志级别 / 目录 | 按环境(DEBUG/INFO) / `runtime/logs` |

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
会话 1xxx、agent 2xxx、记忆 3xxx、知识库 4xxx。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/agentic/chat` | SSE 流式聊天(ag-ui 事件流) |
| `POST` | `/agentic/chat/cancel` | 取消会话当前活跃轮次(幂等;置 Redis 取消标志,流式边界与工具入口感知收口) |
| `GET` | `/agentic/conversation` | 会话列表 |
| `GET` | `/agentic/conversation/{thread_id}` | 会话详情 |
| `GET` | `/agentic/conversation/{thread_id}/history` | 会话历史消息 |
| `DELETE` | `/agentic/conversation/{thread_id}` | 删除会话(硬删除,连同轮次/消息;长期记忆保留) |
| `POST` / `GET` | `/knowledge` | 创建 / 分页列出知识库 |
| `GET` / `PATCH` / `DELETE` | `/knowledge/{kb_id}` | 知识库详情 / 更新 / 删除 |
| `POST` | `/knowledge/{kb_id}/enable` · `/disable` | 启用 / 停用 |
| `POST` | `/knowledge/{kb_id}/document` | 上传 PDF 文档(multipart,异步摄取) |
| `GET` | `/knowledge/{kb_id}/document` | 文档分页列表 |
| `GET` / `PATCH` / `DELETE` | `/knowledge/{kb_id}/document/{doc_id}` | 文档详情 / 更新 / 删除 |
| `POST` | `/knowledge/{kb_id}/document/{doc_id}/retry` · `/enable` · `/disable` | 重试 / 启停文档 |
| `GET` | `/knowledge/{kb_id}/document/{doc_id}/file` | 下载源文件 |
| `GET` / `PUT` | `/agent/{agent_id}/knowledge` | 查看 / 全量替换 agent↔知识库绑定 |

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
│   ├── services/          # 业务逻辑两层制:orchestration/(chat 行程编排)与 domain/(conversation、knowledge 领域服务)
│   ├── components/        # 自包含能力组件:memory(长期记忆)、knowledge(检索)
│   ├── agents/            # BaseAgent 注册表 + AgentFactory + 内置 agent
│   ├── tasks/             # Celery 任务(文档摄取)
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
  api 只做 HTTP 装配;services 两层制——orchestration 编排用户侧行程(chat),domain 承载
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
