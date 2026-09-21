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
const shells = await readFile(
  new URL("../src/design-system/shells.css", import.meta.url),
  "utf8",
);
const chrome = await readFile(
  new URL("../src/design-system/chrome.css", import.meta.url),
  "utf8",
);
const managementRoutes = await readFile(
  new URL("../src/design-system/management-routes.css", import.meta.url),
  "utf8",
);
const composerRoutes = await readFile(
  new URL("../src/design-system/composer.css", import.meta.url),
  "utf8",
);
const homeRoutes = await readFile(
  new URL("../src/design-system/home.css", import.meta.url),
  "utf8",
);
const mobileStudio = await readFile(
  new URL("../src/design-system/mobile-studio.css", import.meta.url),
  "utf8",
);
const mobileManagement = await readFile(
  new URL("../src/design-system/mobile-management.css", import.meta.url),
  "utf8",
);
const mobileOperations = await readFile(
  new URL("../src/design-system/mobile-operations.css", import.meta.url),
  "utf8",
);
const showcaseRoute = await readFile(
  new URL("../src/design-system/showcase-route.css", import.meta.url),
  "utf8",
);
const branding = await readFile(
  new URL("../src/design-system/branding.css", import.meta.url),
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

const authoredCss = [
  controls,
  foundation,
  shells,
  chrome,
  authStyles,
  studioRoutes,
  managementRoutes,
  operationsRoutes,
  composerRoutes,
  homeRoutes,
  mobileStudio,
  mobileManagement,
  mobileOperations,
  showcaseRoute,
  branding,
].join("\n");

function hexToken(source, name) {
  const match = source.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  assert.ok(match, `missing ${name}`);
  return match[1];
}

function relativeLuminance(hex) {
  const channels = hex.slice(1).match(/../g).map((part) => Number.parseInt(part, 16) / 255);
  const [red, green, blue] = channels.map((value) => (
    value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
  ));
  return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue);
}

function contrastRatio(foreground, background) {
  const first = relativeLuminance(foreground);
  const second = relativeLuminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

test("one Chinese-first local bilingual font stack owns Studio Company and Operations", () => {
  assert.match(foundation, /@fontsource-variable\/manrope\/wght\.css/);
  assert.match(foundation, /@fontsource-variable\/noto-sans-sc\/wght\.css/);
  assert.match(tokens, /--font-sans:[\s\S]*?"Noto Sans SC Variable"[\s\S]*?"Manrope Variable"/);
  assert.match(foundation, /html,[\s\S]*?\.app-shell,[\s\S]*?\.control-shell,[\s\S]*?\.ops-console[\s\S]*?font-family:\s*var\(--font-sans\)/);
  assert.doesNotMatch(authoredCss, /font-family:[^;]*(?:"Helvetica Neue"|Microsoft YaHei|PingFang SC)/);
  assert.doesNotMatch(authoredCss, /font-family:\s*ui-monospace/);
  assert.match(managementRoutes, /\.control-shell \.control-table :is\(code, \.is-mono\)\s*\{[^}]*font-family:\s*var\(--font-mono\)/s);
});

test("browser chrome follows the locked light foundation", () => {
  assert.match(indexHtml, /name="theme-color" content="#fafaf7"/);
});

test("shared muted copy and workflow actions meet WCAG AA", () => {
  const paperStart = tokens.indexOf(':where([data-theme="paper"]');
  const mistStart = tokens.indexOf(':where([data-theme="mist"]');
  const warmStart = tokens.indexOf(':where([data-theme="warm"]');
  const paper = tokens.slice(paperStart, mistStart);
  const mist = tokens.slice(mistStart, warmStart);
  const warm = tokens.slice(warmStart, tokens.indexOf("/* Operations keeps domain aliases", warmStart));

  for (const theme of [paper, mist, warm]) {
    assert.ok(
      contrastRatio(hexToken(theme, "--text-muted"), hexToken(theme, "--bg")) >= 4.5,
      "12px muted copy must retain AA contrast on its theme canvas",
    );
  }

  const signal = hexToken(tokens, "--workflow-orange");
  assert.ok(contrastRatio(signal, "#ffffff") >= 4.5, "signal text and buttons must contrast with white");
  assert.ok(
    contrastRatio(signal, hexToken(tokens, "--workflow-orange-soft")) >= 4.5,
    "signal text must contrast with its soft state surface",
  );
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
  assert.match(designSystemIndex, /@import\s+"\.\/mobile-studio\.css"\s+layer\(system\.routes\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/mobile-management\.css"\s+layer\(system\.routes\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/mobile-operations\.css"\s+layer\(system\.routes\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/showcase-route\.css"\s+layer\(system\.routes\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/branding\.css"\s+layer\(system\.routes\)/);
  assert.match(designSystemIndex, /@import\s+"\.\/auth\.css"\s+layer\(system\.routes\)/);
  assert.doesNotMatch(designSystemIndex, /(?:styles|light-theme|community|creation-hub|publishing|operations-console)\.css/);
  assert.doesNotMatch(operationsSource, /import\s+["']\.\/operations-console\.css["']/);
  assert.doesNotMatch(foundation, /@import\s+["']\.\/tokens\.css["']/);
});

test("active layered CSS preserves the readable type floor without specificity patches", () => {
  assert.doesNotMatch(authoredCss, /font-size:\s*(?:8|9|10|11)px\s*;/);
  assert.doesNotMatch(foundation, /Migration guard:/);
  assert.doesNotMatch(foundation, /font-size:[^;]+!important/);
  assert.doesNotMatch(operationsSource, /fontSize:\s*(?:8|9|10|11)\b/);
  assert.match(operationsRoutes, /\.ops-timing-strip small\s*\{[\s\S]*?font-size:\s*var\(--text-caption, 12px\)/);
  assert.match(operationsRoutes, /\.ops-reason-row small\s*\{[\s\S]*?font-size:\s*var\(--text-caption, 12px\)/);
  assert.match(operationsRoutes, /\.ops-exception-meta small\s*\{[\s\S]*?font-size:\s*var\(--text-caption, 12px\)/);
  assert.match(operationsRoutes, /\.ops-table th\s*\{[\s\S]*?font-size:\s*var\(--text-body-sm, 13px\)/);
  assert.doesNotMatch(operationsRoutes, /!important/);
});

test("auth and settings route slices keep one explicit visual owner", () => {
  assert.match(authStyles, /\.auth-shell,[\s\S]*?\.auth-gate\s*\{[^}]*background:\s*var\(--auth-paper\)/s);
  assert.match(authStyles, /\.auth-gate\s*\{[^}]*display:\s*grid;[^}]*place-items:\s*center;/s);
  assert.match(studioRoutes, /\.setting-row\s*\{[^}]*display:\s*flex/s);
  assert.doesNotMatch(studioRoutes, /(^|\n)\s*\.auth-gate\b/m);
  assert.doesNotMatch(`${homeRoutes}\n${composerRoutes}\n${managementRoutes}\n${operationsRoutes}`, /(^|\n)\s*\.(?:auth-gate|settings-list|setting-row)\b/m);
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

test("control contract exposes the 32 40 44 scale and purposeful control corners", () => {
  assert.match(controls, /--control-size-compact:\s*var\(--control-sm, 32px\)/);
  assert.match(controls, /--control-size-default:\s*var\(--control-md, 40px\)/);
  assert.match(controls, /--control-size-touch:\s*var\(--control-lg, 44px\)/);
  assert.match(tokens, /--radius-control:\s*0\.625rem;/);
  assert.match(controls, /--control-corner:\s*var\(--radius-control, 10px\);/);
  assert.doesNotMatch(controls, /--control-corner:\s*0;/);
  assert.match(controls, /\.control-shell \.control-brand\s*\{[\s\S]*?min-height:\s*var\(--control-size-compact\)/);
  assert.match(controls, /\.control-topbar button,[\s\S]*?min-height:\s*var\(--control-size-compact\)/);
  assert.match(mobileManagement, /\.control-shell \.control-mobile-command-panel \.surface-switch button\s*\{[^}]*min-height:\s*44px/s);
  assert.match(mobileManagement, /\.control-shell \.control-mobile-command-panel \.skin-switcher-trigger\s*\{[^}]*min-height:\s*44px/s);
  assert.match(controls, /\.generate-button,[\s\S]*?\.publication-center form > footer button\.is-primary[\s\S]*?min-height:\s*var\(--control-size-touch\)/);
});

test("visible labels and tables cannot fall below the type floor", () => {
  assert.match(controls, /--control-label-size:\s*var\(--text-caption, 0\.75rem\)/);
  assert.match(controls, /--control-table-size:\s*var\(--text-body-sm, 0\.8125rem\)/);
  assert.match(controls, /:is\(\.control-table, \.ops-table\)[\s\S]*?font-size:\s*var\(--control-table-size\)/);
  assert.match(controls, /:is\(\.control-table, \.ops-table\) td[\s\S]*?font-size:\s*var\(--control-table-size\)/);
  assert.match(controls, /:is\(\.control-table, \.ops-table\) th[\s\S]*?font-size:\s*var\(--control-table-size\)/);
  assert.match(controls, /\.skin-switcher-trigger\s*\{[\s\S]*?min-height:\s*var\(--control-size-default/);
  assert.match(controls, /\.skin-switcher-trigger\s*\{[\s\S]*?font-size:\s*var\(--control-label-size/);
  assert.doesNotMatch(controls, /\.skin-switcher[^\n{]*select/);
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

test("composer disclosures remain labeled touch controls instead of icon-only settings buttons", () => {
  const iconOnlyStart = controls.indexOf("/* Icon-only controls keep a square hit target");
  const compactIconStart = controls.indexOf("/* Compact icon groups preserve dense table/tool layouts", iconOnlyStart);
  assert.ok(iconOnlyStart >= 0 && compactIconStart > iconOnlyStart);
  assert.doesNotMatch(controls, /\.composer-settings-button|\.composer-add-media/);
  assert.match(composerRoutes, /\.director-panel-triggers button\s*\{[^}]*min-height:\s*var\(--control-md\);[^}]*white-space:\s*nowrap;/s);
  assert.match(composerRoutes, /@media \(max-width: 620px\)[\s\S]*?\.composer-mobile-edit-header > button\s*\{[^}]*min-height:\s*44px;/s);
  assert.match(composerRoutes, /@media \(max-width: 620px\)[\s\S]*?\.composer-mobile-settings-row > button\s*\{[^}]*min-height:\s*48px;/s);
  assert.match(composerRoutes, /@media \(max-width: 620px\)[\s\S]*?\.director-panel-triggers\s*\{\s*display:\s*none;/s);
});

test("Studio composers participate in shell layout and preserve short-phone reachability", () => {
  assert.match(
    shells,
    /\.app-shell\.is-community-home\s*\{[^}]*grid-template-rows:\s*var\(--shell-topbar-size\) minmax\(0, 1fr\) auto;/s,
  );
  assert.match(
    shells,
    /\.app-shell\.is-community-home > \.main-canvas\s*\{[^}]*grid-row:\s*2;[\s\S]*?\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*3;/,
  );
  assert.match(
    composerRoutes,
    /\.community-composer\s*\{[^}]*position:\s*relative;[^}]*display:\s*grid;[^}]*overflow:\s*hidden;/s,
  );
  assert.match(composerRoutes, /\.community-composer\.is-expanded\s*\{[^}]*max-height:\s*min\(65dvh, 540px\);/s);
  assert.match(composerRoutes, /@media \(max-width: 620px\)[\s\S]*?\.community-composer\[data-mobile-open="false"\]\s*\{[^}]*height:\s*72px;/s);
  assert.match(composerRoutes, /@media \(max-width: 620px\)[\s\S]*?\.community-composer\[data-mobile-open="true"\]\s*\{[^}]*height:\s*100%;[^}]*max-height:\s*none;/s);
  assert.match(shells, /\.app-shell\.is-community-home\.is-mobile-composer-open > \.main-canvas\s*\{\s*display:\s*none;/);
  assert.doesNotMatch(composerRoutes, /position:\s*fixed/);
  assert.doesNotMatch(mobileStudio, /max-height:\s*640px/);
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
