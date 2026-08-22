import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { parse } from "postcss";

const mobileStudio = await readFile(
  new URL("../src/design-system/mobile-studio.css", import.meta.url),
  "utf8",
);
const appSource = await readFile(
  new URL("../src/App.jsx", import.meta.url),
  "utf8",
);

test("mobile Studio layer parses and cannot style Company or Operations", () => {
  assert.doesNotThrow(() => parse(mobileStudio));
  assert.doesNotMatch(mobileStudio, /\.control-shell|\.ops-console/);
  assert.doesNotMatch(mobileStudio, /(?:linear|radial)-gradient\(/);
  assert.doesNotMatch(mobileStudio, /!important/);
  assert.doesNotMatch(mobileStudio, /font-size:\s*(?:8|9|10|11)px\s*;/);
});

test("phone chrome keeps all controls reachable and preserves seven-route navigation", () => {
  assert.match(
    mobileStudio,
    /@media \(max-width: 720px\)[\s\S]*?--studio-mobile-nav-height:\s*68px;[\s\S]*?--shell-topbar-size:\s*60px;/,
  );
  for (const control of [
    ".demo-account-switcher",
    ".surface-switch",
    ".skin-switcher",
    ".user-button",
  ]) {
    assert.ok(mobileStudio.includes(control), `${control} remains represented`);
  }
  assert.match(
    mobileStudio,
    /> \.side-nav > div\s*\{[\s\S]*?overflow-x:\s*auto;[\s\S]*?overscroll-behavior-inline:\s*contain;/,
  );
  assert.match(appSource, /const NAV_ITEMS = \[[\s\S]*?首页[\s\S]*?创作[\s\S]*?素材[\s\S]*?作品[\s\S]*?发布[\s\S]*?历史/);
  assert.match(appSource, />设置<\/span>/);
  assert.match(
    mobileStudio,
    /@media \(max-width: 360px\)[\s\S]*?> \.side-nav > div > button,[\s\S]*?> \.side-nav > button\s*\{[\s\S]*?width:\s*44px;[\s\S]*?min-width:\s*44px;[\s\S]*?flex:\s*0 0 44px;/,
  );
  assert.match(
    mobileStudio,
    /> \.side-nav button\.is-active::before,[\s\S]*?left:\s*50%;[\s\S]*?width:\s*18px;[\s\S]*?height:\s*2px;/,
  );
});

test("home is media-first with one compact filter rail and an editorial mosaic", () => {
  assert.match(
    mobileStudio,
    /\.community-toolbar\s*\{[\s\S]*?display:\s*flex;[\s\S]*?min-height:\s*56px;[\s\S]*?overflow-x:\s*auto;/,
  );
  assert.match(
    mobileStudio,
    /\.community-hero\s*\{[\s\S]*?height:\s*clamp\(310px, 86vw, 360px\);/,
  );
  assert.match(
    mobileStudio,
    /\.community-feed-grid\s*\{[\s\S]*?grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\)/,
  );
});

test("creation records own the viewport and every filter remains swipe-reachable", () => {
  assert.match(
    mobileStudio,
    /\.is-creation-hub \.creation-toolbar\s*\{[\s\S]*?display:\s*flex;[\s\S]*?overflow-x:\s*auto;[\s\S]*?overscroll-behavior-inline:\s*contain;/,
  );
  assert.match(
    mobileStudio,
    /\.is-creation-hub[\s\S]*?> \.community-composer,[\s\S]*?width:\s*calc\(100% - var\(--studio-mobile-edge\) - var\(--studio-mobile-edge\)\);/,
  );
  assert.doesNotMatch(
    mobileStudio,
    /\.is-creation-hub[^{}]*> \.community-composer[^{}]*\{[^}]*position:\s*fixed;/,
  );
});

test("collapsed composer is a 168px command sheet with full settings one tap away", () => {
  assert.match(
    mobileStudio,
    /> \.community-composer:not\(\.is-expanded\)\s*\{[\s\S]*?grid-template-rows:\s*46px 64px 58px;[\s\S]*?height:\s*168px;/,
  );
  assert.match(
    mobileStudio,
    /> \.community-composer:not\(\.is-expanded\)[\s\S]*?\.model-section,[\s\S]*?\.composer-mode-rail\s*\{\s*display:\s*none;/,
  );
  assert.match(appSource, /className="composer-settings-button"[\s\S]*?aria-expanded=\{composerExpanded\}/);
  assert.match(appSource, /composerExpanded \? "收起设置" : "详细设置"/);
  assert.match(appSource, /id="model"/);
  assert.match(appSource, /id="generation-mode"/);
});

test("short phones use the existing disclosure as a complete composer launcher", () => {
  assert.match(
    mobileStudio,
    /@media \(max-width: 360px\) and \(max-height: 640px\)[\s\S]*?> \.community-composer:not\(\.is-expanded\)\s*\{[\s\S]*?grid-template-rows:\s*46px;[\s\S]*?height:\s*46px;/,
  );
  assert.match(
    mobileStudio,
    /> \.community-composer:not\(\.is-expanded\)[\s\S]*?:is\(\.inspector-scroll, \.inspector-actions\)\s*\{\s*display:\s*none;/,
  );
  assert.match(
    mobileStudio,
    /\.community-hero\s*\{\s*height:\s*clamp\(228px, 72vw, 260px\);/,
  );
});

test("mobile Studio controls retain the 44px touch and 12px type floors", () => {
  const touchDeclarations = mobileStudio.match(/min-height:\s*44px;/g) || [];
  assert.ok(touchDeclarations.length >= 12, "frequent controls keep 44px targets");
  assert.match(mobileStudio, /font-size:\s*var\(--text-caption, 12px\)/);
  assert.match(mobileStudio, /font-size:\s*var\(--text-body-sm, 13px\)/);
});
