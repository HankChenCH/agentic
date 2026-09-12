import { expect, type Page } from "@playwright/test";

/**
 * E2E 公共工具：注册即登录的账号工厂 + 登录页操作。
 * 用户名随机后缀保证套件可重复执行（后端注册幂等无冲突）。
 */

export const randomUser = (prefix = "e2e") => ({
  username: `${prefix}-${Math.random().toString(36).slice(2, 10)}`,
  password: "e2e-password-123",
});

/** 在 /login 注册新账号（注册即登录，成功后落到首页） */
export const registerViaUi = async (page: Page, user: { username: string; password: string }) => {
  await page.goto("/login");
  // 双 Tab：切到注册
  await page.getByRole("button", { name: "注册" }).first().click();
  await page.getByLabel("用户名").fill(user.username);
  await page.getByLabel("密码", { exact: false }).first().fill(user.password);
  await page.getByLabel("确认密码", { exact: false }).first().fill(user.password);
  await page.getByRole("button", { name: "注册" }).last().click();
  await expect(page).not.toHaveURL(/\/login/);
};

/** 已登录用户从用户菜单退出登录 */
export const logoutViaUi = async (page: Page) => {
  // 右上角用户菜单（chat-page 头部）；按钮文案见 thread 头部实现
  const menuButton = page.getByRole("button", { name: /退出|账号|用户/ }).first();
  await menuButton.click();
  await page.getByRole("menuitem", { name: /退出登录/ }).click();
  await expect(page).toHaveURL(/\/login/);
};
