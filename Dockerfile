# =============================================================================
# agentic 前端镜像 —— Vite 构建静态产物 + nginx 托管（SPA 回退 + SSE 友好反代）
#
# 构建（CWD = client/agentic-client/）:
#   docker build -t agentic-client .
#
# 模式一：同源反代（推荐 —— 天然免 CORS，后端白名单无需加页面 origin）
#   docker build --build-arg VITE_API_BASE=/api -t agentic-client .
#   docker run -d -p 8080:80 \
#     -e BACKEND_URL=http://host.docker.internal:8000 agentic-client
#   （Linux 宿主机需给 run 加 --add-host=host.docker.internal:host-gateway；
#    compose 内部署改传 -e BACKEND_URL=http://<后端服务名>:8000。
#    BACKEND_URL 是 nginx 反代目标，运行时可改、无需重建镜像）
#
# 模式二：跨域直连后端
#   docker build --build-arg VITE_API_BASE=http://127.0.0.1:8000 -t agentic-client .
#   （后端 CORS 白名单必须包含页面 origin：
#    服务端容器 -e CORS_ORIGINS=http://localhost:8080）
#
# 后端地址在构建期烘焙进静态产物（Vite 的 import.meta.env 特性），改地址 = 重建；
# VITE_SSE_URL 仅在 SSE 与 REST 不同源/走网关时才需要（缺省由 REST_BASE 派生）。
# =============================================================================

# ---------- 构建阶段：node 24 对齐 CI（react-router 8.3 要求 ≥22.22） ----------
FROM node:24-alpine AS build

WORKDIR /app

# 先只拷依赖清单做缓存层（代码变动不触发 yarn install）。
# 全部 COPY 用绝对路径目标：部分 Docker Desktop（containerd 镜像存储）在
# 多阶段构建导出时会把相对目标错误解析到 / 而非 WORKDIR（本机实测踩坑）
COPY package.json yarn.lock /app/
RUN yarn install --frozen-lockfile

COPY . /app/.

# 后端地址经构建参数写入 .env.production（Vite 以 production 模式构建时读取）。
# 只写非空变量：config.ts 的兜底用 ?? 判空，空串会破坏「VITE_SSE_URL 缺省由
# REST_BASE 派生」的语义，故未提供的变量保持不定义；末尾 unset 掉 shell 环境里
# 的同名空串变量，避免 process env（优先级高于 .env 文件）以空串覆盖兜底逻辑。
ARG VITE_API_BASE
ARG VITE_SSE_URL
RUN set -eu; \
    : > .env.production; \
    if [ -n "${VITE_API_BASE:-}" ]; then echo "VITE_API_BASE=${VITE_API_BASE}" >> .env.production; fi; \
    if [ -n "${VITE_SSE_URL:-}" ]; then echo "VITE_SSE_URL=${VITE_SSE_URL}" >> .env.production; fi; \
    unset VITE_API_BASE VITE_SSE_URL; \
    yarn build

# ---------- 运行阶段 ----------
FROM nginx:alpine

# 官方镜像的 entrypoint 会对 /etc/nginx/templates/*.template 跑 envsubst 后
# 落到 conf.d/；只替换已定义的环境变量，$uri/$host 等 nginx 变量不受影响
COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html

# 同源反代目标，缺省指向宿主机上发布的后端；运行时用 -e BACKEND_URL=... 覆盖
ENV BACKEND_URL=http://host.docker.internal:8000

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD wget -qO /dev/null http://127.0.0.1/ || exit 1
