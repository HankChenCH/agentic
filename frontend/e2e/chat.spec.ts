import { expect, test } from "@playwright/test";

import { randomUser, registerViaUi } from "./helpers.ts";

/**
 * 聊天主链路（LLM 断言宽松口径：只断「有非空流式回复」，不校验内容语义）。
 */
test.describe("聊天主链路", () => {
  test("新会话发消息 → 流式回复 + 会话入侧栏 + 刷新后历史回放", async ({ page }) => {
    const user = randomUser("chat");
    await registerViaUi(page, user);

    const composer = page.getByRole("textbox").first();
    await expect(composer).toBeVisible({ timeout: 30_000 });

    // 有智能体目录时先选一个（新会话视图的 AgentModeSwitch；无则后端默认兜底）
    const demoPill = page.getByRole("button", { name: /演示|demo/i }).first();
    if (await demoPill.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await demoPill.click();
    }

    await composer.fill("用一句话介绍你自己");
    await composer.press("Enter");

    // 流式回复：助手消息出现且非空（RUN_STARTED→…→RUN_FINISHED 全程）
    const assistantMessage = page.locator('[data-role="assistant"], .aui-assistant-message').first();
    await expect(assistantMessage).toBeVisible({ timeout: 120_000 });
    await expect(assistantMessage).not.toHaveText(/^$/);

    // RUN_STARTED 刷列表：侧栏出现新会话条目（标题可能暂缺，显示 New Chat）
    const sidebarItem = page.locator('aside, nav').getByText(/New Chat|用一句话|介绍/).first();
    await expect(sidebarItem).toBeVisible({ timeout: 30_000 });

    // 刷新 → 历史回放：translator 还原的消息流仍在
    await page.reload();
    await expect(page.getByRole("textbox").first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("用一句话介绍你自己").first()).toBeVisible({ timeout: 30_000 });
  });
});
