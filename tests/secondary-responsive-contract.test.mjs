import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const managementSource = await readFile(
  new URL("../src/ManagementConsole.jsx", import.meta.url),
  "utf8",
);
const shellSource = await readFile(
  new URL("../src/design-system/shells.css", import.meta.url),
  "utf8",
);
const chromeSource = await readFile(
  new URL("../src/design-system/chrome.css", import.meta.url),
  "utf8",
);
const composerSource = await readFile(
  new URL("../src/design-system/composer.css", import.meta.url),
  "utf8",
);
const mobileSource = await readFile(
  new URL("../src/design-system/mobile-studio.css", import.meta.url),
  "utf8",
);
const studioRoutesSource = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);

function mediaBlocks(source, maxWidth) {
  const marker = `@media (max-width: ${maxWidth}px)`;
  const blocks = [];
  let cursor = 0;

  while (cursor < source.length) {
    const markerIndex = source.indexOf(marker, cursor);
    if (markerIndex < 0) break;
    const openBrace = source.indexOf("{", markerIndex + marker.length);
    assert.notEqual(openBrace, -1, `${marker} must have an opening brace`);
    let depth = 1;
    let index = openBrace + 1;
    while (index < source.length && depth > 0) {
      if (source[index] === "{") depth += 1;
      if (source[index] === "}") depth -= 1;
      index += 1;
    }
    assert.equal(depth, 0, `${marker} must have a closing brace`);
    blocks.push(source.slice(openBrace + 1, index - 1));
    cursor = index;
  }

  assert.ok(blocks.length > 0, `${marker} must exist`);
  return blocks.join("\n");
}

function immediatelyGuardedBy(source, className, guardPattern) {
  const elementPattern = new RegExp(
    `<div[^>]*className="[^"]*${className}[^"]*"[^>]*>`,
  );
  const match = elementPattern.exec(source);
  assert.ok(match, `${className} must exist`);
  const nearbyPrefix = source.slice(Math.max(0, match.index - 220), match.index);
  assert.match(nearbyPrefix, guardPattern);
}

test("Studio becomes one column with a complete horizontal route rail", () => {
  const shellTablet = mediaBlocks(shellSource, 900);
  const chromeTablet = mediaBlocks(chromeSource, 900);
  const phone = mediaBlocks(mobileSource, 720);

  assert.match(shellTablet, /\.app-shell\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.match(shellTablet, /\.app-shell\s*>\s*\.main-canvas\s*\{[\s\S]*?grid-column:\s*1;[\s\S]*?grid-row:\s*2;/);
  assert.match(chromeTablet, /\.app-shell\s*>\s*\.side-nav\s*>\s*div\s*\{[\s\S]*?flex-direction:\s*row;/);
  assert.match(phone, /\.app-shell\[data-theme\]\s*>\s*\.side-nav\s*>\s*div\s*\{[\s\S]*?overflow-x:\s*auto;/);
  assert.match(phone, /overscroll-behavior-inline:\s*contain/);
});

test("secondary routes keep one bounded scrolling work surface", () => {
  assert.match(
    studioRoutesSource,
    /:is\(\.app-shell\.is-secondary-page,\s*\.app-shell\.is-creation-hub\)[\s\S]*?:is\(\.creation-hub,\s*\.secondary-view\)\s*\{[\s\S]*?height:\s*100%;[\s\S]*?min-height:\s*0;[\s\S]*?overflow-y:\s*auto;/,
  );
  assert.match(studioRoutesSource, /\.app-shell\.is-secondary-page \.settings-list\s*\{/);
  assert.match(studioRoutesSource, /\.app-shell\.is-secondary-page \.artwork-item\s*\{/);
});

test("secondary task state remains actionable above the phone navigation", () => {
  assert.match(
    composerSource,
    /\.taskbar\s*\{[\s\S]*?position:\s*fixed;[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto;/,
  );
  assert.match(
    mobileSource,
    /\.app-shell\[data-theme\]\.is-secondary-page\s*>\s*\.taskbar\s*\{[\s\S]*?position:\s*fixed;[\s\S]*?var\(--studio-mobile-nav-height\)/,
  );
  assert.match(
    mobileSource,
    /\.app-shell\[data-theme\]\.is-secondary-page\s*>\s*\.taskbar \.task-action\s*\{[\s\S]*?min-height:\s*44px;/,
  );
});

test("the material library uses content-aware columns", () => {
  assert.match(
    studioRoutesSource,
    /\.media-view \.asset-grid\s*\{[\s\S]*?grid-template-columns:\s*repeat\(\s*auto-(?:fit|fill),\s*minmax\(/,
  );
  assert.doesNotMatch(studioRoutesSource, /\.media-view \.asset-grid\s*\{[^}]*repeat\(3,/s);
});

test("the account center becomes a usable single-column phone form", () => {
  const phone = mediaBlocks(studioRoutesSource, 720);

  assert.match(
    phone,
    /\.app-shell\.is-secondary-page \.account-section\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\);/,
  );
  assert.match(
    phone,
    /\.app-shell\.is-secondary-page \.account-section\s*>\s*:not\(header\)\s*\{[\s\S]*?grid-column:\s*1;/,
  );
  assert.match(
    phone,
    /\.app-shell\.is-secondary-page \.account-profile-form\s*\{[\s\S]*?grid-template-columns:\s*minmax\(0,\s*1fr\);/,
  );
  assert.match(
    phone,
    /\.app-shell\.is-secondary-page \.account-center :is\(input,\s*select,\s*textarea\)[\s\S]*?min-height:\s*var\(--control-lg,\s*44px\);/,
  );
  assert.match(
    phone,
    /\.app-shell\.is-secondary-page \.account-center\s*\{[\s\S]*?padding-block-end:\s*calc\(var\(--control-lg,\s*44px\)\s*\+\s*var\(--space-8,\s*32px\)\);/,
  );
});

test("demo account switching replaces rather than duplicates signed-in identity", () => {
  assert.match(
    appSource,
    /\{DEMO_MODE && \(\s*<DemoAccountSwitcher[\s\S]*?\/>\s*\)\}/,
  );
  immediatelyGuardedBy(
    appSource,
    "popover-anchor user-anchor",
    /\{(?:LIVE_MODE|!DEMO_MODE)\s*&&\s*\(?\s*$/,
  );

  assert.match(
    managementSource,
    /\{demoMode && <DemoAccountSwitcher[\s\S]*?\/>\}/,
  );
  immediatelyGuardedBy(
    managementSource,
    "control-live-session-desktop",
    /\{(?:!demoMode|liveMode)\s*&&\s*\(?\s*$/,
  );
});
