import { defineConfig, devices } from "@playwright/test";

/**
 * 前端 E2E（冒烟层）：目标 = 一键部署的全栈 compose（deploy/docker-compose.yaml，
 * 浏览器 ── :8081 ──► nginx（前端 + /api 反代去前缀）──► 后端）。
 *
 * 运行前提：`docker compose up -d --build`（deploy/ 目录）已拉起全栈；
 * 本 runner 不负责起栈（栈内含 postgres/weaviate/rustfs/redis/worker，
 * 生命周期归 compose 管），只对已运行的服务发请求。
 *
 * 断言口径：LLM 生成内容非确定性，只断「有非空流式回复」级别的宽松结果；
 * 用户名等唯一资源用随机后缀，套件可重复执行。
 */
export default defineConfig({
  testDir: "./e2e",
  // vitest 的 include 不覆盖 e2e/（不在 src/ 下），两套 runner 天然隔离
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false, // 共享一份后端栈，会话/知识库数据互不干扰优先
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:8081",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
