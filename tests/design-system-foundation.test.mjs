import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { operationsSource } from "./operations-source.mjs";

const controls = await readFile(
  new URL("../src/design-system/controls.css", import.meta.url),
  "utf8",
);
const foundation = await readFile(
  new URL("../src/design-system/foundation.css", import.meta.url),
  "utf8",
);
const tokens = await readFile(
  new URL("../src/design-system/tokens.css", import.meta.url),
  "utf8",
);
const legacyStudio = await readFile(
  new URL("../src/light-theme.css", import.meta.url),
  "utf8",
);
const legacyCommunity = await readFile(
  new URL("../src/community.css", import.meta.url),
  "utf8",
);
const legacyCreation = await readFile(
  new URL("../src/creation-hub.css", import.meta.url),
  "utf8",
);
const legacyPublishing = await readFile(
  new URL("../src/publishing.css", import.meta.url),
  "utf8",
);
const legacyCompany = await readFile(
  new URL("../src/styles.css", import.meta.url),
  "utf8",
);
const legacyOperations = await readFile(
  new URL("../src/admin/operations-console.css", import.meta.url),
  "utf8",
);
const operationsRoutes = await readFile(
  new URL("../src/design-system/operations-routes.css", import.meta.url),
  "utf8",
);
const designSystemIndex = await readFile(
  new URL("../src/design-system/index.css", import.meta.url),
  "utf8",
);
const authStyles = await readFile(
  new URL("../src/design-system/auth.css", import.meta.url),
  "utf8",
);
const studioRoutes = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);
const mainSource = await readFile(
  new URL("../src/main.jsx", import.meta.url),
  "utf8",
);
const indexHtml = await readFile(
  new URL("../index.html", import.meta.url),
  "utf8",
);

test("one local bilingual font stack owns Studio Company and Operations", () => {
  assert.match(foundation, /@fontsource-variable\/manrope\/wght\.css/);
  assert.match(foundation, /@fontsource-variable\/noto-sans-sc\/wght\.css/);
  assert.match(tokens, /--font-sans:[\s\S]*?"Manrope Variable"[\s\S]*?"Noto Sans SC Variable"/);
  assert.match(foundation, /html,[\s\S]*?\.app-shell,[\s\S]*?\.control-shell,[\s\S]*?\.ops-console[\s\S]*?font-family:\s*var\(--font-sans\)/);
  assert.doesNotMatch(legacyStudio, /font-family:\s*"Helvetica Neue"/);
  assert.doesNotMatch(legacyOperations, /font-family:[^;]*(Microsoft YaHei|PingFang SC)/);
  assert.doesNotMatch(legacyCompany.slice(0, 500), /font-family:/);
  assert.doesNotMatch(legacyCompany, /font-family:\s*ui-monospace/);
  assert.match(legacyCompany, /font-family:\s*var\(--font-mono\)/);
});

test("browser chrome follows the locked light foundation", () => {
  assert.match(indexHtml, /name="theme-color" content="#ffffff"/);
});

test("one layered stylesheet entry owns the complete application cascade", () => {
  assert.match(mainSource, /import\s+["']\.\/design-system\/index\.css["']/);
  assert.equal(
    [...mainSource.matchAll(/import\s+["'][^"']+\.css["']/g)].length,
    1,
  );
  assert.match(
    designSystemIndex,
    /@layer\s+system\.tokens,\s*system\.foundation,\s*system\.controls,\s*system\.shells,\s*system\.routes\s*;/,
  );
  assert.doesNotMatch(designSystemIndex, /layer\((?:legacy\.|theme\b)/);
  assert.doesNotMatch(designSystemIndex, /@import\s+"\.\.\//);
  assert.match(designSystemIndex, /@import\s+"\.\/shells\.css"\s+layer\(system\.shells\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/chrome\.css"\s+layer\(system\.shells\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/studio-routes\.css"\s+layer\(system\.routes\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/management-routes\.css"\s+layer\(system\.routes\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/operations-routes\.css"\s+layer\(system\.routes\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/composer\.css"\s+layer\(system\.routes\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/home\.css"\s+layer\(system\.routes\)/);
  assert.doesNotMatch(operationsSource, /import\s+["']\.\/operations-console\.css["']/);
  assert.doesNotMatch(foundation, /@import\s+["']\.\/tokens\.css["']/);
});

test("legacy CSS is migrated to tokens instead of hidden behind a selector patch", () => {
  const legacyCss = [
    legacyStudio,
    legacyCompany,
    legacyCommunity,
    legacyCreation,
    legacyPublishing,
    legacyOperations,
  ].join("\n");
  assert.doesNotMatch(legacyCss, /font-size:\s*(?:8|9|10|11)px\s*;/);
  assert.doesNotMatch(legacyCss, /font-weight:\s*(?:520|540|550|580|610|620|630|640|650|660|680|710|720|730|740|750|760|780|800)\s*;/);
  assert.doesNotMatch(foundation, /Migration guard:/);
  assert.doesNotMatch(foundation, /font-size:[^;]+!important/);
  assert.doesNotMatch(operationsSource, /fontSize:\s*(?:8|9|10|11)\b/);
  assert.match(legacyOperations, /Retired: Operations styles now live in design-system\/operations-routes\.css/);
  assert.match(operationsRoutes, /\.ops-timing-strip small\s*\{[\s\S]*?font-size:\s*var\(--text-caption, 12px\)/);
  assert.match(operationsRoutes, /\.ops-reason-row small\s*\{[\s\S]*?font-size:\s*var\(--text-caption, 12px\)/);
  assert.match(operationsRoutes, /\.ops-exception-meta small\s*\{[\s\S]*?font-size:\s*var\(--text-caption, 12px\)/);
  assert.match(operationsRoutes, /\.ops-table th\s*\{[\s\S]*?font-size:\s*var\(--text-body-sm, 13px\)/);
  assert.doesNotMatch(operationsRoutes, /!important/);
});

test("completed auth and settings route slices no longer depend on legacy selectors", () => {
  assert.match(authStyles, /\.auth-gate\s*\{[\s\S]*?background:\s*var\(--bg\)/);
  assert.match(studioRoutes, /\.app-shell\.is-secondary-page \.setting-row\s*\{[\s\S]*?display:\s*flex/);
  assert.doesNotMatch(legacyCompany, /\.auth-gate|\.settings-list|\.setting-row/);
  assert.doesNotMatch(legacyCommunity, /\.auth-gate|\.settings-list|\.setting-row/);
  assert.doesNotMatch(legacyStudio, /\.auth-gate|\.settings-list|\.setting-row/);
});

test("shared controls stay scoped to the three product shells", () => {
  assert.match(controls, /:is\(\.app-shell, \.control-shell, \.ops-console\)/);
  assert.doesNotMatch(controls, /(^|\n)\s*(button|input|select|textarea|table)\s*\{/m);
  assert.doesNotMatch(controls, /(^|\n)\s*:root\s*\{/m);
});

test("Ops maps its existing semantic palette instead of inheriting Studio colors", () => {
  assert.match(controls, /\.ops-console\s*\{[\s\S]*?--control-border-color:\s*var\(--ops-line-strong/);
  assert.match(controls, /\.ops-console\s*\{[\s\S]*?--control-accent:\s*var\(--ops-accent/);
  assert.match(controls, /\.ops-console\s*\{[\s\S]*?--control-danger:\s*var\(--ops-red/);
});

test("control contract exposes only the 32 40 44 scale and six pixel corners", () => {
  assert.match(controls, /--control-size-compact:\s*var\(--control-sm, 32px\)/);
  assert.match(controls, /--control-size-default:\s*var\(--control-md, 40px\)/);
  assert.match(controls, /--control-size-touch:\s*var\(--control-lg, 44px\)/);
  assert.match(controls, /--control-corner:\s*var\(--radius-control, 6px\)/);
  assert.match(controls, /\.control-shell \.control-brand\s*\{[\s\S]*?min-height:\s*var\(--control-size-compact\)/);
  assert.match(controls, /\.control-topbar button,[\s\S]*?min-height:\s*var\(--control-size-compact\)/);
  assert.match(legacyCompany, /\.control-logout\s*\{[^}]*height:\s*32px/s);
  assert.match(legacyCompany, /\.control-platform-view-switch\s*\{[^}]*height:\s*32px/s);
  assert.match(controls, /\.generate-button,[\s\S]*?\.publication-center form > footer button\.is-primary[\s\S]*?min-height:\s*var\(--control-size-touch\)/);
});

test("visible labels and tables cannot fall below the type floor", () => {
  assert.match(controls, /--control-label-size:\s*var\(--text-caption, 0\.75rem\)/);
  assert.match(controls, /--control-table-size:\s*var\(--text-body-sm, 0\.8125rem\)/);
  assert.match(controls, /:is\(\.control-table, \.ops-table\)[\s\S]*?font-size:\s*var\(--control-table-size\)/);
  assert.match(controls, /:is\(\.control-table, \.ops-table\) td[\s\S]*?font-size:\s*var\(--control-table-size\)/);
  assert.match(controls, /:is\(\.control-table, \.ops-table\) th[\s\S]*?font-size:\s*var\(--control-table-size\)/);
  assert.match(controls, /\.skin-switcher select\s*\{[\s\S]*?min-height:\s*var\(--control-size-compact\)/);
});

test("buttons fields tabs and tables share explicit state contracts", () => {
  assert.match(controls, /:hover:not\(:disabled\):not\(\[readonly\]\)/);
  assert.match(controls, /\[aria-invalid="true"\]/);
  assert.match(controls, /:disabled[\s\S]*?cursor:\s*not-allowed/);
  assert.match(controls, /\[aria-selected="true"\]/);
  assert.match(controls, /:focus-visible[\s\S]*?outline:\s*2px solid var\(--control-focus-ring\)/);
  assert.match(controls, /prefers-reduced-motion:\s*reduce/);
});

test("semantic states and icon-only controls remain deliberate exceptions", () => {
  assert.match(controls, /Icon-only controls keep a square hit target/);
  assert.match(controls, /\.icon-button,[\s\S]*?\.icon-only,[\s\S]*?\[data-icon-only="true"\][\s\S]*?width:\s*var\(--control-size-default\)/);
  assert.doesNotMatch(controls, /button\[aria-label\]:not\(\[class\]\)/);
  assert.match(controls, /Compact icon groups preserve dense table\/tool layouts[\s\S]*?\.ops-icon-button[\s\S]*?width:\s*var\(--control-size-compact\)/);
  assert.match(controls, /Large media\/card buttons are button-like surfaces[\s\S]*?\.creation-task-preview\[aria-label\][\s\S]*?width:\s*100%/);
  assert.match(controls, /Pills are reserved for actual state/);
  assert.match(controls, /\.ops-status-pill,[\s\S]*?border-radius:\s*var\(--radius-pill, 999px\)/);
  assert.match(controls, /button\.is-danger[\s\S]*?var\(--control-danger\)/);
});

test("composer settings remains a labeled control instead of collapsing to an icon square", () => {
  const iconOnlyStart = controls.indexOf("/* Icon-only controls keep a square hit target");
  const compactIconStart = controls.indexOf("/* Compact icon groups preserve dense table/tool layouts", iconOnlyStart);
  assert.ok(iconOnlyStart >= 0 && compactIconStart > iconOnlyStart);
  assert.doesNotMatch(
    controls.slice(iconOnlyStart, compactIconStart),
    /\.composer-settings-button/,
  );
  assert.match(
    controls,
    /\.composer-settings-button\s*\{[\s\S]*?flex:\s*0 0 auto;[\s\S]*?width:\s*auto;[\s\S]*?min-width:\s*max-content;[\s\S]*?white-space:\s*nowrap;/,
  );
  assert.match(
    controls,
    /@media \(max-width: 620px\)[\s\S]*?\.community-composer-header \.composer-current-mode\s*\{[\s\S]*?display:\s*none;/,
  );
});

test("collapsed composer reserves space for the shared 40px control contract", () => {
  assert.match(
    legacyCommunity,
    /\.community-composer:not\(\.is-expanded\) \.inspector-scroll\s*\{[\s\S]*?grid-template-rows:\s*minmax\(104px, auto\) auto;/,
  );
  assert.match(
    legacyCommunity,
    /\.community-composer:not\(\.is-expanded\)\s*\{[\s\S]*?grid-template-rows:\s*42px auto auto;[\s\S]*?height:\s*auto;/,
  );
  assert.match(
    legacyCommunity,
    /\.community-composer:not\(\.is-expanded\) \.model-section\s*\{[\s\S]*?padding:\s*4px 12px;/,
  );
  assert.match(
    legacyCommunity,
    /\.community-composer:not\(\.is-expanded\) \.model-section select\s*\{[\s\S]*?height:\s*var\(--control-md, 40px\);/,
  );
  assert.doesNotMatch(
    legacyCommunity,
    /\.is-community-home \.community-composer\s*\{[^}]*height:\s*(?:212|218|224|252|254)px;/s,
  );
});

test("compact table and inline actions do not inherit default or primary height", () => {
  const compactStart = controls.indexOf(".control-table td.is-actions button");
  const touchStart = controls.indexOf(".generate-button", compactStart);
  assert.ok(compactStart > 0 && touchStart > compactStart);
  const compactRules = controls.slice(compactStart, touchStart);
  assert.match(compactRules, /\.ops-table-link[\s\S]*?min-height:\s*var\(--control-size-compact\)/);
  assert.match(compactRules, /font-size:\s*var\(--control-label-size\)/);
  assert.match(compactRules, /\.publication-center \.publication-pagination button/);
  assert.doesNotMatch(compactRules, /min-height:\s*var\(--control-size-touch\)/);
});
