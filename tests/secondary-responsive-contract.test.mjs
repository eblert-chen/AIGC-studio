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
  assert.match(shellTablet, /\.app-shell\s*>\s*\.main-canvas\s*\{[\s\S]*?grid-column:\s*1;[\s\S]*?grid-row:\s*3;/);
  assert.match(chromeTablet, /\.app-shell \.side-nav-track\s*\{[^}]*flex-direction:\s*row;[^}]*overflow-x:\s*auto;/);
  assert.match(
    phone,
    /\.app-shell\[data-theme\] > \.side-nav \.side-nav-track\s*\{[^}]*overflow-x:\s*auto;/,
  );
  assert.match(phone, /overscroll-behavior-inline:\s*contain/);
});

test("secondary routes keep one bounded scrolling work surface", () => {
  assert.match(
    shellSource,
    /\.app-shell > \.main-canvas\s*\{[^}]*min-height:\s*0;[^}]*overflow:\s*auto;/s,
  );
  assert.match(shellSource, /\.app-shell\.is-secondary-page > \.main-canvas\s*\{[^}]*grid-row:\s*2;/);
  assert.match(studioRoutesSource, /\.settings-list,/);
  assert.match(studioRoutesSource, /\.artwork-item\s*\{/);
  assert.doesNotMatch(studioRoutesSource, /\.secondary-view\s*\{[^}]*overflow-y:/s);
});

test("secondary task state remains actionable above the phone navigation", () => {
  assert.match(
    studioRoutesSource,
    /\.taskbar\s*\{[^}]*position:\s*relative;[^}]*width:\s*calc\(100% - var\(--shell-page-gutter\) - var\(--shell-page-gutter\)\);[^}]*margin:\s*0 var\(--shell-page-gutter\) 12px;[^}]*border-radius:\s*14px;[^}]*box-shadow:\s*0 1px 2px/s,
  );
  assert.match(
    shellSource,
    /\.app-shell\.is-secondary-page > \.taskbar\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*3;/,
  );
  assert.match(
    shellSource,
    /@media \(max-width: 900px\)[\s\S]*?\.app-shell\.is-secondary-page > \.taskbar\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*4;/,
  );
  assert.match(mobileSource, /\.app-shell\[data-theme\] \.taskbar button\s*\{[^}]*min-height:\s*44px;/s);
});

test("the material library uses content-aware columns", () => {
  assert.match(
    studioRoutesSource,
    /\.asset-grid\s*\{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/s,
  );
  assert.match(studioRoutesSource, /@media \(max-width: 1240px\)[\s\S]*?\.asset-grid\s*\{[^}]*repeat\(3, minmax\(0, 1fr\)\)/s);
  assert.match(studioRoutesSource, /@media \(max-width: 760px\)[\s\S]*?\.asset-grid,[\s\S]*?grid-template-columns:\s*1fr;/s);
});

test("the account center becomes a usable single-column phone form", () => {
  const phone = mediaBlocks(studioRoutesSource, 760);

  assert.match(
    phone,
    /\.account-section\s*\{[^}]*grid-template-columns:\s*1fr;/,
  );
  assert.match(
    studioRoutesSource,
    /\.account-profile-form,[\s\S]*?\.account-session-list\s*\{[^}]*display:\s*grid;/,
  );
  assert.match(
    studioRoutesSource,
    /\.account-profile-form label\s*\{[^}]*display:\s*grid;/,
  );
  assert.match(
    mobileSource,
    /\.app-shell\[data-theme\] \.secondary-view input:not\(\[type="checkbox"\]\):not\(\[type="radio"\]\):not\(\[type="file"\]\),[\s\S]*?min-height:\s*44px;/,
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
    /const demoPersonaControl = demoMode && activeDemoPersonaHost[\s\S]*?createPortal\([\s\S]*?<DemoAccountSwitcher/,
  );
  assert.equal((managementSource.match(/<DemoAccountSwitcher\b/g) || []).length, 1);
  immediatelyGuardedBy(
    managementSource,
    "control-live-session-desktop",
    /\{(?:!demoMode|liveMode)\s*&&\s*\(?\s*$/,
  );
});
