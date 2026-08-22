import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const community = await readFile(
  new URL("../src/community.css", import.meta.url),
  "utf8",
);
const controls = await readFile(
  new URL("../src/design-system/controls.css", import.meta.url),
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

test("fixed-height product shells do not clamp short desktop viewports", () => {
  assert.match(
    shells,
    /\.app-shell\s*\{[\s\S]*?height:\s*100dvh;[\s\S]*?min-height:\s*0;[\s\S]*?overflow:\s*hidden;/,
  );
  assert.match(
    shells,
    /\.control-shell\s*\{[\s\S]*?height:\s*100dvh;[\s\S]*?min-height:\s*0;[\s\S]*?overflow:\s*hidden;/,
  );
  assert.doesNotMatch(shells, /min-height:\s*(?:560|640|720)px/);
  assert.doesNotMatch(community, /min-height:\s*(?:640|720)px/);
});

test("Operations owns a viewport-height vertical scroll container", () => {
  assert.match(
    shells,
    /\.ops-console\s*\{[\s\S]*?height:\s*100dvh;[\s\S]*?min-height:\s*0;[\s\S]*?overflow-x:\s*hidden;[\s\S]*?overflow-y:\s*auto;[\s\S]*?overscroll-behavior-y:\s*contain;[\s\S]*?scrollbar-gutter:\s*stable;/,
  );
});

test("home composer owns a real viewport clearance zone", () => {
  assert.match(appSource, /composerExpanded \? "is-composer-expanded" : ""/);
  assert.match(
    shells,
    /\.app-shell\.is-community-home:not\(\.is-creation-hub\) > \.main-canvas\s*\{[\s\S]*?margin-block-end:\s*var\(--studio-home-composer-clearance\);/,
  );
  assert.match(
    shells,
    /\.app-shell\.is-community-home\.is-composer-expanded:not\(\.is-creation-hub\)/,
  );
  assert.doesNotMatch(
    shells,
    /\.app-shell\.is-creation-hub[^{}]*> \.main-canvas[^{}]*margin-block-end/,
  );
});

test("secondary pages can scroll their final action above the expanded taskbar", () => {
  assert.match(
    shells,
    /\.app-shell\.is-secondary-page \.secondary-view\s*\{[\s\S]*?padding-block-end:\s*max\(var\(--shell-page-end\), 252px\);[\s\S]*?scroll-padding-block-end:\s*252px;/,
  );
  assert.match(
    shells,
    /@media \(max-width: 1120px\)[\s\S]*?\.app-shell\.is-secondary-page \.secondary-view[\s\S]*?padding-block-end:\s*var\(--shell-page-end\);/,
  );
});

test("Studio mobile controls keep the 44px touch contract after compact rules", () => {
  const touchStart = controls.indexOf("@media (max-width: 900px)");
  assert.ok(touchStart >= 0);
  const touchRules = controls.slice(touchStart);
  for (const selector of [
    ".creation-media-tabs button",
    ".creation-search button",
    ".creation-toolbar button",
    ".creation-select",
    ".creation-layout-switch button",
    ".creation-task-check",
    ".creation-hub-state button",
    ".creation-pagination button",
    ".creation-task-preview-load",
    ".creation-task-open",
    ".secondary-view",
    ".publication-center",
    ".taskbar",
  ]) {
    assert.ok(touchRules.includes(selector), `${selector} must use the touch contract`);
  }
  assert.match(
    touchRules,
    /\.app-shell\[data-theme\]\.is-secondary-page[\s\S]*?:is\(\.secondary-view, \.publication-center, \.taskbar\)[\s\S]*?button,[\s\S]*?select,[\s\S]*?input:not\(\[type="checkbox"\]\):not\(\[type="radio"\]\):not\(\[type="file"\]\)[\s\S]*?min-height:\s*var\(--control-size-touch\);/,
  );
  assert.match(touchRules, /min-height:\s*var\(--control-size-touch\);/);
  assert.doesNotMatch(controls, /button\[aria-label\]:not\(\[class\]\)/);
});

test("Studio extreme phone navigation scrolls instead of shrinking touch targets", () => {
  const extremePhoneRules = shells.slice(shells.indexOf("@media (max-width: 390px)"));
  assert.match(
    extremePhoneRules,
    /\.app-shell > \.side-nav > div\s*\{[^}]*overflow-x:\s*auto;[^}]*overscroll-behavior-inline:\s*contain;/,
  );
  assert.match(
    extremePhoneRules,
    /\.app-shell > \.side-nav > div > button\s*\{[^}]*width:\s*52px;[^}]*min-width:\s*52px;[^}]*flex:\s*0 0 52px;[\s\S]*?\.app-shell > \.side-nav > button\s*\{[^}]*width:\s*56px;[^}]*min-width:\s*56px;[^}]*flex:\s*0 0 56px;/,
  );
  assert.match(
    chrome,
    /@media \(max-width: 900px\)[\s\S]*?\.app-shell > \.side-nav\s*\{[^}]*padding:\s*3px 6px 4px;[\s\S]*?\.app-shell > \.side-nav > div\s*\{[^}]*height:\s*56px;[\s\S]*?\.app-shell > \.side-nav button,[\s\S]*?height:\s*56px;[^}]*min-height:\s*56px;/,
  );
  assert.match(
    chrome,
    /@media \(max-width: 390px\)[\s\S]*?\.app-shell > \.side-nav > div\s*\{[^}]*gap:\s*2px;/,
  );
});

test("Operations phone actions override shared compact exceptions", () => {
  const phoneRules = controls.slice(controls.indexOf("@media (max-width: 620px)", controls.indexOf("@media (max-width: 900px)")));
  assert.match(
    phoneRules,
    /\.ops-console\.ops-console[\s\S]*?\.ops-entitlement-cell\.ops-entitlement-cell\s*\{[^}]*height:\s*var\(--control-size-touch\);[^}]*min-height:\s*var\(--control-size-touch\);/,
  );
});
