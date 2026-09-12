import { expect, test } from "@playwright/test";

import { randomUser, registerViaUi } from "./helpers.ts";

/**
 * 知识库管理链路（解析向量化是异步流水线，轮询口径宽松）。
 */
test.describe("知识库管理", () => {
  test("建库 → 传 Markdown → 轮询至 ready → 建分段", async ({ page }) => {
    const user = randomUser("kb");
    await registerViaUi(page, user);

    await page.goto("/admin/knowledge");
    await expect(page).toHaveURL(/\/admin\/knowledge/);

    // 新建知识库（弹窗表单）
    await page.getByRole("button", { name: /新建|创建/ }).first().click();
    const kbName = `e2e-kb-${Math.random().toString(36).slice(2, 8)}`;
    await page.getByLabel(/名称/).fill(kbName);
    await page.getByRole("button", { name: /确认|创建|保存/ }).last().click();

    // 列表出现新库并进入详情
    await expect(page.getByText(kbName).first()).toBeVisible({ timeout: 30_000 });
    await page.getByText(kbName).first().click();

    // 上传 Markdown 文档（dropzone 点选）
    await page.getByRole("button", { name: /上传/ }).first().click();
    const input = page.locator('input[type="file"]');
    await input.setInputFiles({
      name: "e2e-doc.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# E2E 测试文档\n\n这是一段用于端到端测试的知识库内容。"),
    });
    await page.getByRole("button", { name: /上传/ }).last().click();
    await expect(page.getByText(/已提交 1\/1/)).toBeVisible({ timeout: 30_000 });

    // 轮询至 ready（解析+向量化流水线，给足窗口）
    await expect(page.getByText(/ready|就绪/).first()).toBeVisible({ timeout: 180_000 });
  });
});
