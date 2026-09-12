import { expect, test } from "@playwright/test";

import { randomUser, registerViaUi } from "./helpers.ts";

/**
 * 管理侧模块启动页 + 记忆图谱渲染冒烟（快照数据可为空：空态页也算通过）。
 */
test.describe("管理侧", () => {
  test("管理首页：模块注册表渲染出知识库与记忆图谱入口", async ({ page }) => {
    const user = randomUser("admin-home");
    await registerViaUi(page, user);

    await page.goto("/admin");
    await expect(page.getByText(/知识库/).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/记忆图谱/).first()).toBeVisible();
  });

  test("记忆图谱页渲染（空快照走空态，不白屏）", async ({ page }) => {
    const user = randomUser("memory-graph");
    await registerViaUi(page, user);

    await page.goto("/admin/memory-graph");
    await expect(page).toHaveURL(/\/admin\/memory-graph/);
    // 页面壳就绪：空态提示或图例/画布任一可见即算渲染成功
    await expect(
      page.getByText(/暂无|空|图谱|实体|事件/).first(),
    ).toBeVisible({ timeout: 30_000 });
  });
});
