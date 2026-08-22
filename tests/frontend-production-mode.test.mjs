import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { build as viteBuild } from "vite";

import {
  isExplicitDevelopmentDemo,
  readBuildPlatformConfig,
  readRuntimeAuthNavigation,
} from "../src/runtimeMode.js";

test("demo requires an explicit flag in a development build", () => {
  assert.equal(isExplicitDevelopmentDemo({ MODE: "development", DEV: true }), false);
  assert.equal(isExplicitDevelopmentDemo({
    MODE: "development",
    DEV: true,
    VITE_ENABLE_DEMO: "true",
  }), true);
});

test("production always fails closed even if a demo flag is accidentally supplied", () => {
  assert.equal(isExplicitDevelopmentDemo({
    MODE: "production",
    PROD: true,
    DEV: false,
    VITE_ENABLE_DEMO: "true",
  }), false);
  assert.equal(isExplicitDevelopmentDemo({
    MODE: "production",
    PROD: true,
    DEV: true,
    VITE_ENABLE_DEMO: "true",
  }), false);
});

test("only public platform routing hints are read from Vite build configuration", () => {
  assert.deepEqual(readBuildPlatformConfig({
    VITE_PLATFORM_API_URL: " https://platform.example/ ",
    VITE_COMPANY_ID: " company-1 ",
    VITE_ACCESS_TOKEN: "must-not-be-read",
  }), {
    platformApiUrl: "https://platform.example/",
  });
});

test("formal login and logout navigation comes only from controlled runtime config", () => {
  assert.deepEqual(readRuntimeAuthNavigation({
    location: { origin: "https://app.example" },
    __AI_VIDEO_RUNTIME_CONFIG__: {
      loginUrl: "https://id.example/login",
      logoutUrl: "/session/logout",
    },
  }), {
    loginUrl: "https://id.example/login",
    logoutUrl: "https://app.example/session/logout",
  });
  assert.deepEqual(readRuntimeAuthNavigation({
    location: { origin: "https://app.example" },
    __AI_VIDEO_RUNTIME_CONFIG__: {
      loginUrl: "javascript:alert(1)",
      logoutUrl: "http://insecure.example/logout",
    },
  }), { loginUrl: "", logoutUrl: "" });
});

test("application session mode is restored by the cookie-backed auth gateway", async () => {
  const [source, main, gateway] = await Promise.all([
    readFile(new URL("../src/App.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/main.jsx", import.meta.url), "utf8"),
    readFile(new URL("../src/auth/AuthGateway.jsx", import.meta.url), "utf8"),
  ]);
  assert.match(source, /const DEVELOPMENT_DEMO_ENABLED = import\.meta\.env\.PROD\s*\? false\s*:\s*isExplicitDevelopmentDemo\(import\.meta\.env\)/);
  assert.match(source, /const DEMO_MODE = DEVELOPMENT_DEMO_ENABLED/);
  assert.match(source, /const LIVE_MODE = !DEMO_MODE && API_CONFIGURED/);
  assert.doesNotMatch(source, /const DEMO_MODE = !API_CONFIGURED/);
  assert.doesNotMatch(source, /AUTH_REQUIRED|HAS_AUTHENTICATED_SESSION/);
  assert.match(source, /DEMO_MODE \? DEMO_MODELS\[0\]\.id : initialPendingCreate\?\.modelId \?\? ""/);
  assert.doesNotMatch(source, /LIVE_MODE \?[^\n]+: DEMO_MODELS\[0\]\.id/);
  assert.match(source, /页面不会使用演示数据代替生产数据/);
  assert.match(main, /<AuthGateway[\s\S]*?<App\s*\/>[\s\S]*?<\/AuthGateway>/);
  assert.match(gateway, /client\.getSession/);
  assert.match(gateway, /status === "loading"/);
  assert.match(gateway, /<LoginPage/);
  assert.doesNotMatch(source, /ai-video\.access-token/);
});

test("administrator demo dataset is loaded only from the explicit demo branch", async () => {
  const consoleSource = await readFile(new URL("../src/admin/OperationsConsole.jsx", import.meta.url), "utf8");
  const containerSource = await readFile(new URL("../src/admin/AdminOperationsContainer.jsx", import.meta.url), "utf8");
  assert.doesNotMatch(consoleSource, /from ["']\.\/adminDemoData\.js["']/);
  assert.match(containerSource, /if \(demoMode\) \{[\s\S]*?import\(["']\.\/adminDemoData\.js["']\)/);
});

test("live cancellation is server-authoritative and limited to queued work", async () => {
  const source = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
  assert.match(source, /await companyClient\.cancelTask\(currentTask\.id\)/);
  assert.match(source, /if \(!canCancelTasks\)/);
  assert.match(source, /currentTask\?\.status !== "queued"/);
  assert.match(source, /仅可取消尚未外发的排队任务/);
  assert.doesNotMatch(source, /客户平台尚未提供取消接口/);
});

test("production bundles replace mock publisher creation with a fail-closed client method", async () => {
  const clientPath = fileURLToPath(new URL("../src/api/platformClient.js", import.meta.url)).replaceAll("\\", "/");
  const result = await viteBuild({
    configFile: false,
    envFile: false,
    mode: "production",
    logLevel: "silent",
    plugins: [{
      name: "production-publisher-guard-probe",
      resolveId(id) {
        return id === "virtual:publisher-guard" ? `\0${id}` : null;
      },
      load(id) {
        if (id !== "\0virtual:publisher-guard") return null;
        return `
          import { createPlatformClient } from ${JSON.stringify(clientPath)};
          const client = createPlatformClient({
            baseUrl: "https://platform.invalid",
            companyId: "company-guard",
            accessToken: "session-token",
            fetcher: globalThis.fetch,
          });
          globalThis.__publisherConnectionGuard = client.createPublisherConnection;
        `;
      },
    }],
    build: {
      write: false,
      minify: "esbuild",
      rollupOptions: { input: "virtual:publisher-guard" },
    },
  });
  const outputs = Array.isArray(result) ? result : [result];
  const code = outputs
    .flatMap((output) => output.output || [])
    .filter((item) => item.type === "chunk")
    .map((item) => item.code)
    .join("\n");

  assert.match(code, /PRODUCTION_MOCK_PUBLISHER_FORBIDDEN/);
  await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
  assert.throws(
    () => globalThis.__publisherConnectionGuard({ provider: "mock", displayName: "should-fail" }),
    (error) => error?.code === "PRODUCTION_MOCK_PUBLISHER_FORBIDDEN",
  );
  delete globalThis.__publisherConnectionGuard;
});
