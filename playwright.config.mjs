import { defineConfig } from "@playwright/test";

const port = Number(process.env.PLAYWRIGHT_PORT || 4178);
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  expect: { timeout: 45_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL,
    browserName: "chromium",
    colorScheme: "light",
    locale: "zh-CN",
    // The full browser gate includes the real 3D Director Desk. Headless
    // Chromium disables WebGL unless software rendering is explicitly
    // enabled, which previously made the all-tests command exercise the
    // product's unsupported-device fallback while the dedicated 3D command
    // exercised the editor. Keep both gates on the same rendering contract.
    launchOptions: {
      args: ["--enable-webgl", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader", "--use-angle=swiftshader"],
    },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-1440",
      use: { viewport: { width: 1440, height: 900 } },
    },
    {
      name: "phone-390",
      use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true },
    },
    {
      name: "phone-320",
      use: { viewport: { width: 320, height: 640 }, isMobile: true, hasTouch: true },
    },
  ],
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      ...process.env,
      VITE_ENABLE_DEMO: "true",
    },
  },
});
