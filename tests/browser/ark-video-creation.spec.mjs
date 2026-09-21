import { expect, test } from "@playwright/test";
import { installArkPlatformFixture } from "../browser-fixtures/ark-video-platform.mjs";
import { arkDiscoveryResponses, arkModelResponse } from "../fixtures/ark-video-discovery.mjs";
import { modeLabel } from "../../src/modelCapabilities.js";

// Run: npx playwright test --config tests/browser/ark-video.playwright.config.mjs
// Default demo-mode browser runs deliberately skip this live-transport suite.
test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.config.metadata.arkVideoLive !== true,
    "Requires the dedicated live-mode Ark configuration");
});
test.setTimeout(120_000);

const composer = (page) => page.locator('[data-ui="director-composer"]');
const isPhone = (page) => (page.viewportSize()?.width || 0) <= 620;

async function openDraft(page) {
  // Like the existing critical-surface suite, allow the first Vite transform
  // of the full production App to finish on a cold local/CI browser.
  await expect(composer(page)).toHaveCount(1, { timeout: 90_000 });
  if (isPhone(page) && await composer(page).getAttribute("data-mobile-open") !== "true") {
    await page.locator("#mobile-composer-launcher").click();
  }
}

async function openPanel(page, panel) {
  await openDraft(page);
  if (await composer(page).getAttribute("data-panel") !== panel) {
    await page.locator(`[data-composer-panel-trigger="${panel}"]:visible`).first().click();
  }
  await expect(page.locator(`#composer-${panel}-panel`)).toBeVisible();
}

async function closePanel(page, panel) {
  // Phones intentionally hide the desktop close icon. Exercise the shared
  // keyboard-dismiss contract rather than clicking a hidden desktop control.
  await page.locator(`#composer-${panel}-panel [data-composer-panel-focus]`).first().focus();
  await page.keyboard.press("Escape");
  await expect(composer(page)).toHaveAttribute("data-panel", "closed");
}

async function selectModel(page, model) {
  await openPanel(page, "model");
  await page.locator(".director-model-option").filter({
    has: page.getByText(model.display_name, { exact: true }),
  }).click();
  await expect(page.locator("#model")).toContainText(model.display_name);
}

async function selectMode(page, mode) {
  if (isPhone(page)) {
    await openPanel(page, "recipe");
    await page.locator("#composer-recipe-panel").getByRole("button", { name: modeLabel(mode), exact: true }).click();
    await closePanel(page, "recipe");
  } else {
    await page.locator("#generation-mode").getByRole("tab", { name: modeLabel(mode), exact: true }).click();
  }
}

async function expectSafeLayout(page) {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
  }));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport + 1);
  const action = page.locator(".generate-button");
  await expect(action).toBeVisible();
  const box = await action.boundingBox();
  const viewport = page.viewportSize();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
  if (isPhone(page)) {
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
  }
}

async function expectSingleLineDurationLabels(page) {
  await page.evaluate(async () => { await document.fonts.ready; });
  const labels = await page.locator(".director-choice-group.is-duration button").evaluateAll((buttons) => (
    buttons.map((button) => {
      // React may split the numeral and unit into separate text nodes. Measure
      // their real rendered line boxes together; a CSS declaration alone does
      // not prove that a two-digit duration and its Chinese unit fit.
      const walker = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
      const rects = [];
      while (walker.nextNode()) {
        if (!walker.currentNode.textContent.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(walker.currentNode);
        rects.push(...Array.from(range.getClientRects()).filter((rect) => rect.width > 0 && rect.height > 0));
      }
      const lineCenters = [];
      for (const rect of rects) {
        const center = rect.top + rect.height / 2;
        if (!lineCenters.some((line) => Math.abs(line - center) <= 2)) lineCenters.push(center);
      }
      const bounds = button.getBoundingClientRect();
      return {
        label: button.textContent.trim(),
        lineCenters,
        textLeft: Math.min(...rects.map((rect) => rect.left)),
        textRight: Math.max(...rects.map((rect) => rect.right)),
        left: bounds.left,
        right: bounds.right,
        width: bounds.width,
        height: bounds.height,
      };
    })
  ));
  expect(labels.length).toBeGreaterThan(0);
  for (const label of labels) {
    expect(label.lineCenters, `${label.label} must render on one actual text line`).toHaveLength(1);
    expect(label.textLeft, `${label.label} must fit inside its button`).toBeGreaterThanOrEqual(label.left - 1);
    expect(label.textRight, `${label.label} must fit inside its button`).toBeLessThanOrEqual(label.right + 1);
    if (isPhone(page)) {
      // Fractional scroll coordinates can report a 44px box as 43.999939px.
      expect(Math.round(label.width * 100) / 100, `${label.label} phone target width`).toBeGreaterThanOrEqual(44);
      expect(Math.round(label.height * 100) / 100, `${label.label} phone target height`).toBeGreaterThanOrEqual(44);
    }
  }
}

function recordErrors(page) {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  return errors;
}

test("all seven discovered Ark models use one production Director Deck with exact specs", async ({ page }, testInfo) => {
  const errors = recordErrors(page);
  const state = await installArkPlatformFixture(page);
  const models = arkDiscoveryResponses();
  await page.goto("/creation", { waitUntil: "commit" });
  await openDraft(page);
  await page.locator("#prompt").fill("同一个导演台保留制作说明与选定规格。");
  await expect(page.locator('select[aria-label="切换演示账号"]')).toHaveCount(0);
  await openPanel(page, "model");
  await expect(page.locator(".director-model-option")).toHaveCount(7);
  for (const model of models) {
    await selectModel(page, model);
    await expect(page.locator("#composer-media-tab-image, #composer-media-tab-audio")).toHaveCount(0);
    const modes = Object.keys(model.effective_capabilities.modes);
    await expect(page.locator("#generation-mode [role=tab]")).toHaveCount(modes.length);
    for (const mode of modes) {
      await expect(page.locator("#generation-mode").getByRole("tab", {
        name: modeLabel(mode), exact: true, includeHidden: true,
      })).toBeAttached();
    }
    await openPanel(page, "specs");
    const limits = model.effective_capabilities.modes.text_to_video.limits;
    await expect(page.locator('.director-fixed-specs [data-specification="outputCount"]')).toContainText("1 个");
    await expect(page.locator(".director-choice-group.is-count")).toHaveCount(0);
    await expect(page.locator(".director-choice-group.is-duration button")).toHaveText(
      limits.duration_seconds.map((value) => `${value} 秒`),
    );
    await expect(page.locator(".director-choice-group.is-resolution button")).toHaveText(limits.resolutions);
    await expectSingleLineDurationLabels(page);
    await expectSafeLayout(page);
  }

  await selectModel(page, arkModelResponse("seedance-2.0"));
  await openPanel(page, "specs");
  await page.getByRole("button", { name: "4k", exact: true }).click();
  await page.getByRole("button", { name: "15 秒", exact: true }).click();
  await selectModel(page, arkModelResponse("seedance-2.0-fast"));
  await openPanel(page, "specs");
  await expect(page.locator(".director-choice-group.is-resolution button")).toHaveText(["480p", "720p"]);
  await expect(page.locator(".director-choice-group.is-resolution button.is-active")).not.toHaveText("4k");

  await selectModel(page, arkModelResponse("seedance-2.5"));
  await openPanel(page, "specs");
  const thirty = page.getByRole("button", { name: "30 秒", exact: true });
  await thirty.focus();
  await expect(thirty).toBeFocused();
  await thirty.press("Enter");
  await expect(thirty).toHaveAttribute("aria-pressed", "true");
  await expectSingleLineDurationLabels(page);
  await expectSafeLayout(page);
  await page.screenshot({ path: testInfo.outputPath("ark-30-second-specs.png") });
  await closePanel(page, "specs");
  await page.evaluate(() => {
    window.__arkSharedDeck = document.querySelector('[data-ui="director-composer"]');
  });
  const navigation = page.getByRole("navigation", { name: "工作台导航" });
  await navigation.getByRole("button", { name: "首页", exact: true }).click();
  await expect(composer(page)).toHaveCount(1);
  await expect(page.locator("#prompt")).toHaveValue("同一个导演台保留制作说明与选定规格。");
  await navigation.getByRole("button", { name: "创作", exact: true }).click();
  await expect(composer(page)).toHaveCount(1);
  expect(await page.evaluate(() => window.__arkSharedDeck === document.querySelector('[data-ui="director-composer"]'))).toBe(true);
  await openPanel(page, "specs");
  await expect(page.getByRole("button", { name: "30 秒", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(state.submissions).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test("multimodal media capacity and stale values reconcile when moving to a one-image Ark model", async ({ page }) => {
  const errors = recordErrors(page);
  const state = await installArkPlatformFixture(page);
  await page.goto("/creation", { waitUntil: "commit" });
  await openDraft(page);
  await page.locator("#prompt").fill("根据提供的画面、视频与参考音频制作视频。");
  await selectModel(page, arkModelResponse("seedance-2.5"));
  await selectMode(page, "video_to_video");
  await openPanel(page, "references");
  const source = arkModelResponse("seedance-2.5").effective_capabilities.modes.video_to_video.limits;
  for (const [kind, count, mimeType, extension] of [
    ["image", source.max_images, "image/png", "png"],
    ["video", source.max_videos, "video/mp4", "mp4"],
    ["audio", source.max_audio, "audio/wav", "wav"],
  ]) {
    const group = page.locator(`.media-input-group[data-kind="${kind}"]`);
    await expect(group.locator(".media-slot-add")).toHaveCount(1);
    await group.locator('input[type="file"]').setInputFiles(Array.from({ length: count }, (_, index) => ({
      name: `contract-${kind}-${index + 1}.${extension}`,
      mimeType,
      buffer: Buffer.from(`Synthetic upload bytes ${kind} ${index}; never a real provider input`),
    })));
    await expect(group.locator(".media-slot-filled")).toHaveCount(count);
    await expect(group.locator(".media-slot-add")).toHaveCount(0);
    await expect(group.locator('input[type="file"]')).toBeDisabled();
  }
  expect(state.assets).toHaveLength(source.max_images + source.max_videos + source.max_audio);
  await closePanel(page, "references");
  await selectModel(page, arkModelResponse("seedance-1.0-pro-fast"));
  await selectMode(page, "image_to_video");
  await openPanel(page, "references");
  // Switching away from the now-unsupported video mode chooses the model's
  // first safe mode, so all former multimodal inputs have already been removed.
  await expect(page.locator('.media-input-group[data-kind="video"]')).toHaveCount(0);
  await expect(page.locator('.media-input-group[data-kind="audio"]')).toHaveCount(0);
  await expect(page.locator(".media-slot-filled")).toHaveCount(0);
  const imageInput = page.locator('#media-input-image');
  await expect(imageInput).not.toHaveAttribute("multiple", "");
  await imageInput.setInputFiles({ name: "one-frame.png", mimeType: "image/png", buffer: Buffer.from("Synthetic frame") });
  await expect(page.locator(".media-slot-filled")).toHaveCount(1);
  await page.locator(".media-slot-filled").click();
  await expect(page.locator(".media-slot-filled")).toHaveCount(0);
  await expect(page.locator("#composer-submit-message")).toContainText("图生视频至少需要 1 张参考图");
  await expect(page.locator(".generate-button")).toBeDisabled();
  await expectSafeLayout(page);
  expect(state.submissions).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test("uncertain Ark submission reloads and confirms the exact original request and idempotency key", async ({ page }) => {
  const errors = recordErrors(page);
  const state = await installArkPlatformFixture(page, { uncertainSubmission: true });
  await page.goto("/creation", { waitUntil: "commit" });
  await openDraft(page);
  await selectModel(page, arkModelResponse("seedance-2.5"));
  await page.locator("#prompt").fill("原始三十秒请求，刷新后不能隐式改变参数或创建第二条任务。");
  await openPanel(page, "specs");
  await page.getByRole("button", { name: "30 秒", exact: true }).click();
  await closePanel(page, "specs");
  await expect(page.locator(".generate-button")).toBeEnabled();
  await page.locator(".generate-button").click();
  await expect(page.locator(".generate-button")).toHaveAccessibleName("确认原提交");
  expect(state.submissions.length).toBeGreaterThanOrEqual(1);
  const original = state.submissions[0];
  await page.reload({ waitUntil: "commit" });
  await openDraft(page);
  await expect(page.locator(".generate-button")).toHaveAccessibleName("确认原提交");
  await openPanel(page, "specs");
  await expect(page.locator(".director-readonly-grid")).toContainText("30 秒");
  await expect(page.locator(".director-choice-group")).toHaveCount(0);
  await closePanel(page, "specs");
  await page.locator(".generate-button").click();
  // App-level malformed task responses remain uncertain after each attempt;
  // confirm once more only when the intercepted fixture still reports unknown.
  if (state.submissions.length <= 2) {
    await expect(page.locator(".generate-button")).toHaveAccessibleName("确认原提交");
    await page.locator(".generate-button").click();
  }
  await expect.poll(() => state.tasks.length).toBe(1);
  // Successful phone submissions return to the watch state by design.
  // Reopen the same deck before asserting the execution control's name.
  await openDraft(page);
  await expect(page.locator(".generate-button")).toHaveAccessibleName("任务已接收");
  for (const submission of state.submissions) expect(submission).toEqual(original);
  expect(original.body.request_payload.duration_seconds).toBe(30);
  expect(Object.keys(original.body.request_payload).sort()).toEqual([
    "aspect_ratio", "duration_seconds", "mode", "output_count", "prompt", "resolution",
  ]);
  expect(original.body.expected_capability_version).toBe(7);
  expect(original.body.expected_quote_revision).toMatch(/^sha256:[0-9a-f]{64}$/);
  expect(original.body.idempotency_key).toBeTruthy();
  expect(state.unexpected).toEqual([]);
  expect(errors).toEqual([]);
});

test("uploads reserve the available capacity and keep every input and submit locked until the whole batch finishes", async ({ page }) => {
  const errors = recordErrors(page);
  const pending = [];
  const state = await installArkPlatformFixture(page, {
    beforeUpload: ({ name }) => name.startsWith("held-")
      ? new Promise((resolve) => pending.push(resolve)) : undefined,
  });
  try {
    await page.goto("/creation", { waitUntil: "commit" });
    await openDraft(page);
    await selectModel(page, arkModelResponse("seedance-2.5"));
    await selectMode(page, "image_to_video");
    await page.locator("#prompt").fill("容量与上传串行边界验证。");
    await openPanel(page, "references");
    await page.locator("#media-input-image").setInputFiles({ name: "frame.png", mimeType: "image/png", buffer: Buffer.from("synthetic frame") });
    await expect(page.locator('.media-input-group[data-kind="image"] .media-slot-filled')).toHaveCount(1);
    await page.locator("#media-input-audio").setInputFiles(Array.from({ length: 4 }, (_, index) => ({
      name: `held-${index}.wav`, mimeType: "audio/wav", buffer: Buffer.from("synthetic audio"),
    })));
    for (let index = 0; index < 3; index += 1) {
      await expect.poll(() => pending.length).toBe(index + 1);
      await expect(page.locator("#media-input-image")).toBeDisabled();
      await expect(page.locator("#media-input-audio")).toBeDisabled();
      await expect(page.locator(".generate-button")).toBeDisabled();
      expect(state.submissions).toEqual([]);
      pending[index]();
    }
    await expect(page.locator('.media-input-group[data-kind="audio"] .media-slot-filled')).toHaveCount(3);
    await expect(page.locator("#media-input-image")).toBeEnabled();
    await expect(page.locator("#media-input-audio")).toBeDisabled();
    await expect(page.locator(".generate-button")).toBeEnabled();
    expect(state.assets.filter((asset) => asset.media_type === "audio")).toHaveLength(3);
    await page.locator(".generate-button").click();
    await expect.poll(() => state.submissions.length).toBe(1);
    expect(state.submissions[0].body.request_payload.assets).toHaveLength(4);
    await openDraft(page);
    if (isPhone(page)) {
      await expect(composer(page)).toHaveAttribute("data-panel", "closed");
      await expect(page.locator("#prompt")).toBeVisible();
      await expect(page.locator("#prompt")).toBeFocused();
    }
    expect(state.unexpected).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    pending.forEach((resolve) => resolve());
  }
});

test("an old upload can enter the private library but cannot reappear in a different model draft", async ({ page }) => {
  const errors = recordErrors(page);
  let releaseUpload;
  const state = await installArkPlatformFixture(page, {
    beforeUpload: ({ name }) => name === "old-frame.png"
      ? new Promise((resolve) => { releaseUpload = resolve; }) : undefined,
  });
  try {
    await page.goto("/creation", { waitUntil: "commit" });
    await openDraft(page);
    await selectModel(page, arkModelResponse("seedance-2.5"));
    await selectMode(page, "image_to_video");
    await page.locator("#prompt").fill("换模型之后不能带回旧上传。");
    await openPanel(page, "references");
    await page.locator("#media-input-image").setInputFiles({ name: "old-frame.png", mimeType: "image/png", buffer: Buffer.from("synthetic old frame") });
    await expect.poll(() => Boolean(releaseUpload)).toBe(true);
    await selectModel(page, arkModelResponse("seedance-1.0-pro-fast"));
    await expect(page.locator(".generate-button")).toBeDisabled();
    releaseUpload();
    await expect.poll(() => state.assets.length).toBe(1);
    await openPanel(page, "references");
    await expect(page.locator("#media-input-image")).toBeEnabled();
    await expect(page.locator(".media-slot-filled")).toHaveCount(0);
    await expect(page.locator("#composer-submit-message")).toContainText("图生视频至少需要 1 张参考图");
    await page.locator("#media-input-image").setInputFiles({ name: "current-frame.png", mimeType: "image/png", buffer: Buffer.from("synthetic current frame") });
    await expect(page.locator(".media-slot-filled")).toHaveCount(1);
    await expect(page.locator(".media-slot-filled")).toContainText("current-frame.png");
    await expect(page.locator("#media-input-video, #media-input-audio")).toHaveCount(0);
    await expect(page.locator(".generate-button")).toBeEnabled();
    expect(state.submissions).toEqual([]);
    expect(state.unexpected).toEqual([]);
    expect(errors).toEqual([]);
  } finally {
    releaseUpload?.();
  }
});

for (const stateName of ["ungranted", "readiness_missing"]) {
  test(`Ark ${stateName} remains unavailable without fabricated models or readiness`, async ({ page }, testInfo) => {
    const errors = recordErrors(page);
    const state = await installArkPlatformFixture(page, {
      models: stateName === "ungranted" ? [] : arkDiscoveryResponses({ readiness: "missing" }),
    });
    await page.goto("/creation", { waitUntil: "commit" });
    await openDraft(page);
    if (stateName === "ungranted") {
      await expect(page.locator("#prompt")).toBeDisabled();
      await expect(page.locator('[data-composer-panel-trigger="specs"], [data-composer-panel-trigger="references"], .director-face-control')).toHaveCount(0);
    } else {
      await page.locator("#prompt").fill("没有授权或就绪证据时必须阻止生成。");
    }
    await expect(page.locator(".generate-button")).toBeDisabled();
    await expect(page.locator("#composer-submit-message")).toContainText(
      stateName === "ungranted" ? "公司当前没有已授权模型" : "生成条件尚未确认",
    );
    await expect(page.locator("#composer-submit-message")).toHaveCount(1);
    await expect(page.locator('select[aria-label="切换演示账号"]')).toHaveCount(0);
    await expectSafeLayout(page);
    await page.screenshot({ path: testInfo.outputPath(`ark-${stateName}.png`) });
    expect(state.submissions).toEqual([]);
    expect(state.unexpected).toEqual([]);
    expect(errors).toEqual([]);
  });
}
