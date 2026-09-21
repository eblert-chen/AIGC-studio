import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { operationsSource } from "./operations-source.mjs";
import { managementSource } from "./management-source.mjs";

const managementCss = await readFile(
  new URL("../src/design-system/management-routes.css", import.meta.url),
  "utf8",
);
const controlsCss = await readFile(
  new URL("../src/design-system/controls.css", import.meta.url),
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

test("company management uses the shared 12px readable floor", () => {
  assert.match(
    controlsCss,
    /--control-label-size:\s*var\(--text-caption, 0\.75rem\)/,
  );

  const rules = [...`${managementCss}\n${controlsCss}`.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  const undersizedControlRules = rules.filter(([, selector, body]) => (
    selector.includes(".control-")
    && /font-size:\s*(?:8|9|10|11)px\s*;/i.test(body)
  ));

  assert.deepEqual(
    undersizedControlRules.map(([, selector]) => selector.trim()),
    [],
    "control-* rules must not reintroduce sub-12px data text",
  );
});

test("company page hierarchy is restrained and programmatic drawer focus has no hard outline", () => {
  assert.match(
    managementCss,
    /\.control-shell \.control-page-header h1\s*\{[^}]*font-size:\s*clamp\(27px,\s*2\.4vw,\s*34px\)/s,
  );
  assert.match(
    managementCss,
    /\.control-shell \.control-page-header \.control-page-context\s*\{[^}]*color:\s*var\(--management-cobalt\);[^}]*font-size:\s*12px;[^}]*letter-spacing:\s*0\.05em/s,
  );
  assert.match(
    managementCss,
    /\.control-shell \.control-drawer:focus,[\s\S]*?\.control-shell \.control-drawer:focus-visible\s*\{[^}]*outline:\s*none\s*;/,
  );
  assert.match(
    controlsCss,
    /\):focus-visible\s*\{[^}]*outline:\s*2px solid var\(--control-focus-ring\);[^}]*outline-offset:\s*2px;/s,
  );
  assert.match(managementCss, /\.control-shell \.control-section\s*\{[^}]*border-top:\s*1px solid var\(--management-graphite\);[^}]*border-radius:\s*0;[^}]*box-shadow:\s*none;/s);
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
    /\.ops-console \.ops-module-navigation\s*\{[^}]*grid-template-columns:\s*36px minmax\(0, 1fr\) 36px/s,
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
