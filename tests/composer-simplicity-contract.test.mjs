import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "src", "App.jsx"), "utf8");
const editorSource = fs.readFileSync(path.join(root, "src", "components", "GenerationEditor.jsx"), "utf8");
const uiSource = `${appSource}\n${editorSource}`;
const composerCss = fs.readFileSync(
  path.join(root, "src", "design-system", "composer.css"),
  "utf8",
);
const shellCss = fs.readFileSync(
  path.join(root, "src", "design-system", "shells.css"),
  "utf8",
);
const mobileCss = fs.readFileSync(
  path.join(root, "src", "design-system", "mobile-studio.css"),
  "utf8",
);
const tokenCss = fs.readFileSync(
  path.join(root, "src", "design-system", "tokens.css"),
  "utf8",
);

const mediaGroupSource = appSource.match(
  /function MediaInputGroup\([\s\S]*?function MediaLibrary/,
)?.[0] ?? "";

test("media capacity uses one real add trigger without removing upload behavior", () => {
  assert.ok(mediaGroupSource);
  assert.doesNotMatch(mediaGroupSource, /emptySlots/);
  assert.doesNotMatch(mediaGroupSource, /Array\.from\(\{\s*length:/);
  assert.match(mediaGroupSource, /displayedFiles\.map\(\(file\)\s*=>/);
  assert.match(mediaGroupSource, /onClick=\{\(\)\s*=>\s*onRemove\(file\)\}/);
  assert.match(mediaGroupSource, /files\.length\s*<\s*limit/);
  assert.match(mediaGroupSource, /inputRef\.current\?\.click\(\)/);
  assert.match(mediaGroupSource, /multiple=\{limit\s*>\s*1\}/);
  assert.match(mediaGroupSource, /uploadDisabled/);
  assert.match(mediaGroupSource, /className="media-slot media-slot-add"/);
  assert.match(mediaGroupSource, /files\.length\s*>\s*0\s*\?\s*"继续添加"\s*:\s*"添加"/);
});

test("contextual Director Deck retains every capability field without a settings grid", () => {
  assert.match(appSource, /const \[composerPanel, setComposerPanel\] = useState\(null\)/);
  assert.match(uiSource, /composerPanel === "recipe"/);
  assert.match(uiSource, /composerPanel === "references"/);
  assert.match(uiSource, /composerPanel === "specs"/);
  assert.match(uiSource, /composerPanel === "model"/);
  assert.match(uiSource, /composerPanel === "readiness"/);
  assert.match(uiSource, /id="model"/);
  assert.match(uiSource, /id="generation-mode"/);
  assert.match(uiSource, /selectStudioModel\(item\.id\)/);
  assert.match(uiSource, /selectGenerationMode\(item\)/);
  assert.match(uiSource, /label="参考图"/);
  assert.match(uiSource, /label="参考视频"/);
  assert.match(uiSource, /label="参考音频"/);
  assert.match(uiSource, /setRatio\(item\)/);
  assert.match(uiSource, /setResolution\(item\)/);
  assert.match(uiSource, /setDuration\(item\)/);
  assert.match(
    uiSource,
    /activeCapability\.limits\.outputCounts\.map\(\(item\) => \([\s\S]*?onClick=\{\(\) => setOutputCount\(item\)\}>\{item\} 个<\/button>/,
  );
  assert.doesNotMatch(uiSource, /director-count-control|setOutputCount\(Number\(event\.target\.value\)\)/);
  assert.match(uiSource, /onClick=\{startGeneration\}/);
});

test("the director composer keeps desktop bands and adapts phones into one focused creation surface", () => {
  assert.match(
    uiSource,
    /<header className="community-composer-header">[\s\S]*?className="composer-mode-rail"[\s\S]*?<\/header>/,
    "generation mode tabs remain in the persistent composer header",
  );
  assert.match(composerCss, /\.composer-mode-rail\s*\{[^}]*display:\s*flex;/);
  assert.match(composerCss, /\.director-intent-well\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto;/);
  assert.match(composerCss, /\.director-context-panel\s*\{[^}]*display:\s*grid;[^}]*background:\s*var\(--canvas\);/);
  assert.match(mediaGroupSource, /className="media-input-group" data-kind=\{kind\}/);
  assert.match(composerCss, /\.media-groups\s*\{[^}]*display:\s*flex;/);
  assert.match(composerCss, /\.media-input-group\[data-kind="image"\]\s*\{[^}]*flex-grow:\s*1\.28;/);
  assert.match(composerCss, /\.director-spec-groups\s*\{[^}]*display:\s*flex;/);
  assert.match(uiSource, /className="director-choice-group is-ratio"/);
  assert.match(uiSource, /className="director-choice-group is-duration"/);
  assert.doesNotMatch(composerCss, /\.media-slot\s*\{[^}]*border:\s*1px dashed/s);
  assert.doesNotMatch(composerCss, /\.director-spec-groups\s*\{[^}]*border-block:/s);
  assert.doesNotMatch(composerCss, /\.director-choice-group\s*\{[^}]*border-right:/s);
  assert.match(composerCss, /\.director-choice-group button\.is-active\s*\{[^}]*background:\s*var\(--surface\);[^}]*box-shadow:/s);
  assert.match(composerCss, /\.director-model-list\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/);
  assert.match(composerCss, /\.director-execution-row\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) auto;/);
  assert.match(composerCss, /@media \(max-width: 620px\)[\s\S]*?\.community-composer\[data-mobile-open="false"\]\s*\{[^}]*height:\s*72px;/);
  assert.match(composerCss, /@media \(max-width: 620px\)[\s\S]*?\.community-composer\[data-mobile-open="true"\]\s*\{[^}]*height:\s*100%;/);
  assert.match(composerCss, /@media \(max-width: 620px\)[\s\S]*?\.director-intent-well\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/);
  assert.match(composerCss, /@media \(max-width: 620px\)[\s\S]*?\.director-spec-groups\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/);
  assert.match(composerCss, /@media \(max-width: 620px\)[\s\S]*?\.composer-mobile-settings-row\s*\{[^}]*display:\s*grid;/);
  assert.match(composerCss, /@media \(max-width: 620px\)[\s\S]*?\.director-panel-triggers\s*\{\s*display:\s*none;/);
  assert.match(mobileCss, /min-height:\s*44px/);
});

test("primary composer and secondary live task state each participate in shell layout", () => {
  assert.match(
    shellCss,
    /\.app-shell\.is-community-home\s*\{[\s\S]*?grid-template-rows:\s*var\(--shell-topbar-size\)\s+minmax\(0,\s*1fr\)\s+auto/,
  );
  assert.match(
    composerCss,
    /\.community-composer\s*\{[^}]*position:\s*relative;[^}]*display:\s*grid;[^}]*overflow:\s*hidden;/,
  );
  assert.match(
    shellCss,
    /\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*3;/,
  );
  assert.match(
    shellCss,
    /\.app-shell\.is-secondary-page > \.taskbar\s*\{[^}]*grid-column:\s*2;[^}]*grid-row:\s*3;/,
  );
  assert.match(
    appSource,
    /\{!isPrimaryStudioView && \(taskStageIsActive \|\| taskStageNeedsAttention\) && <footer/,
  );
  assert.match(
    shellCss,
    /@media \(max-width: 900px\)[\s\S]*?\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*4;/,
  );
  assert.match(
    shellCss,
    /@media \(max-width: 900px\)[\s\S]*?\.app-shell\.is-secondary-page > \.taskbar\s*\{[^}]*grid-column:\s*1;[^}]*grid-row:\s*4;/,
  );
});

test("composer exposes one prioritized submit status and only valid prices", () => {
  assert.match(appSource, /const composerSubmitState = \(\(\) => \{/);
  assert.match(
    appSource,
    /if \(LIVE_MODE && !canCreateTasks\)[\s\S]*?code: "permission_denied"[\s\S]*?if \(modelsLoading\)[\s\S]*?if \(historicalRequestLocked\)[\s\S]*?if \(models\.length === 0 \|\| !model\.id\)[\s\S]*?if \(!activeCapability\)[\s\S]*?if \(!currentQuoteRevisionValid[\s\S]*?if \(readinessBlocking\)/,
  );
  assert.match(uiSource, /disabled=\{composerSubmitState\.disabled\}/);
  assert.match(uiSource, /aria-describedby=\{composerSubmitMessageId\}/);
  assert.equal(uiSource.match(/id="composer-submit-message"/g)?.length, 1);
  assert.match(uiSource, /className=\{`composer-submit-status is-\$\{composerSubmitState\.tone\}`\}/);
  assert.doesNotMatch(
    uiSource,
    /<div className="inspector-actions director-execution-spine">[\s\S]*?className="permission-note"[\s\S]*?<div className="director-execution-row">/,
  );
  assert.match(appSource, /const showCostPreview = !historicalRequestLocked[\s\S]*?costPreview\.available[\s\S]*?currentQuoteRevisionValid/);
  assert.match(uiSource, /\{showCostPreview && \([\s\S]*?<div className="cost-row">/);
  assert.match(uiSource, /\{candidatePrice && <span className="director-model-price">\{candidatePrice\}<\/span>\}/);
  assert.doesNotMatch(uiSource, /<strong>\{costPreview\.label\}<\/strong>/);
  assert.match(composerCss, /\.composer-submit-status\s*\{[^}]*justify-self:\s*end;/s);
});

test("composer keeps violet state, orange generation and the visual debt floor", () => {
  assert.match(tokenCss, /--selection-violet:\s*#5a55d2;/);
  assert.match(tokenCss, /--selection-violet-strong:\s*#4a45c2;/);
  assert.match(tokenCss, /--selection-violet-soft:\s*#eeedfb;/);
  assert.match(tokenCss, /--workflow-orange:\s*#ce360a;/);
  assert.match(tokenCss, /--accent:\s*var\(--selection-violet\);/);
  assert.match(tokenCss, /--signal:\s*var\(--workflow-orange\);/);
  assert.match(tokenCss, /--radius-control:\s*0\.625rem;/);
  assert.match(tokenCss, /--radius-md:\s*0\.75rem;/);
  assert.match(tokenCss, /--radius-lg:\s*1rem;/);
  assert.match(
    composerCss,
    /:is\(\.community-composer,\s*\.workbench-generation-editor\) \.generate-button\s*\{[^}]*border:\s*0;[^}]*border-radius:\s*10px;[^}]*background:\s*var\(--signal\);/,
  );
  for (const [name, source] of Object.entries({ composerCss, shellCss, mobileCss })) {
    assert.doesNotMatch(source, /(?:linear|radial)-gradient\(|!important/, `${name} stays debt-free`);
    assert.doesNotMatch(
      source,
      /font(?:-size)?\s*:[^;\n]*\b(?:[0-9]|1[01])px\b/,
      `${name} keeps visible copy at 12px or larger`,
    );
  }
});
