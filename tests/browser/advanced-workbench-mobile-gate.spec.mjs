import { expect, test } from "@playwright/test";

const WORKBENCHES = [
  { id: "console", title: "3D 导演台", mounted: '[data-ui="director-console"]' },
  { id: "notebook", title: "导演手记", mounted: '[data-ui="director-notebook"]' },
  { id: "canvas", title: "谱系图谱", mounted: '[data-ui="lineage-canvas"]' },
];
const ADVANCED_SURFACES = '[data-ui="director-console"], [data-ui="director-notebook"], [data-ui="lineage-canvas"]';

test.setTimeout(120_000);

async function useDemoPersona(page) {
  await page.addInitScript(() => {
    if (window !== window.top) return;
    window.sessionStorage.setItem("ai-video.demo-persona", "operator");
  });
}

async function expectNoHorizontalPageOverflow(page) {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: Math.max(document.documentElement.scrollWidth, document.body?.scrollWidth || 0),
  }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport + 1);
}

test("phone entry exposes three equal desktop-only paths and never mounts an advanced editor", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith("phone-"), "Phone-only product boundary.");
  const directorRequests = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/director-desk/")) directorRequests.push(request.url());
  });
  await useDemoPersona(page);
  await page.goto("/creation", { waitUntil: "commit" });

  const entry = page.locator('[data-ui="creation-invitation-entry"]');
  await expect(entry).toBeVisible({ timeout: 90_000 });
  await expect(entry.locator(".wb-pathway")).toHaveCount(3);

  for (const workbench of WORKBENCHES) {
    const path = entry.locator(`[data-workbench="${workbench.id}"]`);
    await expect(path).toBeVisible();
    await expect(path).toHaveAccessibleName(new RegExp(`${workbench.title}.*仅桌面端`));
    await expect(path.locator("xpath=..").locator(".wb-desktop-badge")).toHaveText("仅桌面端");
  }
  await expect(entry.locator(".wb-transfer-entry")).toHaveCount(0);
  await expectNoHorizontalPageOverflow(page);
  await page.screenshot({
    path: testInfo.outputPath(`advanced-workbench-entry-${page.viewportSize()?.width}x${page.viewportSize()?.height}.png`),
    fullPage: true,
  });

  for (const workbench of WORKBENCHES) {
    await entry.locator(`[data-workbench="${workbench.id}"]`).click();
    const gate = page.locator('[data-ui="desktop-workbench-gate"]');
    await expect(gate).toBeVisible();
    await expect(gate).toHaveAttribute("data-workbench", workbench.id);
    await expect(gate.getByRole("heading", { name: `请在桌面端使用${workbench.title}` })).toBeVisible();
    await expect(gate.getByText("手机端不会加载编辑器。", { exact: false })).toBeVisible();
    await expect(page.locator(ADVANCED_SURFACES)).toHaveCount(0);
    await expect(page.locator('[data-ui="workbench-editor"]')).toHaveCount(0);
    await expect(page.locator('[data-ui="storyai-director-desk"]')).toHaveCount(0);
    await expect(page.locator('iframe[title="StoryAI 3D 导演台"]')).toHaveCount(0);
    await expectNoHorizontalPageOverflow(page);
    if (workbench.id === "console") {
      await page.screenshot({
        path: testInfo.outputPath(`advanced-workbench-gate-${page.viewportSize()?.width}x${page.viewportSize()?.height}.png`),
        fullPage: true,
      });
    }
    await gate.getByRole("button", { name: "使用手机快速生成" }).click();
    await expect(page).toHaveURL(/\/creation$/);
    await expect(entry).toBeVisible();
  }
  expect(directorRequests).toEqual([]);
});

test("phone deep links resolve to the same desktop-required gate", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith("phone-"), "Phone-only product boundary.");
  const directorRequests = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/director-desk/")) directorRequests.push(request.url());
  });
  await useDemoPersona(page);

  for (const workbench of WORKBENCHES) {
    await page.goto(`/creation?workbench=${workbench.id}`, { waitUntil: "commit" });
    await expect(page).toHaveURL(new RegExp(`/creation\\?workbench=${workbench.id}$`));
    const gate = page.locator('[data-ui="desktop-workbench-gate"]');
    await expect(gate).toBeVisible({ timeout: 90_000 });
    await expect(gate).toHaveAttribute("data-workbench", workbench.id);
    await expect(gate.getByRole("heading", { name: `请在桌面端使用${workbench.title}` })).toBeVisible();
    await expect(gate.getByText("手机端不会加载编辑器。", { exact: false })).toBeVisible();
    await expect(page.locator(ADVANCED_SURFACES)).toHaveCount(0);
    await expect(page.locator('[data-ui="workbench-editor"]')).toHaveCount(0);
    await expect(page.locator('[data-ui="storyai-director-desk"]')).toHaveCount(0);
    await expect(page.locator('iframe[title="StoryAI 3D 导演台"]')).toHaveCount(0);
    await expectNoHorizontalPageOverflow(page);
  }
  expect(directorRequests).toEqual([]);
});

test("desktop deep links still mount the advanced workbenches", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-1440", "Desktop-only control check.");
  await useDemoPersona(page);

  for (const workbench of WORKBENCHES) {
    await page.goto(`/creation?workbench=${workbench.id}`, { waitUntil: "commit" });
    await expect(page.locator(workbench.mounted)).toBeVisible({ timeout: 90_000 });
    await expect(page.locator('[data-ui="desktop-workbench-gate"]')).toHaveCount(0);
  }
});
