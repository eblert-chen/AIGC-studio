import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync, writeFileSync } from "node:fs";

const storyAiLicenseNotice = readFileSync(new URL("./LICENSE", import.meta.url), "utf8");

export default defineConfig({
  base: "/director-desk/",
  // The upstream public folder contains a separately licensed Sketchfab mesh.
  // This embedded build intentionally uses the procedural mannequin instead.
  publicDir: false,
  assetsInclude: ["**/*.fbx", "**/*.obj"],
  plugins: [
    react(),
    {
      name: "storyai-third-party-license-notice",
      apply: "build",
      generateBundle() {
        this.emitFile({
          type: "asset",
          fileName: "THIRD_PARTY_LICENSES.txt",
          source: storyAiLicenseNotice,
        });
      },
    },
    {
      name: "storyai-opaque-sandbox-classic-entry",
      apply: "build",
      writeBundle() {
        const entryPath = new URL("../../public/director-desk/index.html", import.meta.url);
        const html = readFileSync(entryPath, "utf8")
          .replace(/<script type="module" crossorigin src="([^"]+)"><\/script>/, '<script defer src="$1"></script>')
          .replace(/<link rel="stylesheet" crossorigin /, '<link rel="stylesheet" ');
        writeFileSync(entryPath, html, "utf8");
      },
    },
  ],
  build: {
    outDir: "../../public/director-desk",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        format: "iife",
        name: "StoryAiDirectorDesk",
        inlineDynamicImports: true,
        entryFileNames: "assets/director-desk.js",
      },
    },
  },
  server: {
    fs: {
      allow: [
        decodeURIComponent(new URL(".", import.meta.url).pathname),
      ],
    },
  },
});
