# agentic

一个 AI 智能体平台 monorepo：Python 后端 + React 前端，通过 **ag-ui 协议**（SSE 流式）
通信。开箱提供流式 agentic run、多轮会话（服务端权威历史 + 分支变体）、长期记忆图谱、
知识库 RAG 检索与个人用量统计。

## 功能一览

- **流式 Agentic run**：`POST /agentic/run` 以 SSE 输出 ag-ui 事件（文本增量 / 推理过程 /
  工具调用 / 声明式 A2UI 卡片），前端经 assistant-ui 零适配渲染；支持显式取消与多模态图片输入。
- **多轮会话**：历史由服务端权威落库（SQLite / PostgreSQL），每轮从 DB 重建 LLM 上下文；
  每轮一个 turn 节点构成分支树，支持重试 / 编辑变体与活跃路径切换。
- **长期记忆**：记忆 v2 双层图谱（实体 / 双时间轴事实 / 事件），轮末自动巩固；
  聊天中快注注入 + 深召回工具，管理侧提供图谱可视化与 L1–L4 编辑维护。
- **知识库（RAG）**：文档上传（PDF / xlsx / docx / Markdown，本地解析为主、MinerU 云端兜底）
  → 分块 → bge-m3 嵌入 → Weaviate 混合检索（双通道召回 + RRF 融合），回答带 [n] 溯源引用。
- **内置智能体**：`builtin:demo` 演示助手（天气查询 + 长期记忆）、`builtin:rag` 知识库问答，
  会话可绑定切换；工具面经组件注册表装配。
- **异步任务**：Celery + Redis 承载文档摄取流水线（acks_late + 幂等 claim 门闸 + 看门狗对账）。
- **平台能力**：JWT 双令牌认证（access/refresh + 静默刷新）、个人用量统计、
  `/health` 就绪探针与 `/metrics` Prometheus 指标。

## 架构

```
┌─────────────────┐  ag-ui 事件流 (SSE) + REST   ┌──────────────────────────┐
│  frontend/      │ ◄──────────────────────────► │  backend/                │
│  React 19 + TS  │   POST /agentic/run           │  FastAPI + Celery        │
│  assistant-ui   │   /knowledge /memory /stats…  │  LangChain/LangGraph     │
└─────────────────┘                               └───────────┬──────────────┘
                                                              │
                                              ▼  四件中间件（docker compose）
                              ┌──────────┬──────────┬──────────┐
                              ▼          ▼          ▼          ▼
                          PostgreSQL  Weaviate    Redis     RustFS
                          (关系数据)  (向量检索) (队列/信号) (S3 对象存储)
```

**客户端 ⇄ 服务端契约 = ag-ui 协议**：前端 `HttpAgent`（`@ag-ui/client`）POST RunRequest 并
消费 SSE 事件流，`useAgUiRuntime` 把事件映射到 assistant-ui 的 parts 模型；其余管理功能走
普通 REST。两侧任一改动都须保持事件序列 / 格式一致。

## 仓库布局

```
agentic/
├── backend/     # Python 3.12 后端（v2 六边形布局）— 详见 backend/README.md 与 backend/AGENTS.md
│   ├── app/     #   FastAPI + Celery + LangChain/LangGraph + ag-ui
│   ├── tests/   #   单元套件（临时 SQLite + 替身，不依赖中间件）
│   ├── tests_e2e/  # 端到端套件（针对运行中服务的真实全链路）
│   └── docker-compose.yaml  # 中间件栈：postgres / weaviate / rustfs / redis
├── frontend/    # React 19 + TS + Vite 前端 — 详见 frontend/AGENTS.md
│   └── src/     #   assistant-ui 聊天 UI + 知识库 / 记忆图谱 / 用量统计管理侧
├── deploy/      # 部署视图 — 详见 deploy/README.md 与 deploy/deployment-spec.md
│   └── docker-compose.yaml  # 全栈一键拉起（中间件 + 后端 + worker + 前端）
├── docs/        # 培训材料（HTML slides）
└── AGENTS.md    # 工作区概览与跨切面契约（agent 协作指南入口）
```

## 快速开始

### 方式一：一键全栈（Docker）

```bash
cd deploy/
cp .env.example .env && vi .env   # 至少填 DEEPSEEK_API_KEY 与 AUTH_JWT_SECRET
docker compose up -d --build
```

完成后访问 **http://127.0.0.1:8081**（前端唯一用户入口，注册即用）；后端调试口
http://127.0.0.1:8000（`/health`、`/docs`）。可选：Ollama + bge-m3 支撑知识库 /
记忆的向量链路（`ollama pull bge-m3`），不可达时基础对话不受影响。

### 方式二：前后端分离开发

**中间件**（`backend/` 下，全部仅绑 127.0.0.1）：

```bash
cd backend/
docker compose up -d        # postgres + weaviate + rustfs + redis
cp .env.example .env        # 填 DEEPSEEK_API_KEY（必填）、MINERU_API_KEY（PDF 解析，可选）
```

**后端**（Python 3.12，[uv](https://docs.astral.sh/uv/) 管理）：

```bash
cd backend/
uv sync                                              # 安装依赖
uv run python -m app.cmd.admin db upgrade            # Alembic 建表（应用启动不自动建表）
uv run uvicorn app.cmd.http.main:server --reload     # HTTP 服务 → 0.0.0.0:8000
uv run celery -A app.adapters.tasking.celery_app worker   # Celery worker（知识库摄取）
uv run celery -A app.adapters.tasking.celery_app beat     # 看门狗调度（可选）
```

**前端**（Node ≥ 22，yarn）：

```bash
cd frontend/
yarn          # 安装依赖
yarn dev      # Vite dev server，默认 http://localhost:5173
```

前端默认连 `http://127.0.0.1:8000`（`VITE_API_BASE` / `VITE_SSE_URL` 可覆盖），
CORS 白名单已包含 Vite 默认端口的两个 loopback origin。

## 测试与质量

| What | CWD | Command |
| --- | --- | --- |
| 后端单元测试 | `backend/` | `uv run pytest tests/`（临时 SQLite，无需中间件） |
| 后端 e2e 测试 | `backend/` | 起服务 + 中间件后 `E2E_BASE_URL=http://127.0.0.1:8000 uv run pytest tests_e2e/` |
| 后端 lint | `backend/` | `uv run ruff check .` |
| 前端单元测试 | `frontend/` | `yarn test`（vitest，翻译器 / run 注入 / store 纯逻辑） |
| 前端 lint / 类型 / 构建 | `frontend/` | `yarn lint` / `yarn typecheck` / `yarn build` |

CI（`.github/workflows/ci.yml`）：后端 lint + pytest + Alembic 迁移门禁（PG 空库
`db upgrade` + `db check`）；前端 frozen-lockfile 安装 + lint + typecheck + test + build。

## 文档地图

| 文档 | 内容 |
| --- | --- |
| [`AGENTS.md`](AGENTS.md) | 工作区概览、前后端契约、agent shell 环境注意事项 |
| [`backend/AGENTS.md`](backend/AGENTS.md) / [`backend/README.md`](backend/README.md) | 后端六边形架构、分层依赖规则、配置项、Gotchas |
| [`frontend/AGENTS.md`](frontend/AGENTS.md) | 前端布局、路由与认证数据流、UI 栈约定、Gotchas |
| [`deploy/README.md`](deploy/README.md) / [`deploy/deployment-spec.md`](deploy/deployment-spec.md) | 一键拉起指南、部署规范（镜像/端口/变量/安全/升级） |
| [`backend/docs/memory-v2-design.md`](backend/docs/memory-v2-design.md) | 记忆 v2 双层图谱设计定稿 |
