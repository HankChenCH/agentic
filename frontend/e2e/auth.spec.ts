import { expect, test } from "@playwright/test";

import { randomUser, registerViaUi } from "./helpers.ts";

test.describe("认证链路", () => {
  test("未登录访问受保护路由 → 重定向 /login 并带 next 回跳参数", async ({ page }) => {
    await page.goto("/admin/knowledge");
    await expect(page).toHaveURL(/\/login\?next=/);
  });

  test("注册即登录：落首页可进入聊天视图", async ({ page }) => {
    const user = randomUser("auth-reg");
    await registerViaUi(page, user);

    // 首页即聊天页（新会话视图）：composer 可见即会话运行时就绪
    await expect(page.getByRole("textbox").first()).toBeVisible({ timeout: 30_000 });
  });

  test("退出登录后回 /login，再访问受保护路由仍被守卫", async ({ page }) => {
    const user = randomUser("auth-logout");
    await registerViaUi(page, user);
    await expect(page.getByRole("textbox").first()).toBeVisible({ timeout: 30_000 });

    // 用户菜单 → 退出登录
    await page.getByRole("button", { name: /退出|账号|用户/ }).first().click();
    await page.getByRole("menuitem", { name: /退出登录/ }).click();
    await expect(page).toHaveURL(/\/login/);

    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
  });
});
