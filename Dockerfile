# =============================================================================
# agentic 后端镜像 —— 单镜像多角色，按 command 区分
#
# 构建（CWD = server/）:
#   docker build -t agentic-server .
#
# HTTP 服务（缺省 CMD）:
#   docker run --rm -p 8000:8000 \
#     -e DEEPSEEK_API_KEY=sk-... \
#     -v agentic-server-data:/app/data \
#     agentic-server
#
# Celery worker / watchdog beat（需可达 redis，中间件栈见 docker-compose.yaml）:
#   docker run --rm <同上 env/卷> agentic-server \
#     celery -A app.cmd.task_executor.main worker
#   docker run --rm <同上 env/卷> agentic-server \
#     celery -A app.cmd.task_executor.main beat \
#       --schedule /app/runtime/celerybeat-schedule
#
# 数据库迁移（新库执行一次；SQLITE_DB_PATH 缺省 data/agentic.db）:
#   docker run --rm <同上 env/卷> agentic-server python -m app.cmd.admin db upgrade
#
# 说明:
# - .env 不打进镜像（内含真实 API key，已在 .dockerignore 排除），配置一律经
#   容器环境变量注入（取值优先级：进程 env > .env > YAML 内联默认，
#   见 app/core/config/loader.py）。配置目录随镜像在 /app/app/configs，
#   可用 AGENTIC_CONFIG_DIR / AGENTIC_ENV_FILE 覆盖。
# - DEEPSEEK_API_KEY 是唯一无默认值的必填变量，缺省启动即 fail-fast。
# - 健康检查探 GET /health：有 HTTP 响应即存活（503=依赖未就绪属 readiness
#   口径，不判进程死亡），依赖级就绪探针交给编排层。
# - ENTRYPOINT 用 tini：uvicorn 的 SIGTERM 优雅停机（lifespan 会先取消在途
#   SSE 流）与 celery prefork 的僵尸回收都需要 PID1 正确转发/收割信号。
# - uvicorn 单进程是有意的：在途 SSE 流/会话注入注册表均在进程内，
#   多 worker 会破坏 cancel 与取消广播的语义。
# - 全部 COPY 用绝对路径目标：部分 Docker Desktop（containerd 镜像存储）在
#   多阶段构建导出时会把相对目标错误解析到 / 而非 WORKDIR（本机实测踩坑）。
#   两个 stage 的 WORKDIR 也刻意不同名。
# =============================================================================

# ---------- 依赖安装阶段：uv sync --frozen 按 uv.lock 精确还原 ----------
FROM python:3.12-slim AS builder

# UV_COMPILE_BYTECODE: 预编译 site-packages 字节码，加速容器冷启动
# UV_LINK_MODE=copy: 消除硬链接跨文件系统告警；UV_PYTHON_DOWNLOADS=never: 只用基础镜像自带的 3.12
# UV_PROJECT_ENVIRONMENT: venv 直接建在运行阶段的最终路径 /app/.venv ——
#   console script 的 shebang 是 venv 绝对路径，建错位置再搬移会全部失效
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

WORKDIR /build

# 先只拷依赖清单，充分利用层缓存（代码变动不触发重装依赖）
COPY pyproject.toml uv.lock /build/
# --no-install-project: 应用代码以源码目录形式进镜像（python -m 从 CWD 解析），
#   无需把 server 本身构建/安装成包
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# ---------- 运行阶段 ----------
FROM python:3.12-slim

# PYTHONPATH=/app: uv sync 用了 --no-install-project（应用代码不装成包），
# console script（uvicorn/celery/alembic）运行时 CWD 不在 sys.path 上，需显式补上
ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app

# tini 作为 PID1：信号转发 + 僵尸回收（见文件头说明）
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 agentic \
 && useradd --system --uid 10001 --gid agentic agentic \
 # 可写目录：SQLite 数据（SQLITE_DB_PATH 缺省 data/agentic.db，建议挂卷持久化）
 # 与文件日志（logging.yaml 的 ${APP_LOG_DIR:runtime/logs}）+ celery beat 调度文件
 && mkdir -p /app/data /app/runtime/logs \
 && chown -R agentic:agentic /app/data /app/runtime

COPY --from=builder /app/.venv /app/.venv

# 应用代码 + Alembic 迁移（alembic.ini 的 script_location 以 %(here)s 锚定，
# 与 CWD 无关，迁移命令在 /app 下执行即可）
COPY app /app/app
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini

COPY <<'EOF' /usr/local/bin/container-healthcheck.py
"""容器存活探针：/health 有任意 HTTP 响应即存活。

503（db/redis 未就绪）是 readiness 语义，不算进程死亡；
只有连不上（进程挂了/端口未监听）才返回非零。
"""
import sys
import urllib.error
import urllib.request

try:
    urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3)
except urllib.error.HTTPError:
    pass
except Exception:
    sys.exit(1)
EOF

USER agentic
WORKDIR /app

EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "/usr/local/bin/container-healthcheck.py"]

# 缺省启动 HTTP 服务；celery/beat/迁移按文件头示例以 command 覆盖运行
CMD ["uvicorn", "app.cmd.http.main:server", "--host", "0.0.0.0", "--port", "8000"]
