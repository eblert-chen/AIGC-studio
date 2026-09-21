import { expect, test } from "@playwright/test";
import { modeLabel } from "../../src/modelCapabilities.js";
import { arkModelResponse } from "../fixtures/ark-video-discovery.mjs";
import { minimaxH3DiscoveryResponses, minimaxH3ModelResponse } from "../fixtures/minimax-h3-discovery.mjs";
import { installMiniMaxH3PlatformFixture } from "../browser-fixtures/minimax-h3-platform.mjs";

// This suite drives the production App with intercepted Platform responses.
// No test is evidence of provider access, live generation or customer grants.
test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.config.metadata.minimaxH3Contract !== true,
    "Requires the dedicated synthetic MiniMax H3 contract configuration");
});
test.setTimeout(120_000);

const deck = (page) => page.locator('[data-ui="director-composer"]');
const phone = (page) => page.viewportSize().width <= 620;

async function openDraft(page) {
  await expect(deck(page)).toHaveCount(1, { timeout: 90_000 });
  if (phone(page) && await deck(page).getAttribute("data-mobile-open") !== "true") {
    await page.locator("#mobile-composer-launcher").click();
  }
}

async function panel(page, name) {
  await openDraft(page);
  if (await deck(page).getAttribute("data-panel") !== name) {
    await page.locator(`[data-composer-panel-trigger="${name}"]:visible`).first().click();
  }
  await expect(page.locator(`#composer-${name}-panel`)).toBeVisible();
}

async function closePanel(page, name) {
  await page.locator(`#composer-${name}-panel [data-composer-panel-focus]`).first().focus();
  await page.keyboard.press("Escape");
  await expect(deck(page)).toHaveAttribute("data-panel", "closed");
}

async function selectModel(page, model) {
  await panel(page, "model");
  await page.locator(".director-model-option").filter({ has: page.getByText(model.display_name, { exact: true }) }).click();
  await expect(page.locator("#model")).toContainText(model.display_name);
}

async function selectMode(page, mode) {
  if (phone(page)) {
    await panel(page, "recipe");
    await page.locator("#composer-recipe-panel").getByRole("button", { name: modeLabel(mode), exact: true }).click();
    await closePanel(page, "recipe");
  } else {
    await page.locator("#generation-mode").getByRole("tab", { name: modeLabel(mode), exact: true }).click();
  }
}

async function safeLayout(page) {
  expect(await page.evaluate(() => Math.max(document.body.scrollWidth, document.documentElement.scrollWidth)
    - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  const action = page.locator(".generate-button");
  await expect(action).toBeVisible();
  const box = await action.boundingBox();
  const viewport = page.viewportSize();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
  if (phone(page)) {
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
    const sizes = await page.locator(".director-choice-group button:visible").evaluateAll((buttons) => buttons.map((button) => {
      const bounds = button.getBoundingClientRect();
      return { width: bounds.width, height: bounds.height };
    }));
    for (const size of sizes) {
      expect(Math.round(size.width * 100) / 100).toBeGreaterThanOrEqual(44);
      expect(Math.round(size.height * 100) / 100).toBeGreaterThanOrEqual(44);
    }
  }
}

function trackErrors(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  return errors;
}

test("Ark to H3 to Max keeps exact specs and one mounted Director Deck", async ({ page }, testInfo) => {
  const errors = trackErrors(page);
  const h3 = minimaxH3ModelResponse("minimax-h3");
  const maximum = minimaxH3ModelResponse("minimax-h3-max");
  const models = [arkModelResponse("seedance-2.5"), arkModelResponse("seedance-2.0"), h3, maximum];
  const state = await installMiniMaxH3PlatformFixture(page, { models });
  await page.goto("/creation", { waitUntil: "commit" });
  await openDraft(page);
  await selectModel(page, models[0]);
  await panel(page, "specs");
  await page.getByRole("button", { name: "30 秒", exact: true }).click();
  await selectModel(page, h3);
  await panel(page, "specs");
  await expect(page.locator("#composer-media-tab-image, #composer-media-tab-audio")).toHaveCount(0);
  await expect(page.locator('.director-fixed-specs [data-specification="outputCount"]')).toContainText("1 个");
  await expect(page.locator(".director-choice-group.is-count")).toHaveCount(0);
  await expect(page.locator(".director-choice-group.is-duration button")).toHaveText(Array.from({ length: 12 }, (_, index) => `${index + 4} 秒`));
  await expect(page.locator(".director-choice-group.is-resolution button")).toHaveText(["768p", "2k"]);
  await expect(page.getByRole("button", { name: "30 秒", exact: true })).toHaveCount(0);
  await selectModel(page, models[1]);
  await panel(page, "specs");
  await page.getByRole("button", { name: "4k", exact: true }).click();
  await selectModel(page, h3);
  await panel(page, "specs");
  await expect(page.locator(".director-choice-group.is-resolution button.is-active")).toHaveText("768p");
  const twoK = page.getByRole("button", { name: "2k", exact: true });
  await twoK.focus();
  await twoK.press("Enter");
  await expect(twoK).toBeFocused();
  await expect(twoK).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "4 秒", exact: true }).click();
  await safeLayout(page);
  await page.screenshot({ path: testInfo.outputPath("minimax-h3-exact-specs.png") });
  await closePanel(page, "specs");
  const unicodePrompt = "中".repeat(6999) + "🎬";
  await page.locator("#prompt").fill(unicodePrompt);
  await expect(page.locator("#prompt")).toHaveValue(unicodePrompt);
  await expect(page.locator(".composer-prompt-section .field-heading span")).toHaveText("7000 / 7000");
  await page.locator("#prompt").fill(unicodePrompt + "🚀");
  await expect(page.locator("#prompt")).toHaveValue(unicodePrompt);
  await page.locator("#prompt").fill("镜头随人物进入明亮的制作室，保持参考画面的自然光。🎬");
  await selectModel(page, maximum);
  await panel(page, "specs");
  await expect(page.locator(".director-choice-group.is-resolution button")).toHaveText(["480p", "768p"]);
  await expect(page.locator(".director-choice-group.is-duration button.is-active")).toHaveText("5 秒");
  await expect(page.locator("#generation-mode [role=tab]")).toHaveCount(1);
  await closePanel(page, "specs");
  await page.evaluate(() => { window.__minimaxSharedDeck = document.querySelector('[data-ui="director-composer"]'); });
  const navigation = page.getByRole("navigation", { name: "工作台导航" });
  await navigation.getByRole("button", { name: "首页", exact: true }).click();
  await navigation.getByRole("button", { name: "创作", exact: true }).click();
  await expect(deck(page)).toHaveCount(1);
  expect(await page.evaluate(() => window.__minimaxSharedDeck === document.querySelector('[data-ui="director-composer"]'))).toBe(true);
  await expect(page.locator("#prompt")).toHaveValue("镜头随人物进入明亮的制作室，保持参考画面的自然光。🎬");
  await expect(page.locator("#model")).toContainText(maximum.display_name);
  expect(state.submissions).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test("multimodal Ark references narrow to H3 then disappear completely for Max", async ({ page }) => {
  const errors = trackErrors(page);
  const ark = arkModelResponse("seedance-2.5");
  const state = await installMiniMaxH3PlatformFixture(page, { models: [ark, ...minimaxH3DiscoveryResponses()] });
  await page.goto("/creation", { waitUntil: "commit" });
  await openDraft(page);
  await selectModel(page, ark);
  await page.locator("#prompt").fill("从真实参考的光线、运动和声音创作新的镜头。");
  await selectMode(page, "video_to_video");
  await panel(page, "references");
  for (const [kind, count, mimeType, extension] of [["image", 9, "image/png", "png"], ["video", 3, "video/mp4", "mp4"], ["audio", 3, "audio/wav", "wav"]]) {
    const group = page.locator(`.media-input-group[data-kind="${kind}"]`);
    await expect(group.locator(".media-slot-add")).toHaveCount(1);
    await group.locator('input[type="file"]').setInputFiles(Array.from({ length: count }, (_, index) => ({
      name: `reference-${kind}-${index}.${extension}`, mimeType, buffer: Buffer.from(`Synthetic ${kind} ${index}; no provider call`),
    })));
    await expect(group.locator(".media-slot-filled")).toHaveCount(count);
  }
  await closePanel(page, "references");
  await selectModel(page, minimaxH3ModelResponse("minimax-h3"));
  await panel(page, "references");
  await expect(page.locator('.media-input-group[data-kind="image"] .media-slot-filled')).toHaveCount(6);
  await expect(page.locator('.media-input-group[data-kind="video"] .media-slot-filled')).toHaveCount(3);
  await expect(page.locator('.media-input-group[data-kind="audio"] .media-slot-filled')).toHaveCount(3);
  await closePanel(page, "references");
  await selectMode(page, "image_to_video");
  await panel(page, "references");
  await expect(page.locator('.media-input-group[data-kind="video"]')).toHaveCount(0);
  await expect(page.locator('.media-input-group[data-kind="image"] .media-slot-add')).toHaveCount(1);
  await closePanel(page, "references");
  await selectModel(page, minimaxH3ModelResponse("minimax-h3-max"));
  await expect(page.locator(".media-slot-filled")).toHaveCount(0);
  await expect(page.locator(".director-reference-trigger")).toHaveCount(0);
  await expect(page.locator("#generation-mode [role=tab]")).toHaveCount(1);
  await safeLayout(page);
  expect(state.assets).toHaveLength(15);
  expect(state.submissions).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test("unknown H3 submission restores 7000 emoji and exact payload/quote/idempotency after discovery changes", async ({ page }) => {
  const errors = trackErrors(page);
  const models = minimaxH3DiscoveryResponses();
  const state = await installMiniMaxH3PlatformFixture(page, { models, uncertainSubmission: true });
  await page.goto("/creation", { waitUntil: "commit" });
  await openDraft(page);
  await selectModel(page, models[0]);
  const originalPrompt = "🎬".repeat(7000);
  await page.locator("#prompt").fill(originalPrompt);
  await panel(page, "specs");
  await page.getByRole("button", { name: "2k", exact: true }).click();
  await page.getByRole("button", { name: "15 秒", exact: true }).click();
  await closePanel(page, "specs");
  await expect(page.locator(".generate-button")).toBeEnabled();
  await page.locator(".generate-button").click();
  await expect(page.locator(".generate-button")).toHaveAccessibleName("确认原提交");
  const original = state.submissions[0];
  models[0].unit_price_points = 999;
  models[0].capability_version = 9;
  models[0].quote_revision = `sha256:${"f".repeat(64)}`;
  models[0].effective_capabilities.modes.text_to_video.limits.resolutions = ["768p"];
  models[0].effective_capabilities.modes.text_to_video.limits.duration_seconds = [4];
  await page.reload({ waitUntil: "commit" });
  await openDraft(page);
  await expect(page.locator("#prompt")).toHaveValue(originalPrompt);
  await expect(page.locator(".composer-prompt-section .field-heading span")).toHaveText("7000 字，只读");
  await expect(page.locator(".generate-button")).toHaveAccessibleName("确认原提交");
  await panel(page, "specs");
  await expect(page.locator(".director-readonly-grid")).toContainText("15 秒");
  await expect(page.locator(".director-readonly-grid")).toContainText("2k");
  await expect(page.locator(".director-choice-group")).toHaveCount(0);
  await closePanel(page, "specs");
  await page.locator(".generate-button").click();
  if (state.submissions.length <= 2) {
    await expect(page.locator(".generate-button")).toHaveAccessibleName("确认原提交");
    await page.locator(".generate-button").click();
  }
  await expect.poll(() => state.tasks.length).toBe(1);
  await openDraft(page);
  await expect(page.locator(".generate-button")).toHaveAccessibleName("任务已接收");
  for (const request of state.submissions) expect(request).toEqual(original);
  expect(original.body.request_payload.prompt).toBe(originalPrompt);
  expect(original.body.request_payload.resolution).toBe("2k");
  expect(original.body.request_payload.duration_seconds).toBe(15);
  expect(original.body.expected_capability_version).toBe(8);
  expect(original.body.expected_quote_revision).not.toBe(models[0].quote_revision);
  expect(original.body.idempotency_key).toBeTruthy();
  expect(state.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

for (const scenario of ["ungranted", "readiness_missing"]) {
  test(`MiniMax ${scenario} has one disabled reason and no fabricated access`, async ({ page }, testInfo) => {
    const errors = trackErrors(page);
    const state = await installMiniMaxH3PlatformFixture(page, {
      models: scenario === "ungranted" ? [] : minimaxH3DiscoveryResponses({ readiness: "missing" }),
    });
    await page.goto("/creation", { waitUntil: "commit" });
    await openDraft(page);
    if (scenario === "ungranted") await expect(page.locator("#prompt")).toBeDisabled();
    else await page.locator("#prompt").fill("没有有效授权和就绪证据不能开始生成。");
    await expect(page.locator(".generate-button")).toBeDisabled();
    await expect(page.locator("#composer-submit-message")).toHaveCount(1);
    await expect(page.locator("#composer-submit-message")).toContainText(scenario === "ungranted" ? "公司当前没有已授权模型" : "生成条件尚未确认");
    await expect(page.locator('select[aria-label="切换演示账号"]')).toHaveCount(0);
    await safeLayout(page);
    await page.screenshot({ path: testInfo.outputPath(`minimax-${scenario}.png`) });
    expect(state.submissions).toEqual([]);
    expect(state.unexpected).toEqual([]);
    expect(errors).toEqual([]);
  });
}
