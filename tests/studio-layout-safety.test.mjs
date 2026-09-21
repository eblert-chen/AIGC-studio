import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const studioRoutes = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);
const home = await readFile(
  new URL("../src/design-system/home.css", import.meta.url),
  "utf8",
);
const composer = await readFile(
  new URL("../src/design-system/composer.css", import.meta.url),
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
const mobileStudio = await readFile(
  new URL("../src/design-system/mobile-studio.css", import.meta.url),
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
  assert.doesNotMatch(`${studioRoutes}\n${home}\n${composer}\n${mobileStudio}`, /min-height:\s*(?:640|720)px/);
});

test("Operations owns a viewport-height vertical scroll container", () => {
  assert.match(
    shells,
    /\.ops-console\s*\{[\s\S]*?height:\s*100dvh;[\s\S]*?min-height:\s*0;[\s\S]*?overflow-x:\s*hidden;[\s\S]*?overflow-y:\s*auto;[\s\S]*?overscroll-behavior-y:\s*contain;[\s\S]*?scrollbar-gutter:\s*stable;/,
  );
});

test("primary Studio composer owns an in-flow viewport row", () => {
  assert.match(appSource, /composerExpanded \? "is-composer-expanded" : ""/);
  assert.match(
    shells,
    /\.app-shell\.is-community-home\s*\{[^}]*grid-template-rows:\s*var\(--shell-topbar-size\) minmax\(0, 1fr\) auto;/s,
  );
  assert.match(
    shells,
    /\.app-shell\.is-community-home > \.main-canvas\s*\{[^}]*grid-row:\s*2;[\s\S]*?\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*3;/,
  );
  assert.match(
    composer,
    /\.community-composer\s*\{[^}]*position:\s*relative;[^}]*display:\s*grid;[^}]*overflow:\s*hidden;/s,
  );
  assert.doesNotMatch(composer, /position:\s*fixed/);
});

test("secondary task state participates in shell layout and never covers route actions", () => {
  assert.match(
    shells,
    /\.app-shell\.is-secondary-page\s*\{[^}]*grid-template-rows:\s*var\(--shell-topbar-size\) minmax\(0, 1fr\) auto;/,
  );
  assert.match(
    shells,
    /\.app-shell\.is-secondary-page > \.taskbar\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*3;/,
  );
  assert.match(
    shells,
    /@media \(max-width: 900px\)[\s\S]*?\.app-shell\.is-secondary-page > \.taskbar\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*4;/,
  );
  assert.match(studioRoutes, /\.taskbar\s*\{[^}]*position:\s*relative;[^}]*width:\s*calc\(100% - var\(--shell-page-gutter\) - var\(--shell-page-gutter\)\);[^}]*margin:\s*0 var\(--shell-page-gutter\) 12px;[^}]*border-radius:\s*14px;[^}]*box-shadow:\s*0 1px 2px/s);
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
  assert.match(
    mobileStudio,
    /@media \(max-width:\s*720px\)[\s\S]*?\.app-shell\[data-theme\] > \.side-nav \.side-nav-track\s*\{[^}]*overflow-x:\s*auto;[^}]*overscroll-behavior-inline:\s*contain;[^}]*scrollbar-width:\s*none;/,
  );
  assert.match(
    mobileStudio,
    /@media \(max-width:\s*360px\)[\s\S]*?\.app-shell\[data-theme\] > \.side-nav \.side-nav-track button\s*\{[^}]*width:\s*68px;[^}]*min-width:\s*68px;[^}]*flex:\s*0 0 68px;/,
  );
  assert.match(
    chrome,
    /@media \(max-width:\s*900px\)[\s\S]*?\.app-shell \.side-nav-track\s*\{[^}]*overflow-x:\s*auto;[^}]*overflow-y:\s*hidden;[\s\S]*?\.app-shell > \.side-nav button\s*\{[^}]*min-width:\s*82px;[^}]*min-height:\s*53px;/,
  );
  assert.match(chrome, /> \.side-nav > \.side-nav-scroll-forward\s*\{[^}]*display:\s*grid;[^}]*width:\s*44px;/s);
  assert.doesNotMatch(`${shells}\n${chrome}\n${mobileStudio}`, /\.side-nav-track\s*\{[^}]*overflow-x:\s*hidden/s);
});

test("Operations phone actions override shared compact exceptions", () => {
  const phoneRules = controls.slice(controls.indexOf("@media (max-width: 620px)", controls.indexOf("@media (max-width: 900px)")));
  assert.match(
    phoneRules,
    /\.ops-console\.ops-console[\s\S]*?\.ops-entitlement-cell\.ops-entitlement-cell\s*\{[^}]*height:\s*var\(--control-size-touch\);[^}]*min-height:\s*var\(--control-size-touch\);/,
  );
});
