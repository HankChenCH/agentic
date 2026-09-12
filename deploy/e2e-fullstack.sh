#!/usr/bin/env bash
# e2e 变体作业：经 nginx 全栈入口跑后端 e2e 套件。
#
# 与直连模式的差别（见 backend/tests_e2e/helpers.py）：
#   - 入口是前端 nginx（WEB_PORT，默认 8081，全栈唯一用户入口），
#     /api/ 前缀由 nginx 剥离后转发后端；
#   - E2E_API_PREFIX=/api 开启路径改写 + 429 退避（/api/auth/ 限流
#     1r/s + burst 5，套件多用户注册会被误伤）；
#   - test_e2e_gateway.py 的代理特有断言仅在本模式运行。
#
# 前置（deploy/deployment-spec.md）：
#   docker compose up -d --build          # 全栈拉起（含 migrate/beat）
#   backend/.env 携带真实 DEEPSEEK_API_KEY（compose 栈经 server 环境注入）
#
# 直连模式（对照）：起 backend dev 栈后
#   E2E_BASE_URL=http://127.0.0.1:8000 uv run pytest tests_e2e/
set -euo pipefail

WEB_PORT="${WEB_PORT:-8081}"
BASE_URL="${E2E_BASE_URL:-http://127.0.0.1:${WEB_PORT}}"

# 非交互 shell（agent/CI runner）的 PATH 不含 uv（~/.local/bin）与 fnm 管的 node
if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
fi

cd "$(dirname "$0")/../backend"

# 前置健康检查：入口与 /api 反代通路
echo "==> 检查全栈入口 ${BASE_URL}"
curl -fsS "${BASE_URL}" -o /dev/null || {
  echo "全栈入口不可达（docker compose up -d --build 起全栈后再跑）" >&2
  exit 1
}
echo "==> 检查 /api/health 反代通路"
curl -fsS "${BASE_URL}/api/health" -o /dev/null || {
  echo "/api/health 不可达（nginx 反代或后端未就绪）" >&2
  exit 1
}

echo "==> 运行 e2e（经 nginx 全栈入口）"
E2E_BASE_URL="${BASE_URL}" E2E_API_PREFIX=/api \
  uv run pytest tests_e2e/ -v
