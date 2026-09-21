import { defineConfig } from "@playwright/test";
import arkConfig from "./ark-video.playwright.config.mjs";

// Production App logic with intercepted, synthetic Platform responses only.
const port = Number(process.env.MINIMAX_H3_PLAYWRIGHT_PORT || 4191);
const baseURL = `http://127.0.0.1:${port}`;
export default defineConfig({
  ...arkConfig,
  testMatch: "minimax-h3-creation.spec.mjs",
  metadata: { ...arkConfig.metadata, minimaxH3Contract: true },
  use: { ...arkConfig.use, baseURL },
  webServer: {
    ...arkConfig.webServer,
    command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: baseURL,
    env: { ...arkConfig.webServer.env, VITE_PLATFORM_API_URL: baseURL },
  },
});
