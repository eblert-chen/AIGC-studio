import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { parse } from "postcss";

const routes = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);

function mediaBlock(source, maxWidth) {
  const marker = `@media (max-width: ${maxWidth}px)`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `${marker} must exist`);
  const open = source.indexOf("{", start);
  let depth = 1;
  let cursor = open + 1;
  while (cursor < source.length && depth > 0) {
    if (source[cursor] === "{") depth += 1;
    if (source[cursor] === "}") depth -= 1;
    cursor += 1;
  }
  assert.equal(depth, 0, `${marker} must close`);
  return source.slice(open + 1, cursor - 1);
}

test("Studio route stylesheet parses and remains isolated from other product surfaces", () => {
  assert.doesNotThrow(() => parse(routes));
  assert.match(
    routes,
    /^:is\(\.app-shell\.is-secondary-page, \.app-shell\.is-creation-hub\)\s*\{/m,
  );
  assert.doesNotMatch(routes, /(?:^|\n)\s*:root\s*\{/);
  assert.doesNotMatch(routes, /\.control-shell|\.ops-console|\.community-home|\.community-composer/);
});

test("non-home Studio routes share one precise header and readable type floor", () => {
  assert.match(
    routes,
    /:is\(\.creation-hub-titlebar, \.secondary-heading, \.publication-heading\)[\s\S]*?border-block-end:\s*1px solid var\(--studio-route-divider\)/,
  );
  assert.match(
    routes,
    /:is\(\.creation-hub-heading, \.secondary-heading, \.publication-heading\)[\s\S]*?h1\s*\{[\s\S]*?font-size:\s*clamp\(1\.625rem/,
  );
  assert.match(routes, /font-size:\s*var\(--text-body-sm, 13px\)/);
  assert.doesNotMatch(routes, /font-size:\s*(?:8|9|10|11)px\s*;/);
});

test("creation records use a continuous toolbar and media-first adaptive grid", () => {
  assert.match(
    routes,
    /\.creation-toolbar\s*\{[\s\S]*?display:\s*grid;[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) auto/,
  );
  assert.match(
    routes,
    /\.creation-task-grid:is\(\.is-grid, \.is-compact\)\s*\{[\s\S]*?repeat\(\s*auto-fill,[\s\S]*?minmax\(min\(240px, 100%\), 1fr\)/,
  );
  assert.match(
    routes,
    /\.creation-hub \.creation-task-preview\s*\{[\s\S]*?border-radius:\s*var\(--studio-route-media-radius\)/,
  );
  assert.match(
    routes,
    /\.creation-hub-state\s*\{[\s\S]*?border:\s*0;[\s\S]*?background:\s*transparent;/,
  );
});

test("materials and works keep real media dominant without card-on-card containers", () => {
  assert.match(
    routes,
    /\.media-view \.asset-grid\s*\{[\s\S]*?repeat\(\s*auto-fill,[\s\S]*?minmax\(min\(238px, 100%\), 1fr\)/,
  );
  assert.match(
    routes,
    /:is\(\.asset-item, \.asset-upload\)\s*\{[\s\S]*?border:\s*0;[\s\S]*?background:\s*transparent;[\s\S]*?box-shadow:\s*none;/,
  );
  assert.match(
    routes,
    /\.artwork-grid\s*\{[\s\S]*?repeat\(\s*auto-fill,[\s\S]*?minmax\(min\(330px, 100%\), 1fr\)/,
  );
  assert.match(
    routes,
    /\.artwork-media\s*\{[\s\S]*?aspect-ratio:\s*16 \/ 10;[\s\S]*?border-radius:\s*var\(--studio-route-media-radius\)/,
  );
  assert.doesNotMatch(routes, /(?:linear|radial)-gradient\(/);
});

test("history and settings are compact continuous work surfaces", () => {
  assert.match(
    routes,
    /\.task-history-row\s*\{[\s\S]*?border-block-end:\s*1px solid var\(--studio-route-divider\);[\s\S]*?background:\s*transparent;/,
  );
  assert.match(
    routes,
    /\.task-audit-grid\s*\{[\s\S]*?grid-template-columns:[\s\S]*?minmax\(230px, 2fr\)/,
  );
  assert.match(
    routes,
    /\.settings-list\s*\{[\s\S]*?width:\s*min\(900px, 100%\);[\s\S]*?border-block-start:\s*1px solid/,
  );
  assert.match(
    routes,
    /\.setting-row\s+input\[type="checkbox"\]\s*\{[\s\S]*?width:\s*46px;[\s\S]*?appearance:\s*none;/,
  );
});

test("publishing remains a clean split work surface with restrained overlays", () => {
  assert.match(
    routes,
    /\.publication-center \.publication-layout\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0, 1fr\) minmax\(300px, 350px\)/,
  );
  assert.match(
    routes,
    /:is\(\.publication-jobs, \.publication-connections\)\s*\{[\s\S]*?border-radius:\s*0;[\s\S]*?background:\s*transparent;[\s\S]*?box-shadow:\s*none;/,
  );
  assert.match(
    routes,
    /\.publication-center \.publication-empty\s*\{[\s\S]*?min-height:\s*164px;[\s\S]*?text-align:\s*start;/,
  );
  assert.match(
    routes,
    /:is\(\.publication-dialog, \.publication-details, \.publication-reconcile\)\s*\{[\s\S]*?border-radius:\s*var\(--studio-route-overlay-radius\)/,
  );
});

test("mobile Studio routes collapse to one column and retain 44px controls", () => {
  const mobile = mediaBlock(routes, 620);
  assert.match(
    mobile,
    /\.creation-task-grid:is\(\.is-grid, \.is-compact\)\s*\{\s*grid-template-columns:\s*1fr/,
  );
  assert.match(
    mobile,
    /\.media-view[\s\S]*?\.asset-grid,[\s\S]*?\.artwork-grid\s*\{\s*grid-template-columns:\s*1fr/,
  );
  assert.match(mobile, /min-height:\s*var\(--control-lg, 44px\)/);
  assert.match(
    mobile,
    /\.history-toolbar\s*\{[\s\S]*?grid-template-columns:\s*1fr/,
  );
  assert.match(
    routes,
    /@media \(max-width: 900px\)[\s\S]*?\.creation-task-open\s*\{[^}]*min-width:\s*var\(--control-lg, 44px\);[^}]*min-height:\s*var\(--control-lg, 44px\)/,
  );
  assert.match(
    routes,
    /\.scope-control button,[\s\S]*?\.artwork-copy footer \.text-button,[\s\S]*?\.download-button,[\s\S]*?\.artwork-followup-actions button,[\s\S]*?\.task-history-row footer \.text-button\s*\{[^}]*min-height:\s*var\(--control-lg, 44px\)/,
  );
});

test("route layer avoids specificity debt and leaves the in-flow composer alone", () => {
  assert.doesNotMatch(routes, /!important/);
  assert.doesNotMatch(routes, /:has\(/);
  assert.doesNotMatch(routes, /\.community-composer|\.inspector-scroll|\.inspector-actions/);
  assert.match(routes, /@media \(prefers-reduced-motion: reduce\)/);
});
