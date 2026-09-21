import { expect, test } from "@playwright/test";

test.setTimeout(120_000);

function captureBrowserFailures(page) {
  const failures = [];
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
  return failures;
}

async function expectNoHorizontalPageOverflow(page) {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: Math.max(
      document.documentElement.scrollWidth,
      document.body?.scrollWidth || 0,
    ),
  }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport + 1);
}

async function recordedCalls(page, method) {
  return page.evaluate((targetMethod) => (
    globalThis.__publishingFixture.calls.filter((call) => call.method === targetMethod)
  ), method);
}

async function openFixture(page, query = "") {
  await page.goto(`/tests/browser-fixtures/publishing-center.html${query}`, {
    waitUntil: "commit",
  });
  await expect(page.getByRole("heading", { name: "发布", exact: true })).toBeVisible();
  await expect(page.getByText("待批准短片", { exact: true })).toBeVisible();
}

function jobCard(page, title) {
  return page.locator(".publication-job").filter({ hasText: title });
}

test("publishing creates one approval-gated job with a stable idempotency key", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await openFixture(page);

  const trigger = page.getByRole("button", { name: "新建发布" });
  await trigger.focus();
  await trigger.press("Enter");

  const dialog = page.getByRole("dialog", { name: "安排作品发布" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭发布编辑器" })).toBeFocused();
  await dialog.getByLabel("发布标题").fill("浏览器新建任务");
  await dialog.getByLabel("发布文案").fill("由行为级浏览器测试提交的发布文案");
  await dialog.getByRole("button", { name: "提交待审核" }).click();

  await expect(page.getByText("发布任务已提交，当前处于待审核状态。")).toBeVisible();
  await expect(page.getByText("浏览器新建任务", { exact: true })).toBeVisible();
  const createCalls = await recordedCalls(page, "createPublicationJob");
  expect(createCalls).toHaveLength(1);
  expect(createCalls[0].payload).toMatchObject({
    artifactId: "artifact-browser-1",
    connectionId: "connection-browser-1",
    title: "浏览器新建任务",
    caption: "由行为级浏览器测试提交的发布文案",
    timezone: "Asia/Shanghai",
  });
  expect(createCalls[0].payload.idempotencyKey).toMatch(/^[0-9a-f-]{20,}$/i);
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});

test("publishing keeps approval cancel retry and unknown reconciliation as distinct actions", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await openFixture(page);

  await jobCard(page, "待批准短片").getByRole("button", { name: "批准发布" }).click();
  await expect(page.getByText("发布任务已批准。")).toBeVisible();
  expect(await recordedCalls(page, "approvePublicationJob")).toEqual([
    { method: "approvePublicationJob", payload: { id: "job-pending" } },
  ]);

  await jobCard(page, "待取消排期").getByRole("button", { name: "取消待发布任务" }).click();
  await expect(page.getByText("发布任务已取消。")).toBeVisible();
  expect(await recordedCalls(page, "cancelPublicationJob")).toHaveLength(1);

  await jobCard(page, "可恢复任务").getByRole("button", { name: "重新进入发布队列" }).click();
  await expect(page.getByText("发布任务已重新进入队列。")).toBeVisible();
  expect(await recordedCalls(page, "retryPublicationJob")).toHaveLength(1);

  const unknown = jobCard(page, "渠道结果待核对");
  await expect(unknown.getByRole("button", { name: "重新进入发布队列" })).toHaveCount(0);
  await expect(unknown.getByText("结果未知任务禁止自动重试。")).toBeVisible();
  await unknown.getByRole("button", { name: "核对并确认渠道结果" }).click();
  const reconcile = page.getByRole("dialog", { name: "核对并确认渠道结果" });
  await reconcile.getByLabel("渠道作品号").fill("douyin-post-browser-1");
  await reconcile.getByLabel("渠道作品地址（可选）").fill("https://example.com/published/browser-1");
  await reconcile.getByRole("button", { name: "确认渠道结果" }).click();
  await expect(page.getByText("渠道结果已确认为发布成功。")).toBeVisible();
  expect(await recordedCalls(page, "reconcilePublicationJob")).toEqual([
    {
      method: "reconcilePublicationJob",
      payload: {
        id: "job-unknown",
        outcome: "published",
        externalPostId: "douyin-post-browser-1",
        externalPostUrl: "https://example.com/published/browser-1",
        errorMessage: "",
      },
    },
  ]);
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});

test("publishing confirms OAuth return through server state and scrubs callback parameters", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await openFixture(page, "?publishing_oauth=connected&provider=douyin");

  await expect(page.getByText("抖音账号已由服务端确认并可用于发布。")).toBeVisible();
  await expect(page).not.toHaveURL(/publishing_oauth|provider=/);
  expect((await recordedCalls(page, "listPublisherConnections")).length).toBeGreaterThanOrEqual(2);
  expect(failures).toEqual([]);
});

test("publishing dialogs trap keyboard focus, close with Escape and restore the trigger", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await openFixture(page);

  const trigger = page.getByRole("button", { name: "新建发布" });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "安排作品发布" });
  await expect(dialog).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭发布编辑器" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "提交待审核" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "关闭发布编辑器" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});
