# agentic 部署视图（Deployment View）

一键拉起整套 agentic（中间件 + 后端 + 后台任务 + 前端）的部署目录。

| 文件 | 说明 |
| --- | --- |
| [`docker-compose.yaml`](docker-compose.yaml) | 全栈编排（唯一入口）：中间件四件 include 自 `server/docker-compose.yaml`，外加迁移、HTTP、worker、beat、前端 |
| [`deployment-spec.md`](deployment-spec.md) | **部署规范**：镜像/端口/环境变量/数据卷/健康检查/安全基线/升级回滚的正式口径，改部署相关内容前必读 |
| [`.env.example`](.env.example) | 环境变量模板，`cp .env.example .env` 后按需填写 |

> 本目录编排的镜像由两侧仓库各自的 `Dockerfile` 构建（`server/Dockerfile`、
> `client/agentic-client/Dockerfile`），本目录不含镜像定义。

## 前置条件

1. **Docker**（含 compose v2；Linux 宿主机已由编排自动补
   `host.docker.internal` 映射）。
2. **`DEEPSEEK_API_KEY`**（必填）——LLM 服务密钥。
3. **Ollama + bge-m3**（推荐）——记忆沉淀与知识库检索的 embedding 模型：
   `ollama pull bge-m3`。不可达时基础对话不受影响，相关功能降级失败。
4. **dev 栈已停止**（见下文「与 dev 栈的关系」）。

## 一键拉起

```bash
cd deploy/
cp .env.example .env && vi .env   # 至少填 DEEPSEEK_API_KEY 与 AUTH_JWT_SECRET（缺一 compose 拒绝启动）
docker compose up -d --build
```

首次拉起自动完成：构建两个镜像 → 拉起中间件（等健康）→ rustfs 建桶 →
Alembic 建表 → 起后端/worker/beat → 前端等后端健康后启动。

完成后访问 **http://127.0.0.1:8081**（前端，唯一用户入口；首个用户在登录页
注册即用）。后端直连调试口在 http://127.0.0.1:8000（`/health`、`/metrics`、
`/docs`）。

### 验证清单

```bash
docker compose ps                       # 全部 Up（server/client 应 healthy）
curl -s http://127.0.0.1:8000/health    # {"error_code":0,...,"db":"up","redis":"up"}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8081/   # 200
```

浏览器注册账号 → 发一条消息得到回复（验证 LLM + SSE）→
管理侧建知识库传 PDF（验证对象存储 + worker + MinerU + 向量链路，需
`MINERU_API_KEY` 与 Ollama）。

## 日常运维

| 操作 | 命令 |
| --- | --- |
| 停止（保留数据） | `docker compose down` |
| **清空全部数据** | `docker compose down -v`（删数据卷，慎用） |
| 看日志 | `docker compose logs -f [server\|worker\|beat\|client\|...]` |
| 重跑迁移（幂等） | `docker compose run --rm migrate` |
| 扩容 worker | `docker compose up -d --scale worker=2`（server/beat 禁扩） |
| 升级到最新代码 | 两侧仓库 `git pull` 后 `docker compose up -d --build` |

## 服务与端口

| 入口 | 地址 | 说明 |
| --- | --- | --- |
| 前端 | `http://127.0.0.1:8081`（`WEB_PORT`） | 页面 + `/api` 同源反代，免 CORS |
| 后端调试口 | `http://127.0.0.1:8000`（`SERVER_PORT`） | `/health` `/metrics` `/docs` |
| 自建 ESM CDN（可选） | `http://127.0.0.1:8082`（`ESM_SH_PORT`） | profile `esm-sh`；react 全家 importmap 源，上游 npmmirror |
| 中间件 | postgres 5432 / weaviate 8080 / rustfs 9000 / redis 6379 | 全部仅绑 `127.0.0.1`，排查用 |

完整口径（镜像、变量、数据卷、安全基线、远程部署变体）见
[`deployment-spec.md`](deployment-spec.md)。

## 可选：自建 ESM CDN（esm.sh + 阿里云 npmmirror 包源）

前端产物里的 react 全家（react/react-dom/react-router）经 importmap 从 ESM
CDN 加载（入口 chunk 因此从 1.5MB 降到 147KB，见 `frontend/AGENTS.md`），
**缺省指向 esm.sh 官方公网源**。内网隔离或国内加速场景，可启用本目录编排的
自建服务：esm.sh 官方 Docker 镜像 + 阿里云 npmmirror 作上游包源。

启用（两步，均在本目录执行）：

```bash
# 1) 启动自建 CDN（profile 化，不影响默认栈；首次访问各依赖时构建并缓存）
docker compose --profile esm-sh up -d esm-sh
curl -sf http://127.0.0.1:8082/react@19.2.7 >/dev/null && echo esm-sh ok

# 2) 前端指向它并重建（CDN_BASE 是构建期事实，改 = 重建前端镜像）
cp .env.example .env   # 或编辑既有 .env：取消 FRONTEND_CDN_BASE 注释
vi .env                # FRONTEND_CDN_BASE=http://127.0.0.1:8082
docker compose build frontend && docker compose up -d frontend
```

要点：

- **地址必须浏览器可达**：importmap 由用户浏览器解析，`FRONTEND_CDN_BASE`
  要填浏览器侧能访问到的地址（远程部署用部署机 IP/域名，勿用 127.0.0.1）；
  这与 `RUSTFS_PUBLIC_ENDPOINT` 同一口径。
- **npmmirror 的角色**：它是 npm 原文件镜像（react 包是纯 CJS，浏览器无法
  直接当 ESM 模块加载），只能作自建 esm.sh 的上游包源
  （`ESM_SH_NPM_REGISTRY`，缺省即 npmmirror），**不能**直接填进
  `FRONTEND_CDN_BASE`。
- **缓存与升级**：esm.sh 对每个「包@版本」构建一次即缓存进 `esm-sh-data`
  卷；升级前端依赖版本后新版本会自动构建，无需干预。删卷 =
  清空缓存全量重建。
- **回退**：`.env` 去掉 `FRONTEND_CDN_BASE` 重建前端即回到 esm.sh 官方源；
  彻底离线（要求不依赖任何外部 CDN）时，删 `frontend/vite.config.ts` 的
  `cdnExternals()` 全量打包（见 `frontend/AGENTS.md`「基础依赖 CDN 外置」）。
- 正式口径（镜像/数据卷/协议约束）见
  [`deployment-spec.md`](deployment-spec.md) §12.5。

## 与 dev 栈的关系

- 本栈项目名 `agentic-stack`、数据卷前缀 `agentic-stack_*`；`server/` 的
  dev 中间件栈项目名 `agentic`——**两栈数据互相独立**，切换运行互不丢数据。
- 两栈共用容器名与宿主端口，**不能同时运行**：拉起本栈前先
  `cd ../server && docker compose down`；回 dev 栈前先
  `docker compose down`（本目录）。

## 故障排查

| 现象 | 处置 |
| --- | --- |
| `up` 报端口占用 | dev 中间件栈或本地 dev server 未停（5432/6379/8080/8000 冲突）；或 8081 被占，`.env` 改 `WEB_PORT` |
| compose 解析期报 `DEEPSEEK_API_KEY` | `.env` 未填必填项 |
| server 反复 503 | `curl :8000/health` 看组件明细；`docker compose logs server`；`APP_ENV=dev` 排障（规范 §4） |
| 对话能聊但记忆不沉淀 / 知识库检索失败 | 宿主 Ollama 未起或没拉 bge-m3；Linux 宿主机需 `OLLAMA_HOST=0.0.0.0`（规范 §12.1） |
| 知识库 PDF 解析报错 | `.env` 未填 `MINERU_API_KEY` |
| 远程机器打不开图片附件 | `RUSTFS_PUBLIC_ENDPOINT` 仍是 127.0.0.1，改成浏览器可达地址（规范 §12.2） |
| 迁移失败 | `docker compose logs migrate`；基线迁移单事务失败会整体回滚，修完直接重跑 |

## 已验证范围（2026-09-02，本机 macOS + Docker Desktop）

镜像构建 → 全栈拉起（含 Alembic 迁移建表）→ `/health` 组件全绿 → 前端
200 → 经 nginx 反代注册/登录/智能体目录 → SSE 对话流（DeepSeek 真实推理流
逐帧到达）→ 停止与数据卷清理。迁移曾暴露并修复一个基线脚本问题：
`agentic_conversation_message` 的自引用外键在 PostgreSQL 下要求 `message_id`
唯一索引（SQLite 不校验所以 dev 未暴露），已在该迁移内以「先建唯一索引、
后补挂外键」修复，模型同步加 `unique/index` 声明。
