import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: {
    alias: [
      {
        find: /^@phosphor-icons\/react$/,
        replacement: fileURLToPath(new URL("./src/phosphorIcons.js", import.meta.url)),
      },
    ],
  },
  build: {
    outDir: "dist/client",
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("recharts") || id.includes("victory-vendor") || id.includes("d3-") || id.includes("decimal.js-light")) return "charts-vendor";
          if (id.includes("@phosphor-icons")) return "icons-vendor";
          if (id.includes("react") || id.includes("scheduler")) return "react-vendor";
          return undefined;
        },
      },
    },
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: ["terminal.local"],
    watch: {
      ignored: [
        "**/backend/**",
        "**/.pytest-*/**",
        "**/artifacts/pytest-tmp/**",
        "**/artifacts/**/pytest-tmp/**",
      ],
    },
    warmup: {
      // The design-system entry is intentionally a large ordered import graph.
      // Warm it explicitly so the dev server cannot report ready while the first
      // browser is still waiting for its initial CSS transform.
      clientFiles: ["./src/main.jsx", "./src/design-system/index.css"],
    },
  },
  plugins: [react()],
});
