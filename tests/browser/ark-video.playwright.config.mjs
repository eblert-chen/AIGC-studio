import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import sharedConfig from "../../playwright.config.mjs";

// The standard suite intentionally runs demo mode. This bounded suite runs
// the unchanged production App logic against intercepted Platform responses.
const port = Number(process.env.ARK_VIDEO_PLAYWRIGHT_PORT || 4189);
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  ...sharedConfig,
  testDir: fileURLToPath(new URL(".", import.meta.url)),
  testMatch: "ark-video-creation.spec.mjs",
  metadata: { ...sharedConfig.metadata, arkVideoLive: true },
  use: { ...sharedConfig.use, baseURL },
  webServer: {
    ...sharedConfig.webServer,
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    cwd: fileURLToPath(new URL("../../", import.meta.url)),
    reuseExistingServer: false,
    env: {
      ...sharedConfig.webServer.env,
      VITE_ENABLE_DEMO: "false",
      VITE_PLATFORM_API_URL: baseURL,
    },
  },
});
