import { expect, test, type Page } from "@playwright/test";

async function enableAiMode(page: Page) {
  const firstParagraph = page.locator(".notes-editor-content p").first();
  await firstParagraph.click({ button: "right" });
  await page.getByRole("menuitem", { name: "啟用 AI 助理" }).click();
  await expect(page.locator(".ai-cursor-mode")).toBeVisible();
}

async function dragSelectFirstParagraph(page: Page) {
  const paragraph = page.locator(".notes-editor-content p").first();
  const box = await paragraph.boundingBox();
  if (!box) throw new Error("Editor paragraph has no bounding box");
  await page.mouse.move(box.x + 4, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + Math.min(155, box.width - 4), box.y + box.height / 2, { steps: 10 });
  await expect(page.locator("[data-inline-ai-popover]")).toHaveCount(0);
  await page.mouse.up();
  await expect(page.locator("[data-inline-ai-popover]")).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/personal-notes/ai", async (route) => {
    const body = route.request().postDataJSON() as { instruction?: string; selectedText?: string };
    const generatedText = body.instruction?.includes("微調")
      ? "Refined deterministic suggestion."
      : `AI:${body.selectedText ?? "selection"}`;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ generatedText, operation: "improve", provider: "mock" }),
    });
  });
  await page.goto("/ai-editor-e2e");
  await expect(page.locator(".notes-editor-content")).toBeVisible();
});

test("right-click mode, mouse selection, popover refinement, replace and undo", async ({ page }) => {
  await enableAiMode(page);
  const cursor = await page.locator(".notes-editor-content").evaluate((element) => getComputedStyle(element).cursor);
  expect(cursor).toContain("data:image/svg+xml");
  const selectionColor = await page.locator(".notes-editor-content p").first().evaluate(
    (element) => getComputedStyle(element, "::selection").backgroundColor,
  );
  expect(selectionColor).toBe("rgb(220, 238, 255)");

  await dragSelectFirstParagraph(page);
  const popover = page.locator("[data-inline-ai-popover]");
  const beforeBox = await popover.boundingBox();
  expect(beforeBox?.x).toBeGreaterThanOrEqual(0);
  expect(beforeBox?.y).toBeGreaterThanOrEqual(0);

  await page.getByRole("button", { name: "Improve" }).click();
  await expect(
  popover.getByText("AI:Alpha paragraph for A", { exact: true })
).toBeVisible();
  await page.getByLabel("繼續微調").fill("再精簡一點");
  await page.getByRole("button", { name: "微調" }).click();
  await expect(popover.getByText("Refined deterministic suggestion.")).toBeVisible();
  await page.getByRole("button", { name: "重新生成" }).click();
  await expect(popover.getByText("Refined deterministic suggestion.")).toBeVisible();

  await page.setViewportSize({ width: 720, height: 640 });
  const resizedBox = await popover.boundingBox();
  expect((resizedBox?.x ?? 0) + (resizedBox?.width ?? 0)).toBeLessThanOrEqual(720);
  expect((resizedBox?.y ?? 0) + (resizedBox?.height ?? 0)).toBeLessThanOrEqual(640);

  await page.getByRole("button", { name: "接受取代" }).click();
  await expect(page.locator(".notes-editor-content")).toContainText("Refined deterministic suggestion.");
  await expect(page.getByTestId("update-count")).toHaveText("1");
  await page.getByRole("button", { name: "復原" }).click();
  await expect(page.locator(".notes-editor-content")).toContainText("Alpha paragraph");
});

test("Escape exits AI mode and spreadsheet keeps its native context area", async ({ page }) => {
  await enableAiMode(page);
  await page.locator(".spreadsheet-node-view").click({ button: "right" });
  await expect(page.getByRole("menu", { name: "筆記編輯選單" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.locator(".ai-cursor-mode")).toHaveCount(0);

  await enableAiMode(page);
  await page.locator(".notes-editor-content p").first().click({ button: "right" });
  await page.getByRole("menuitem", { name: "退出 AI 助理" }).click();
  await expect(page.locator(".ai-cursor-mode")).toHaveCount(0);
});
