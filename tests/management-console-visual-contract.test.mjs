import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { operationsSource } from "./operations-source.mjs";

const managementCss = await readFile(
  new URL("../src/styles.css", import.meta.url),
  "utf8",
);
const operationsCss = await readFile(
  new URL("../src/design-system/operations-routes.css", import.meta.url),
  "utf8",
);
const mobileOperationsCss = await readFile(
  new URL("../src/design-system/mobile-operations.css", import.meta.url),
  "utf8",
);
const tokens = await readFile(
  new URL("../src/design-system/tokens.css", import.meta.url),
  "utf8",
);
const managementSource = await readFile(
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  "utf8",
);

test("company management uses the shared 12px readable floor", () => {
  assert.match(
    managementCss,
    /\.control-shell\s*\{[^}]*--control-caption-size:\s*var\(--text-caption, 12px\)\s*;/s,
  );

  const rules = [...managementCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  const undersizedControlRules = rules.filter(([, selector, body]) => (
    selector.includes(".control-")
    && /font-size:\s*(?:8|9|10)px\s*;/i.test(body)
  ));

  assert.deepEqual(
    undersizedControlRules.map(([, selector]) => selector.trim()),
    [],
    "control-* rules must not reintroduce 8-10px data text",
  );
});

test("company page hierarchy is restrained and programmatic drawer focus has no hard outline", () => {
  assert.match(
    managementCss,
    /\.control-page-header h1\s*\{[^}]*font-size:\s*clamp\(23px,\s*2\.2vw,\s*28px\)/s,
  );
  assert.match(
    managementCss,
    /\.control-page-header\s*>\s*div:first-child\s*>\s*span\s*\{[^}]*font-size:\s*var\(--control-caption-size\)[^}]*letter-spacing:\s*0\.05em/s,
  );
  assert.match(
    managementCss,
    /\.control-drawer:focus,[\s\S]*?\.control-drawer:focus-visible\s*\{[^}]*outline:\s*none\s*;/,
  );
});

test("company navigation keeps the restored active section visible", () => {
  assert.match(managementSource, /const controlNavRef = useRef\(null\)/);
  assert.match(managementSource, /const activeNavItemRef = useRef\(null\)/);
  assert.match(managementSource, /activeItem\.scrollIntoView\(\{ block: "nearest", inline: "center" \}\)/);
  assert.match(managementSource, /scrollRoot\.scrollTop = documentScroll\.top/);
  assert.match(managementSource, /new ResizeObserver\(keepActiveItemVisible\)/);
  assert.match(managementSource, /<nav ref=\{controlNavRef\}/);
  assert.match(managementSource, /ref=\{section === id \? activeNavItemRef : null\}/);
});

test("operations navigation uses accessible controls instead of covering module labels", () => {
  assert.match(
    operationsCss,
    /\.ops-topbar nav\s*\{[^}]*overflow-x:\s*auto[^}]*scrollbar-width:\s*thin/s,
  );
  assert.match(
    operationsCss,
    /\.ops-module-navigation\s*\{[^}]*grid-template-columns:\s*40px minmax\(0, 1fr\) 40px/s,
  );
  assert.match(
    operationsSource,
    /navigation\.scrollWidth - navigation\.clientWidth/,
  );
  assert.match(
    operationsSource,
    /aria-label="查看前面的平台模块"[\s\S]*?aria-label="查看更多平台模块"/,
  );
  assert.doesNotMatch(operationsCss, /ops-nav-overflow-hint/);
  assert.doesNotMatch(operationsSource, /ops-nav-overflow-hint/);
  assert.match(
    mobileOperationsCss,
    /@media\s*\(max-width:\s*820px\)[\s\S]*?\.ops-module-navigation\s*\{[^}]*grid-row:\s*2/s,
  );
});

test("operations focus and modal scrim follow the active light-skin tokens", () => {
  assert.match(
    operationsCss,
    /outline:\s*2px solid var\(--ops-focus-ring,\s*var\(--ops-accent\)\)/,
  );
  assert.match(
    tokens,
    /--ops-focus-ring:\s*var\(--focus-ring\)/,
  );
  assert.match(
    operationsCss,
    /\.ops-overlay\s*\{[^}]*background:\s*var\(--drawer-scrim\)/s,
  );
  assert.match(
    operationsCss,
    /\.ops-filterbar \.ops-search\s*\{[^}]*background:\s*var\(--ops-surface\)/s,
  );
  assert.match(
    operationsCss,
    /\.ops-worklist > div > button\s*\{[^}]*background:\s*var\(--ops-surface\)/s,
  );
  assert.match(
    operationsCss,
    /\.ops-callout\.is-warning\s*\{[^}]*background:\s*var\(--ops-orange-soft\)[^}]*color:\s*var\(--ops-orange\)/s,
  );
});
