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

function captureForbiddenOwnerContextTransitions(page) {
  const transitions = [];
  const record = (kind, rawUrl) => {
    const url = new URL(rawUrl, "http://127.0.0.1");
    if (
      /\/auth\/logout(?:\/|$)/.test(url.pathname)
      || url.searchParams.get("prompt") === "select_account"
    ) {
      transitions.push(`${kind}: ${url.href}`);
    }
  };
  page.on("request", (request) => record("request", request.url()));
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) record("navigation", frame.url());
  });
  return transitions;
}

async function useDemoPersona(page, persona) {
  await page.addInitScript((value) => {
    // Playwright injects init scripts into child frames too. The 3D editor is
    // deliberately sandboxed without allow-same-origin, so only the product's
    // top-level document may touch this demo-session key.
    if (window !== window.top) return;
    window.sessionStorage.setItem("ai-video.demo-persona", value);
  }, persona);
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

async function expectInsideViewport(locator, page) {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  const viewport = page.viewportSize();
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width + 1);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
}

async function expectTouchTarget(locator, page) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  if ((page.viewportSize()?.width || 0) <= 820) {
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
  }
}

function ownerCreationEntry(page) {
  return page.getByRole("button", { name: /个人创作/ });
}

async function revealReturnToPlatform(page) {
  if ((page.viewportSize()?.width || 0) <= 620) {
    const menu = page.locator(".studio-mobile-command-menu");
    const trigger = menu.locator("summary");
    await trigger.focus();
    await expect(trigger).toBeFocused();
    await trigger.press("Enter");
    await expect(menu).toHaveAttribute("open", "");
  }
  return page.getByRole("button", { name: "返回 Platform" });
}

async function expectDemoPersona(page, persona) {
  const controls = page.locator('select[aria-label="切换演示账号"]');
  await expect(controls.first()).toBeAttached();
  await expect.poll(() => controls.evaluateAll((nodes) => nodes.map((node) => node.value)))
    .toEqual(Array.from({ length: await controls.count() }, () => persona));
}

test("creation keeps its real execution action visible and keyboard reachable", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await useDemoPersona(page, "operator");
  await page.goto("/creation", { waitUntil: "commit" });

  // The first Vite request cold-transforms the largest lazy route. Keep this
  // within the test's 120s ceiling so slower CI runners exercise the product
  // instead of failing before the creation bundle has finished loading.
  await expect(page.locator("#creation-hub-title")).toBeAttached({ timeout: 90_000 });
  await expect(page.locator('[data-ui="creation-invitation-entry"]')).toBeVisible();
  await expect(page.locator("#creation-entry-title")).toBeVisible();
  if ((page.viewportSize()?.width || 0) <= 620) {
    const launcher = page.locator("#mobile-composer-launcher");
    await expectInsideViewport(page.locator(".creation-entry-copy"), page);
    await expectInsideViewport(launcher, page);
    await launcher.click();
    await expect(page.locator(".community-composer")).toHaveAttribute("data-mobile-open", "true");
    await expect(page.locator(".main-canvas")).toBeHidden();
  }
  const generate = page.locator(".generate-button");
  await expectInsideViewport(generate, page);
  await expect(generate).toBeEnabled({ timeout: 40_000 });
  await expect(generate).toHaveAccessibleName("开始生成");
  await generate.focus();
  await expect(generate).toBeFocused();
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});

test("creation adapts inputs, face options and fixed specs to the selected model", async ({ page }, testInfo) => {
  const failures = captureBrowserFailures(page);
  await useDemoPersona(page, "operator");
  await page.goto("/creation", { waitUntil: "commit" });
  const deck = page.locator('[data-ui="director-composer"]');
  await expect(deck).toHaveCount(1, { timeout: 90_000 });
  const isPhone = page.viewportSize().width <= 620;
  if (isPhone) await page.locator("#mobile-composer-launcher").click();
  const openPanel = async (name) => {
    if (await deck.getAttribute("data-panel") !== name) {
      await page.locator(`[data-composer-panel-trigger="${name}"]:visible`).first().click();
    }
    await expect(page.locator(`#composer-${name}-panel`)).toBeVisible();
  };
  const chooseModel = async (name) => {
    await openPanel("model");
    await page.locator(".director-model-option").filter({ has: page.getByText(name, { exact: true }) }).click();
    await expect(deck).toHaveAttribute("data-panel", "closed");
    await expect(page.locator("#model")).toContainText(name);
  };
  await page.locator("#prompt").fill("保留我的想法，只根据模型调整输入。");
  await expect(page.locator("#composer-media-tab-video")).toBeAttached();
  await expect(page.locator("#composer-media-tab-image, #composer-media-tab-audio")).toHaveCount(0);
  await openPanel("readiness");
  await page.locator('.director-face-control input[type="checkbox"]').check();
  await openPanel("specs");
  await expect(page.locator('.director-fixed-specs [data-specification="outputCount"]')).toContainText("1 个");
  await expect(page.locator(".director-choice-group.is-count")).toHaveCount(0);
  await chooseModel("Rush Video 1.6");
  await openPanel("readiness");
  await expect(page.locator(".director-face-control")).toHaveCount(0);
  await openPanel("specs");
  await expect(page.locator(".director-choice-group.is-resolution")).toHaveCount(0);
  await expect(page.locator('.director-fixed-specs [data-specification="resolution"]')).toContainText("720p");
  await expect(page.locator(".director-fixed-specs button, .director-fixed-specs input, .director-fixed-specs [tabindex]")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("model-adaptive-fixed-specs.png") });

  await chooseModel("FrameFlow Lite");
  await expect(page.locator("#composer-media-tab-video, #composer-media-tab-audio")).toHaveCount(0);
  await expect(page.locator("#composer-media-tab-image")).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#prompt")).toHaveValue("保留我的想法，只根据模型调整输入。");
  await openPanel("references");
  await expect(page.locator(".media-input-group")).toHaveCount(1);
  await expect(page.locator("#media-input-image")).not.toHaveAttribute("multiple", "");
  await expect(page.locator("#media-input-video, #media-input-audio")).toHaveCount(0);
  await openPanel("specs");
  await expect(page.locator(".director-choice-group.is-duration")).toHaveCount(0);
  await expect(page.locator(".director-choice-group.is-count button")).toHaveText(["1 个", "2 个", "3 个", "4 个"]);
  const four = page.getByRole("button", { name: "4 个", exact: true });
  await four.focus();
  await four.press("Enter");
  await expect(four).toHaveAttribute("aria-pressed", "true");
  await expectInsideViewport(page.locator(".generate-button"), page);
  await expectNoHorizontalPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("model-adaptive-image.png") });
  await chooseModel("CinemoX Pro 2.1");
  await openPanel("readiness");
  await expect(page.locator('.director-face-control input[type="checkbox"]')).not.toBeChecked();
  await expect(deck).toHaveCount(1);
  expect(failures).toEqual([]);
});

test("home and creation share one Director Deck and one continuous draft", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  const isPhone = (page.viewportSize()?.width || 0) <= 620;
  const sharedDraft = "共享导演台草稿 · 首页与创作保持一致";
  await useDemoPersona(page, "operator");
  await page.goto("/", { waitUntil: "commit" });

  await expect(page.locator("#community-title")).toBeAttached({ timeout: 90_000 });
  const composer = page.locator('[data-ui="director-composer"]');
  const prompt = page.locator("#prompt");
  await expect(composer).toHaveCount(1);
  await page.evaluate(() => {
    window.__sharedDirectorDeckProbe = document.querySelector('[data-ui="director-composer"]');
  });

  if (isPhone) {
    const main = page.locator(".main-canvas");
    const launcher = page.locator("#mobile-composer-launcher");
    const [mainBox, composerBox] = await Promise.all([main.boundingBox(), composer.boundingBox()]);
    expect(mainBox).not.toBeNull();
    expect(composerBox).not.toBeNull();
    expect(Math.abs((mainBox.y + mainBox.height) - composerBox.y)).toBeLessThanOrEqual(1);
    expect(composerBox.height).toBe(72);
    await launcher.click();
    await expect(composer).toHaveAttribute("data-mobile-open", "true");
    await expect(main).toBeHidden();
    await expect(page.getByRole("button", { name: "返回首页灵感" })).toBeVisible();
    await expectTouchTarget(page.locator(".generate-button"), page);
  }

  await prompt.fill(sharedDraft);
  const studioNav = page.getByRole("navigation", { name: "工作台导航" });
  await studioNav.getByRole("button", { name: "创作", exact: true }).click();
  await expect(page).toHaveURL(/\/creation$/);
  await expect(page.locator("#creation-hub-title")).toBeAttached();
  await expect(composer).toHaveCount(1);
  await expect(prompt).toHaveValue(sharedDraft);
  expect(await page.evaluate(() => (
    window.__sharedDirectorDeckProbe === document.querySelector('[data-ui="director-composer"]')
  ))).toBe(true);

  await studioNav.getByRole("button", { name: "首页", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator("#community-title")).toBeAttached();
  await expect(composer).toHaveCount(1);
  await expect(prompt).toHaveValue(sharedDraft);
  expect(await page.evaluate(() => (
    window.__sharedDirectorDeckProbe === document.querySelector('[data-ui="director-composer"]')
  ))).toBe(true);

  if (isPhone) {
    await page.getByRole("button", { name: "返回首页灵感" }).click();
    await expect(page.locator(".main-canvas")).toBeVisible();
  }
  await page.locator(".community-hero-copy").getByRole("button", { name: "开始创作" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(prompt).toBeFocused();

  if (isPhone) {
    await page.getByRole("button", { name: "返回首页灵感" }).click();
  }
  const inspiration = page.locator('.community-card-caption button:not([disabled])').first();
  await inspiration.click();
  await expect(page).toHaveURL(/\/$/);
  await expect(prompt).toBeFocused();
  await expect(prompt).not.toHaveValue(sharedDraft);
  const inspirationDraft = await prompt.inputValue();
  expect(inspirationDraft.length).toBeGreaterThan(0);

  await expect(page.locator(".composer-workbench-handoff")).toHaveCount(0);
  await expect(composer).toHaveCount(1);
  await expect(prompt).toHaveValue(inspirationDraft);
  expect(await page.evaluate(() => (
    window.__sharedDirectorDeckProbe === document.querySelector('[data-ui="director-composer"]')
  ))).toBe(true);
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});

test("advanced workbench boundary and browser-back restore the quick composer", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  const isPhone = (page.viewportSize()?.width || 0) <= 720;
  await useDemoPersona(page, "operator");
  await page.goto("/creation", { waitUntil: "commit" });
  await expect(page.locator('[data-ui="creation-invitation-entry"]')).toBeVisible({ timeout: 90_000 });
  await expect(page.locator('[data-ui="director-composer"]')).toHaveCount(1);

  const workbenches = [
    { id: "console", surface: "director-console" },
    { id: "notebook", surface: "director-notebook" },
    { id: "canvas", surface: "lineage-canvas" },
  ];

  for (const { id, surface } of workbenches) {
    await page.locator(`[data-workbench="${id}"]`).first().click();
    await expect(page).toHaveURL(new RegExp(`/creation\\?workbench=${id}$`));
    await expect(page.locator(".app-shell.is-advanced-workbench")).toBeVisible();
    await expect(page.locator('[data-ui="director-composer"]')).toHaveCount(0);
    await expect(page.locator("#mobile-composer-launcher")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "返回创作入口" })).toBeVisible();
    await expect(page.locator(".creation-record-browser")).toHaveCount(0);

    if (isPhone) {
      await expect(page.getByRole("navigation", { name: "工作台导航" })).toBeHidden();
      await expect(page.locator('[data-ui="creation-invitation-entry"]')).toHaveCount(0);
      const gate = page.locator('[data-ui="desktop-workbench-gate"]');
      await expect(gate).toBeVisible();
      await expect(gate).toHaveAttribute("data-workbench", id);
      await expect(page.locator(`[data-ui="${surface}"]`)).toHaveCount(0);
      await expect(page.locator('[data-ui="workbench-editor"]')).toHaveCount(0);
      await expect(page.locator('iframe[title="StoryAI 3D 导演台"]')).toHaveCount(0);
    } else {
      await expect(page.locator(`[data-ui="${surface}"]`)).toBeVisible();
      await expect(page.locator('[data-ui="desktop-workbench-gate"]')).toHaveCount(0);
    }
    await expectNoHorizontalPageOverflow(page);

    await page.goBack();
    await expect(page).toHaveURL(/\/creation$/);
    await expect(page.locator('[data-ui="creation-invitation-entry"]')).toBeVisible();
    await expect(page.locator('[data-ui="director-composer"]')).toHaveCount(1);
  }

  expect(failures).toEqual([]);
});

test("every customer Studio route composes without page-level overflow", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await useDemoPersona(page, "operator");

  for (const route of ["/", "/creation", "/media", "/artworks", "/publishing", "/history", "/settings"]) {
    await page.goto(route, { waitUntil: "commit" });
    await expect(page.locator(".app-shell")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "工作台导航" })).toBeAttached();
    await expectNoHorizontalPageOverflow(page);
  }

  expect(failures).toEqual([]);
});

test("public login keeps one clear, keyboard-reachable recovery action", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await page.route("**/api/v1/auth/session", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ authenticated: false, login_available: true }),
  }));
  await page.goto("/tests/browser-fixtures/auth-entry.html?logged_out=1", {
    waitUntil: "commit",
  });

  await expect(page.getByRole("heading", { name: "已安全退出" })).toBeVisible();
  await expect(page.getByText("密码与验证码由安全身份服务处理，旭天不会保存。")).toBeVisible();
  await expect(page.locator(".skin-switcher-trigger")).toHaveCount(0);
  const login = page.getByRole("button", { name: "重新登录" });
  await expectInsideViewport(login, page);
  await login.focus();
  await expect(login).toBeFocused();
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});

test("Company remains reachable with one truthful demo identity", async ({ page }) => {
  const failures = captureBrowserFailures(page);

  await useDemoPersona(page, "owner");
  await page.goto("/company", { waitUntil: "commit" });
  await expect(page.getByRole("navigation", { name: "公司管理导航" })).toBeAttached();
  await expect(page.locator('select[aria-label="切换演示账号"]')).toHaveCount(1);
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});

test("the Platform owner enters and leaves personal creation without changing login identity", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  const forbiddenAuthTransitions = captureForbiddenOwnerContextTransitions(page);

  await useDemoPersona(page, "platform_admin");
  await page.goto("/platform", { waitUntil: "commit" });
  await expect(page.getByRole("navigation", { name: "平台管理员模块" })).toBeAttached({ timeout: 90_000 });
  const persona = page.locator('select[aria-label="切换演示账号"]');
  await expect(persona).toHaveCount(1);
  await expectDemoPersona(page, "platform_admin");
  const operationsEntry = ownerCreationEntry(page);
  await expect(operationsEntry).toHaveCount(1);
  await expectInsideViewport(operationsEntry, page);
  await expectTouchTarget(operationsEntry, page);

  await page.getByRole("button", { name: "打开基础配置" }).click();
  await expect(page.getByRole("navigation", { name: "平台管理导航" })).toBeVisible();
  const personalCreationEntry = ownerCreationEntry(page);
  await expect(personalCreationEntry).toHaveCount(1);
  await expectInsideViewport(personalCreationEntry, page);
  await expectTouchTarget(personalCreationEntry, page);
  await personalCreationEntry.focus();
  await expect(personalCreationEntry).toBeFocused();
  await personalCreationEntry.press("Enter");
  await expect(page).toHaveURL(/\/personal\/creation$/);
  await expect(page.locator("#creation-hub-title")).toBeAttached({ timeout: 90_000 });
  await expectDemoPersona(page, "platform_admin");
  await expect(page.getByRole("navigation", { name: "平台管理导航" })).toHaveCount(0);
  await expect(page.getByRole("navigation", { name: "平台管理员模块" })).toHaveCount(0);
  const returnToPlatform = await revealReturnToPlatform(page);
  await expect(returnToPlatform).toHaveCount(1);
  await expectInsideViewport(returnToPlatform, page);
  await expectTouchTarget(returnToPlatform, page);
  await expectNoHorizontalPageOverflow(page);

  await page.reload({ waitUntil: "commit" });
  await expect(page).toHaveURL(/\/personal\/creation$/);
  await expect(page.locator("#creation-hub-title")).toBeAttached({ timeout: 90_000 });
  await expectDemoPersona(page, "platform_admin");
  await expect(ownerCreationEntry(page)).toHaveCount(0);

  const refreshedReturn = await revealReturnToPlatform(page);
  await expect(refreshedReturn).toHaveCount(1);
  await expectInsideViewport(refreshedReturn, page);
  await expectTouchTarget(refreshedReturn, page);
  await refreshedReturn.focus();
  await expect(refreshedReturn).toBeFocused();
  await refreshedReturn.press("Space");
  await expect(page).toHaveURL(/\/platform(?:\?.*)?$/);
  await expect(page.getByRole("navigation", { name: "平台管理员模块" })).toBeAttached({ timeout: 90_000 });
  await expect(persona).toHaveCount(1);
  await expectDemoPersona(page, "platform_admin");
  await expect(ownerCreationEntry(page)).toHaveCount(1);
  expect(forbiddenAuthTransitions).toEqual([]);
  expect(failures).toEqual([]);
});

test("Operations cockpit survives nullable trend values and a fresh reload", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await useDemoPersona(page, "platform_admin");

  const expectCockpit = async () => {
    await expect(
      page.locator('.ops-console[data-active-section="cockpit"]'),
    ).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("heading", { name: "平台经营总览" })).toBeVisible();

    const trendPanel = page
      .locator(".ops-cockpit-grid > .ops-panel")
      .filter({ has: page.getByRole("heading", { name: "经营趋势" }) });
    await trendPanel.getByText("查看图表数值", { exact: true }).click();

    const table = trendPanel.getByRole("table", { name: "平台经营趋势数值" });
    await expect(table).toBeVisible();
    await expect(
      table.getByRole("cell", { name: "待核验", exact: true }).first(),
    ).toBeVisible();
  };

  await page.goto("/platform?ops_module=cockpit&ops_range=24h", {
    waitUntil: "commit",
  });
  await expectCockpit();

  await page.reload({ waitUntil: "commit" });
  await expectCockpit();

  expect(failures).toEqual([]);
});

test("Operations does not derive the personal-creation entry from a mixed surface payload", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await page.goto(
    "/tests/browser-fixtures/operations-workspace-entry.html?surfaces=personal,platform",
    { waitUntil: "commit" },
  );

  await expect(page.getByRole("navigation", { name: "平台管理员模块" })).toBeAttached({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: "个人创作" })).toHaveCount(0);
  await expect(page.locator("#selected-surface")).toHaveText("");
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});

test("Operations keeps its account boundary isolated for every mixed payload", async ({ page }) => {
  const failures = captureBrowserFailures(page);

  await page.goto(
    "/tests/browser-fixtures/operations-workspace-entry.html?surfaces=platform",
    { waitUntil: "commit" },
  );
  await expect(page.getByRole("navigation", { name: "平台管理员模块" })).toBeAttached({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: "个人创作" })).toHaveCount(0);

  await page.goto(
    "/tests/browser-fixtures/operations-workspace-entry.html?surfaces=personal,studio,company,platform",
    { waitUntil: "commit" },
  );
  await expect(page.getByRole("navigation", { name: "平台管理员模块" })).toBeAttached({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: "个人创作" })).toHaveCount(0);
  await expect(page.locator("#selected-surface")).toHaveText("");
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});

test("the owner-only personal-creation action has one canonical target", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  const forbiddenAuthTransitions = captureForbiddenOwnerContextTransitions(page);
  await page.goto(
    "/tests/browser-fixtures/operations-workspace-entry.html?role=owner&surfaces=personal,studio,company,platform",
    { waitUntil: "commit" },
  );

  await expect(page.locator("#fixture-role")).toHaveText("owner");
  const entry = ownerCreationEntry(page);
  await expect(entry).toHaveCount(1);
  await expectInsideViewport(entry, page);
  await expectTouchTarget(entry, page);
  await entry.focus();
  await expect(entry).toBeFocused();
  await entry.press("Space");
  await expect(page).toHaveURL(/\/personal\/creation$/);
  await expect(page.locator("#selected-surface")).toHaveText("personal");
  await expect(page.locator("#context-switch-count")).toHaveText("1");
  await expect(page.getByRole("heading", { name: "本人个人创作空间" })).toBeVisible();
  await expect(page.getByText("当前登录主体保持不变：owner")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "平台管理员模块" })).toHaveCount(0);
  const returnToPlatform = page.getByRole("button", { name: "返回 Platform" });
  await expectInsideViewport(returnToPlatform, page);
  await expectTouchTarget(returnToPlatform, page);
  await returnToPlatform.focus();
  await returnToPlatform.press("Enter");
  await expect(page).toHaveURL(/\/platform$/);
  await expect(page.locator("#fixture-role")).toHaveText("owner");
  await expect(page.getByRole("navigation", { name: "平台管理员模块" })).toBeAttached();
  await expect(ownerCreationEntry(page)).toHaveCount(1);
  await expectNoHorizontalPageOverflow(page);
  expect(forbiddenAuthTransitions).toEqual([]);
  expect(failures).toEqual([]);
});

test("ordinary Platform admin, company, and personal principals never receive the owner context switch", async ({ page }) => {
  const failures = captureBrowserFailures(page);

  for (const role of ["platform_admin", "company", "personal"]) {
    await page.goto(
      `/tests/browser-fixtures/operations-workspace-entry.html?role=${role}`,
      { waitUntil: "commit" },
    );
    await expect(page.locator("#fixture-role")).toHaveText(role);
    await expect(ownerCreationEntry(page)).toHaveCount(0);
    await expect(page.locator("#context-switch-count")).toHaveText("0");
    await expect(page.locator("[data-product-context=\"platform\"]")).toBeVisible();
  }

  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});

test("company and personal product surfaces do not expose the Platform owner switch", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await page.goto(
    "/tests/browser-fixtures/operations-workspace-entry.html?role=company",
    { waitUntil: "commit" },
  );

  const cases = [
    { persona: "operator", route: "/", navigation: "工作台导航" },
    { persona: "operator", route: "/company", navigation: "公司管理导航" },
    { persona: "personal_creator", route: "/personal/creation", navigation: "工作台导航" },
  ];
  for (const scenario of cases) {
    await page.evaluate((persona) => {
      window.sessionStorage.setItem("ai-video.demo-persona", persona);
    }, scenario.persona);
    await page.goto(scenario.route, { waitUntil: "commit" });
    await expect(page.getByRole("navigation", { name: scenario.navigation }))
      .toBeAttached({ timeout: 90_000 });
    expect(new URL(page.url()).pathname).toBe(scenario.route);
    await expectDemoPersona(page, scenario.persona);
    await expect(ownerCreationEntry(page)).toHaveCount(0);
    await expectNoHorizontalPageOverflow(page);
  }

  expect(failures).toEqual([]);
});

test("the shared light-skin chooser is authored, persistent in state, and keyboard complete", async ({ page }) => {
  const failures = captureBrowserFailures(page);
  await page.goto(
    "/tests/browser-fixtures/operations-workspace-entry.html?surfaces=platform",
    { waitUntil: "commit" },
  );
  await expect(page.getByRole("navigation", { name: "平台管理员模块" }))
    .toBeAttached({ timeout: 90_000 });

  const trigger = page.getByRole("button", { name: "界面皮肤：纯白" });
  await expectInsideViewport(trigger, page);
  await expectTouchTarget(trigger, page);
  await expect(trigger.locator("select")).toHaveCount(0);

  await trigger.focus();
  await trigger.press("Enter");
  const menu = page.getByRole("menu", { name: "界面皮肤" });
  await expectInsideViewport(menu, page);
  const options = menu.getByRole("menuitemradio");
  await expect(options).toHaveCount(3);
  await expect(options.nth(0)).toHaveAttribute("aria-checked", "true");
  await expect(options.nth(0)).toBeFocused();

  await page.keyboard.press("ArrowDown");
  await expect(options.nth(1)).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#selected-skin")).toHaveText("mist");
  await expect(page.locator(".ops-console")).toHaveAttribute("data-theme", "mist");
  const mistTrigger = page.getByRole("button", { name: "界面皮肤：雾灰" });
  await expect(mistTrigger).toBeFocused();

  await mistTrigger.press("ArrowDown");
  await expect(page.getByRole("menu", { name: "界面皮肤" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu", { name: "界面皮肤" })).toHaveCount(0);
  await expect(mistTrigger).toBeFocused();
  await expectNoHorizontalPageOverflow(page);
  expect(failures).toEqual([]);
});
