import path from "node:path";

import { defineConfig } from "vitest/config";

// 测试专用最小配置：被测模块（thread-message-translator / run-input /
// agent-store）均为纯 .ts 数据变换，无需 react/tailwind 插件——刻意与
// vite.config.ts 解耦，规避 vitest 内置 vite 与应用 vite 的插件版本耦合。
// alias 需与 vite.config.ts 保持一致（@ → ./src）。
export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    // node 环境足够：被测模块不触 DOM；zustand persist 在无 window 时
    // 静默跳过 hydration（middleware 内部 try/catch），不影响 store 断言
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
