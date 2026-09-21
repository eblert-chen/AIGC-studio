import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const editorSource = await readFile(new URL("../src/components/GenerationEditor.jsx", import.meta.url), "utf8");
const uiSource = `${appSource}\n${editorSource}`;
const composerCss = await readFile(
  new URL("../src/design-system/composer.css", import.meta.url),
  "utf8",
);
const shellCss = await readFile(
  new URL("../src/design-system/shells.css", import.meta.url),
  "utf8",
);

test("composer disclosures focus their heading without scrolling to the end", () => {
  assert.match(
    appSource,
    /const panelTop =\s*panel\.getBoundingClientRect\(\)\.top\s*-\s*scroller\.getBoundingClientRect\(\)\.top\s*\+\s*scroller\.scrollTop;/s,
  );
  assert.match(
    appSource,
    /const mobileSingleFocus = globalThis\.matchMedia\?\.\("\(max-width: 620px\)"\)\?\.matches;[\s\S]*?scroller\.scrollTo\(\{ top: mobileSingleFocus \? 0 : Math\.max\(0, panelTop\), behavior: "auto" \}\);/,
  );
  assert.doesNotMatch(
    appSource,
    /scroller\?\.scrollTo\?\.\(\{ top: scroller\.scrollHeight/,
  );
  assert.match(
    composerCss,
    /\.director-context-panel \[data-composer-panel-focus\]:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--focus-ring\);/s,
  );
});

test("phone watch mode keeps the result visible behind one compact creation launcher", () => {
  assert.match(
    shellCss,
    /@media \(max-width: 620px\)[\s\S]*?\.app-shell\.is-community-home\s*\{[^}]*grid-template-rows:\s*var\(--shell-topbar-size\) var\(--studio-mobile-nav-height, 50px\) minmax\(0, 1fr\) 72px;/,
  );
  assert.match(
    shellCss,
    /@media \(max-width: 620px\)[\s\S]*?\.app-shell\.is-community-home > \.main-canvas\s*\{[^}]*min-height:\s*0;[^}]*overflow:\s*auto;/,
  );
  assert.match(
    composerCss,
    /@media \(max-width: 620px\)[\s\S]*?\.community-composer\[data-mobile-open="false"\]\s*\{[^}]*height:\s*72px;[^}]*grid-template-rows:\s*72px;/,
  );
  assert.match(
    composerCss,
    /@media \(max-width: 620px\)[\s\S]*?\.composer-mobile-launcher\s*\{[^}]*min-height:\s*72px;/,
  );
  const phoneRules = composerCss.match(
    /@media \(max-width: 620px\) \{[\s\S]*?(?=\n@media \(max-width: 360px\))/,
  )?.[0] ?? "";
  assert.doesNotMatch(
    phoneRules,
    /\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*margin-top:\s*-/s,
    "the launcher remains in its own shell row instead of intruding into the stage",
  );
});

test("phone creation and configuration each own one focused work surface", () => {
  const phoneRules = composerCss.match(
    /@media \(max-width: 620px\) \{[\s\S]*?(?=\n@media \(max-width: 360px\))/,
  )?.[0] ?? "";

  assert.match(appSource, /const \[mobileComposerOpen, setMobileComposerOpen\] = useState\(false\)/);
  assert.match(appSource, /\$\{isQuickStudioView && mobileComposerOpen \? "is-mobile-composer-open" : ""\}/);
  assert.match(uiSource, /data-mobile-open=\{mobileComposerOpen\}/);
  assert.match(uiSource, /\{isQuick && <>\s*<button\s*id="mobile-composer-launcher"[\s\S]*?onClick=\{focusCommunityComposer\}/);
  assert.match(appSource, /const focusCommunityComposer = \(\) => \{[\s\S]*?setComposerPanel\(null\);[\s\S]*?setMobileComposerOpen\(true\);[\s\S]*?focusComposerPrompt\(\);/);
  assert.match(appSource, /const focusComposerPrompt = \(\) => \{[\s\S]*?#prompt/);
  assert.match(uiSource, /className="composer-mobile-edit-header"[\s\S]*?aria-label=\{activeNav === "create" \? "返回当前成片" : "返回首页灵感"\}[\s\S]*?setComposerPanel\(null\);[\s\S]*?setMobileComposerOpen\(false\);[\s\S]*?#mobile-composer-launcher/);
  assert.match(uiSource, /className="composer-mobile-draft-summary"[\s\S]*?返回编辑/);
  assert.match(uiSource, /id="composer-recipe-panel"[\s\S]*?内容类型[\s\S]*?生成模式/);
  assert.match(
    shellCss,
    /\.app-shell\.is-community-home\.is-mobile-composer-open > \.main-canvas\s*\{\s*display:\s*none;/,
  );
  assert.match(
    phoneRules,
    /\.community-composer\[data-mobile-open="true"\]\s*\{[^}]*height:\s*100%;[^}]*grid-template-rows:\s*64px minmax\(0, 1fr\) auto;/,
  );
  assert.match(phoneRules, /\.director-intent-well\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/);
  assert.match(phoneRules, /\.community-composer\[data-panel\]:not\(\[data-panel="closed"\]\) \.composer-prompt-section\s*\{\s*display:\s*none;/);
  assert.match(phoneRules, /\.composer-mobile-settings-row\s*\{[^}]*display:\s*grid;/);
  assert.match(phoneRules, /\.director-panel-triggers\s*\{\s*display:\s*none;/);
});

test("phone composer controls keep the 44px touch floor", () => {
  const phoneRules = composerCss.match(
    /@media \(max-width: 620px\) \{[\s\S]*?(?=\n@media \(max-width: 360px\))/,
  )?.[0] ?? "";

  for (const [selector, height] of [
    [".composer-mobile-launcher", "72px"],
    [".composer-mobile-edit-header > button", "44px"],
    [".composer-mobile-draft-summary", "62px"],
    [".director-reference-trigger", "48px"],
    [".director-choice-group button", "44px"],
    [".composer-mobile-settings-row > button", "48px"],
    [".director-model-trigger", "48px"],
    [":is(.community-composer, .workbench-generation-editor) .generate-button", "48px"],
  ]) {
    assert.match(
      phoneRules,
      new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[^}]*min-height:\\s*${height};`),
      `${selector} retains at least the 44px touch floor`,
    );
  }
  assert.doesNotMatch(uiSource, /director-count-control|setOutputCount\(Number\(event\.target\.value\)\)/);
});
