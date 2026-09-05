# agentic 部署规范（Deployment View Specification）

> 部署视图的正式规范。面向负责把 `agentic` 部署到一台机器 / 一套环境上的人；
> 快速上手看 [`README.md`](README.md)（一键拉起指南），架构与开发约定见各仓库
> `AGENTS.md`。本文与 `docker-compose.yaml` 共同构成部署的单一事实源：改拓扑、
> 端口、变量口径时，两处必须同步。

- 适用范围：单机 Docker Compose 全栈部署（本目录 `docker-compose.yaml`）；
  形态变体（跨域直连、远程部署、反代/TLS）在 §12。
- 约定：端口/变量/镜像名均为本目录 compose 的缺省值，可经 `deploy/.env` 覆盖
  （模板见 [`.env.example`](.env.example)）。

---

## 1. 部署单元与镜像规范

| 镜像 | Dockerfile（构建上下文） | 角色 | 说明 |
| --- | --- | --- | --- |
| `agentic-server:latest` | `server/Dockerfile`（`server/`） | 多角色单镜像 | HTTP / celery worker / beat / 迁移按 `command` 区分，构建一次全栈复用 |
| `agentic-client:latest` | `client/agentic-client/Dockerfile`（`client/agentic-client/`） | 前端静态托管 + 同源反代 | Vite 构建产物 + nginx（envsubst 模板） |

规则：

- **镜像只构建一次**：`migrate` 服务承担 `agentic-server` 的 `build` 入口，
  `server`/`worker`/`beat` 按 `image:` tag 复用；禁止给多个服务写重复的
  `build:` 定义（漂移风险）。
- **正式部署建议以源码版本打 tag**：`server/` 与 `client/agentic-client/` 是
  两个独立 git 仓库，发布时用各自短 SHA 打附加 tag（如
  `agentic-server:<sha>`），`latest` 只作本地开发的滚动指针。
- 构建参数只允许经 Dockerfile 既定的 `ARG` 注入：客户端 `VITE_API_BASE` /
  `VITE_SSE_URL`（构建期烘焙进静态产物），其余运行期事实一律走容器环境变量
  （如 nginx 反代目标 `BACKEND_URL`），改地址不重建镜像。
- `.env` 与真实密钥**永不进镜像**（两仓库 `.dockerignore` 已排除）。

## 2. 目标拓扑

```
浏览器 ── :8081 ──► client(nginx) ── /api/ 去前缀 ──► server(uvicorn :8000, 单进程)
                                                      │   depends_on: migrate✓ + 中间件健康
    postgres(5432)  weaviate(8080/gRPC)  rustfs(9000)  redis(6379)
         └────────── 中间件四件 include 自 server/docker-compose.yaml ──┘
                                          redis ◄── worker(celery, 可扩) + beat(看门狗, 单实例)
                                                     migrate（一次性 Alembic，先于一切应用容器）
宿主机 Ollama(embedding bge-m3) ◄── host.docker.internal ── server/worker
```

- 依赖启动顺序由 compose `depends_on` 条件强制：postgres/weaviate/rustfs
  健康 → `rustfs-init`（幂等建桶）完成 → `migrate`（Alembic）成功 →
  server / worker / beat 启动；client 等 server 健康后才启动。
- 中间件四件的定义 **include 自 `server/docker-compose.yaml`**（与本地 dev
  栈单一事实源）。本栈与其不能同时运行（container_name 与宿主端口冲突），
  拉起前先在 `server/` 执行 `docker compose down`（数据卷保留，互不影响，
  见 §6）。

## 3. 端口与网络规范

| 宿主端口 | 服务 | 绑定 | 用途 |
| --- | --- | --- | --- |
| `8081`（`WEB_PORT`） | client(nginx) | `0.0.0.0` | **用户唯一入口**（页面 + `/api` 同源反代） |
| `8000`（`SERVER_PORT`） | server | `0.0.0.0` | 直连调试口（`/health` `/metrics` `/docs`）；同源反代模式下浏览器流量不经此口 |
| `5432` | postgres | `127.0.0.1` | 中间件（host 侧排查用） |
| `8080` / `50052` | weaviate | `127.0.0.1` | HTTP / gRPC（宿主侧 gRPC 错开 50051 的原因见 dev 栈注释） |
| `9000` / `9001` | rustfs | `127.0.0.1` | S3 API / 控制台口（9001 当前也由 S3 应答） |
| `6379` | redis | `127.0.0.1` | Celery broker/backend + 取消信号存储 |
| `9091` | worker 指标 | **不发布** | Celery worker Prometheus 指标（容器网络内抓取；需宿主抓取时在 compose 显式加发布口） |

规则：

- **8080 归 weaviate**，前端入口缺省 8081——改动前先查本表，端口新增须在此
  登记。
- 容器间一律走**服务名 + 容器内端口**（如 `http://server:8000`、
  `WEAVIATE_GRPC_PORT=50051`），禁止在容器环境里写宿主发布口
  （`127.0.0.1:*`）；宿主侧才允许出现发布端口。
- 中间件宿主发布口全部绑 `127.0.0.1`（仅本机排查可达）；唯一面向用户的
  发布口是 client 的 `WEB_PORT`。公网部署时在前面加反代做 TLS 与限流
  （§12.3；应用内不限流是既定决策，限流职责归网关层）。

## 4. 环境变量与配置规范

服务端配置链：进程 env > `.env` > `app/configs/*.yaml` 内联默认；`${VAR}` 无
默认值的变量缺失即启动 fail-fast。部署环境变量分三级：

| 级别 | 变量 | 口径 |
| --- | --- | --- |
| **必填** | `DEEPSEEK_API_KEY` | llm.yaml 无默认值；compose 用 `:?` 在解析期拦截（早于容器启动） |
| **必填** | `AUTH_JWT_SECRET` | JWT 签名密钥（HS256，≥32 字节强随机串）；compose 用 `:?` 在解析期拦截，且应用在 `APP_ENV=prod` 装配期二次校验——检出 dev 兜底密钥或长度不足 32 字节即拒绝启动（http/worker/migrate 全入口生效）；`python3 -c "import secrets; print(secrets.token_urlsafe(48))"` 生成 |
| 可选 | `MINERU_API_KEY`（知识库 PDF 解析）、`APP_ENV`（缺省 `prod`）、`WEB_PORT`/`SERVER_PORT`、`RUSTFS_PUBLIC_ENDPOINT`、`OLLAMA_API_URL`、中间件凭据组（`POSTGRES_*`/`RUSTFS_*`） | 缺省值与注释见 compose 与 `.env.example` |

规则：

- **容器接线变量（服务名/容器内端口）写死在 compose `environment`**，不进
  `.env`——它们是拓扑事实，不是环境差异；`.env` 只放环境差异（密钥、对外
  端口、对外地址）。
- `APP_ENV=prod` 是本栈缺省：业务异常以外的错误统一「服务内部错误」不泄露
  细节、日志 INFO。排障可临时 `APP_ENV=dev`（响应附 detail+trace、DEBUG
  日志），**不得**作为常态运行配置。
- 新增配置项的落点：先在 `server/app/configs/*.yaml` 以
  `${VAR:默认值}` 环境变量化（遵守 server/AGENTS.md 的连接信息建模规则），
  再在 compose `x-server-environment` 与 `.env.example` 两处登记——三处同步
  是配置变更的验收标准。

## 5. 数据与持久化规范

| 数据卷（`agentic-stack_` 前缀） | 挂载点 | 内容 | 地位 |
| --- | --- | --- | --- |
| `postgres-data` | postgres `/var/lib/postgresql` | **主数据**：会话/消息/记忆图谱/知识库元数据/用户 | 事实源，必须备份 |
| `rustfs-data` | rustfs `/data` | 知识库 PDF 与分段原文、会话图片附件（`agentic` 桶） | 事实源，必须备份 |
| `weaviate-data` | weaviate `/var/lib/weaviate` | 向量索引（记忆 + 知识库 collection） | **可再建**：记忆索引可 `admin memory rebuild-index` 重建；知识库可重新摄取。不单独备份 |
| `server-data` | server 家族 `/app/data` | SQLite 兜底位（postgres 形态下闲置） | 兜底卷，防 admin CLI 误用 sqlite 丢盘 |

规则：

- **应用进程不做 DDL**：建表/增量只经 Alembic——compose 的 `migrate` 一次性
  服务（`python -m app.cmd.admin db upgrade`），幂等可重跑
  （`docker compose run --rm migrate`）。schema 变更必须走新迁移脚本
  （server/AGENTS.md 的 db 工作流），禁止启动时建表。
- 备份口径：`pg_dump` postgres 卷 + 导出 rustfs 桶即可完整重建
  （weaviate 向量按上表再建）；`docker compose down -v` 会**删除全部数据卷**，
  只允许在明确要清空环境的场景使用，执行前必须二次确认。
- 日志文件（`logging.yaml` file sink，JSON 行）落各容器内部，**刻意不挂共享
  卷**（多进程共写一文件会争抢切割）；跨容器留存走 `docker logs`（console
  sink）接入宿主日志采集。

## 6. 与 dev 栈的关系（数据独立性）

- 本栈项目名 `agentic-stack`，数据卷前缀 `agentic-stack_*`；dev 中间件栈
  项目名 `agentic`，卷前缀 `agentic_*`——**两栈数据互相独立**，切换运行不丢
  对方数据。
- 两栈共用 container_name（`agentic-postgres` 等）与宿主端口，**不可同时
  运行**：拉起本栈前先 `cd server && docker compose down`；反之回 dev 栈前
  先 `cd deploy && docker compose down`。

## 7. 健康检查与就绪语义

- server 镜像内置 HEALTHCHECK（`GET /health`）：**liveness 口径**——有 HTTP
  响应即存活；503（db/redis 未就绪）是 readiness 降级，不算进程死亡，编排层
  不应据此重启容器。
- 服务间就绪由 compose `depends_on` 条件表达（健康/完成），不依赖应用内
  等待重试。
- worker / beat 无健康检查（Celery 进程存活即可）；worker 就绪可通过
  `docker compose exec worker celery -A app.cmd.task_executor.main inspect ping`
  人工确认。

## 8. 可观测性

- **日志**：`docker compose logs -f [server|worker|beat|...]`；每条请求日志带
  `X-Request-ID`（响应头同回带），跨服务排障以 request_id 串联。
- **指标**：server `GET :8000/metrics`（应用 + HTTP 中间件指标，路由模板做
  handler 标签）；worker 指标在容器内 `:9091`（任务计数/耗时/在途）。抓取端
  按需接入 Prometheus；`METRICS_ENABLED=false` 可整体关闭。
- **探活**：`/health` 逐项给出 db/redis 组件明细（全绿 200 / 任一 down 503）。

## 9. 安全基线

1. 密钥只经 `.env`/环境变量注入，不进镜像、不进 git（`deploy/.env` 不入库；
   server 仓库 `.gitignore` 已排除 `.env`）。
2. `AUTH_JWT_SECRET` 必填且必须是强随机值（§4）：compose 解析期 `:?` 拦截
   缺失，应用 prod 装配期拦截 dev 兜底密钥/弱密钥；泄露即全员会话失效级
   的轮换事件。
3. 对外只暴露 `WEB_PORT`；中间件与调试口不直接暴露公网（远程部署见 §12.2）。
4. 同源反代模式下后端 CORS 白名单无需登记页面 origin；跨域直连模式必须
   显式设置 `CORS_ORIGINS`（精确 origin 列表，禁止 `*`）。
5. 限流不在应用内（既定决策）：公网部署由网关层实现，nginx `limit_req`
   示例见 §12.3；请求体上限已由后端中间件与 nginx（`client_max_body_size
   64m`）双侧收口。
6. 预签名 URL（附件 302）TTL 600s、不落库不进日志；代理形态必须原样透传
   host+path（§12.2）。

## 10. 进程语义与扩缩容

| 服务 | 实例数 | 原因 |
| --- | --- | --- |
| server | **固定 1**（禁 scale） | uvicorn 单进程是有意的：在途 SSE 流 / 会话取消注册表在进程内，多进程破坏 cancel 语义与优雅停机 |
| worker | 可扩（`--scale worker=N`） | acks_late + prefetch=1 + claim 门闸幂等，天然支持水平扩 |
| beat | **固定 1** | 周期调度器，多实例会重复派发对账任务 |
| migrate | 一次性 | 幂等，可随时 `docker compose run --rm migrate` 重跑 |
| client | 可扩（前置 LB 时） | 无状态静态托管 |

优雅停机：server 收 SIGTERM 先取消在途 SSE 流（turn 记 CANCELED、不落半截
消息）再正常排空——compose stop 超时给足默认值即可，不要 `kill -9`。

## 11. 升级与回滚

标准升级流程（幂等，可重复执行）：

```bash
cd server/ && git pull && cd ../client/agentic-client && git pull && cd ../../deploy
docker compose up -d --build     # 重建镜像 → 依次滚动重启；migrate 先于应用执行
docker compose run --rm migrate  # 兜底重跑迁移（幂等，通常 up 已自动完成）
```

- 回滚：切回上一版源码后 `up -d --build`；若新迁移已应用，需先
  `docker compose run --rm migrate` 所在镜像执行 `alembic downgrade <rev>`
  （用回滚版本的镜像跑），再回退代码。**降级前先备份 §5 两个事实源卷。**
- 前端改 `VITE_API_BASE` 属构建期事实：改端口/域名 = 重新 build client，
  不是改 env 重启。

## 12. 形态变体

### 12.1 本机默认形态（本栈缺省）

同源反代 + 本机浏览器。要点：`RUSTFS_PUBLIC_ENDPOINT` 缺省
`http://127.0.0.1:9000`（浏览器可达的预签名基地址）；Ollama 经
`host.docker.internal`（Docker Desktop 自带；Linux 由 compose 的
`extra_hosts: host.docker.internal:host-gateway` 提供，且宿主 Ollama 需监听
`0.0.0.0`——`OLLAMA_HOST=0.0.0.0`，bridge 网关到不了仅绑 127.0.0.1 的服务）。

### 12.2 远程部署（浏览器不在宿主机上）

- `RUSTFS_PUBLIC_ENDPOINT` 必须改成浏览器可达地址（如
  `http://<host>:9000`）。中间件宿主口当前绑 127.0.0.1，远程可达需在 compose
  显式放开绑定或前置反代；反代 rustfs 时**必须原样透传 host+path**（SigV4
  签名含 host，子路径改写会签名失配）。
- `WEB_PORT`/`SERVER_PORT` 按需调整；`CORS_ORIGINS` 仅跨域直连形态需要。

### 12.3 公网 / TLS

前置网关（nginx/traefik 等）做 TLS 终结与限流；SSE 通道必须
`proxy_buffering off` + 长读超时（client 镜像内置模板已是该配置，外置网关
照抄）；限流策略在网关层实现（应用不产生 429）。

nginx `limit_req` 参考配置（示例阈值，按实际流量调整；限流挂在前置网关，
client 镜像内置的 nginx 只做静态托管与同源反代，不负责限流）：

```nginx
# ---- http {} 上下文：按来源 IP 定义两个限流区 ----
limit_req_zone $binary_remote_addr zone=api_general:10m rate=20r/s;  # 常规 API
limit_req_zone $binary_remote_addr zone=api_auth:10m    rate=1r/s;   # 认证端点（防爆破）

# ---- server {} 上下文（TLS 终结处）----
server {
    listen 443 ssl;
    # ...证书与常规反代参数略...

    # 超额返回 429（limit_req 缺省回 503，语义不当）
    limit_req_status 429;

    # 认证端点从严：login/register 是爆破面，burst 小且无排队
    location /api/auth/ {
        limit_req zone=api_auth burst=5 nodelay;
        proxy_pass http://127.0.0.1:8081;   # 转发到 client 的 WEB_PORT（页面与 /api 同源反代照旧）
    }

    # 常规 API（含 SSE）：限流按「请求数」计——一个 run 是一条请求，流式
    # 时长不占速率配额，但必须保留 SSE 反代参数（外层网关也要关缓冲）
    location /api/ {
        limit_req zone=api_general burst=40 nodelay;
        proxy_pass http://127.0.0.1:8081;
        proxy_buffering off;                # SSE 必须
        proxy_read_timeout 300s;            # SSE 长读超时
    }

    # 页面静态资源不限流
    location / {
        proxy_pass http://127.0.0.1:8081;
    }
}
```

注意：

- 键用 `$binary_remote_addr`（内存占用最小的每 IP 计数）；前面还有
  LB/CDN 时必须先配 `real_ip` 模块还原真实客户端 IP，否则全体流量共享
  同一个桶，正常用户会被误伤。
- `burst` + `nodelay`：突发额度一次性放行、超额直接 429；去掉 `nodelay`
  会排队等待，对 SSE 与交互式请求体验都差。
- 认证端点单独从严是防爆破口径，与 §9.5 的「限流不在应用内」同一决策：
  网关层是全栈唯一的 429 来源。

### 12.4 跨域直连模式（不用同源反代）

client 构建参数改 `VITE_API_BASE=http://<后端地址>`（重建镜像），后端
`CORS_ORIGINS` 显式登记页面 origin。仅特殊网络拓扑使用，默认形态不做。

### 12.5 数据库退回 SQLite

compose 缺省 `DB_DEFAULT=postgres`；单机极简场景可去掉该变量（回落
`db.yaml` 缺省 sqlite，`server-data` 卷接管持久化），但 http/worker/beat 多
进程共写 SQLite 仅适合试用，不作为部署推荐形态。
