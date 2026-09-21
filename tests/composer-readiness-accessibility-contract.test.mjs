import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


const appSource = await readFile(
  new URL("../src/App.jsx", import.meta.url),
  "utf8",
);
const editorSource = await readFile(
  new URL("../src/components/GenerationEditor.jsx", import.meta.url),
  "utf8",
);
const uiSource = `${appSource}\n${editorSource}`;
const composerStyles = await readFile(
  new URL("../src/design-system/composer.css", import.meta.url),
  "utf8",
);


test("generation readiness replaces the former raw entitlement inspector", () => {
  assert.match(uiSource, /composerPanel === "readiness"/);
  assert.match(uiSource, /id="composer-readiness-panel"/);
  assert.match(uiSource, /id="composer-readiness-title">生成就绪</);
  assert.match(
    uiSource,
    /data-composer-panel-trigger="readiness"[\s\S]*?data-readiness=\{generationReadiness\.status\}[\s\S]*?aria-controls="composer-readiness-panel"/,
  );
  assert.match(
    uiSource,
    /<dl[\s\S]*?className=\{`director-readiness-evidence is-\$\{generationReadiness\.status\}`\}[\s\S]*?data-composer-panel-focus/,
  );
  assert.match(uiSource, /<ul className="director-readiness-blockers" aria-label="其他待处理项">/);
  assert.match(uiSource, /<time dateTime=\{model\.readinessCheckedAt\}>\{readinessCheckedAtLabel\}<\/time>/);
  assert.doesNotMatch(uiSource, /director-readiness-state/);
  assert.doesNotMatch(uiSource, /composerPanel === "expert"/);
  assert.doesNotMatch(uiSource, /id="composer-expert-panel"/);
  assert.doesNotMatch(uiSource, />能力与授权</);
  assert.doesNotMatch(uiSource, /所需授权：\{activeCapability\.requiredResourceKeys\.join/);
});


test("all mutually exclusive composer panels receive focus and restore their trigger", () => {
  assert.match(
    appSource,
    /useLayoutEffect\(\(\) => \{[\s\S]*?if \(composerPanel\) \{[\s\S]*?querySelector\(`#composer-\$\{composerPanel\}-panel \[data-composer-panel-focus\]`\)[\s\S]*?\.focus\(\{ preventScroll: true \}\);[\s\S]*?\}, \[composerPanel\]\);/,
  );
  assert.match(
    appSource,
    /const closeComposerPanel = \(panel, \{ restoreFocus = true \} = \{\}\) => \{[\s\S]*?restoreComposerPanelFocusRef\.current = restoreFocus;[\s\S]*?setComposerPanel\(null\);/,
  );
  assert.match(
    appSource,
    /if \(restoreComposerPanelFocusRef\.current\) \{[\s\S]*?composerPanelTriggerRef\.current[\s\S]*?\.focus\(\{ preventScroll: true \}\)/,
  );
  assert.match(
    appSource,
    /const toggleComposerPanelFromTrigger = \(event, nextPanel\) => \{[\s\S]*?focusPanel: event\.detail === 0/,
    "pointer disclosure keeps focus on its trigger while keyboard disclosure enters the panel",
  );
  assert.match(
    appSource,
    /if \(focusComposerPanelOnOpenRef\.current\) \{[\s\S]*?data-composer-panel-focus[\s\S]*?\.focus\(\{ preventScroll: true \}\)/,
    "keyboard and programmatic disclosures retain an explicit panel focus target",
  );
  assert.match(
    appSource,
    /if \(event\.key !== "Escape"\) return;[\s\S]*?if \(composerPanel\) \{[\s\S]*?closeComposerPanel\(composerPanel\);[\s\S]*?return;[\s\S]*?if \(!mobileComposerOpen\) return;[\s\S]*?setMobileComposerOpen\(false\);[\s\S]*?#mobile-composer-launcher/,
  );
  for (const panel of ["recipe", "references", "specs", "model", "readiness"]) {
    assert.match(
      uiSource,
      new RegExp(`id="composer-${panel}-panel"[\\s\\S]*?data-composer-panel-focus`),
      `${panel} panel has a programmatic initial focus target`,
    );
    assert.match(
      uiSource,
      new RegExp(`onClick=\\{\\(\\) => closeComposerPanel\\("${panel}"\\)\\}`),
      `${panel} close control restores its disclosure trigger`,
    );
  }
  assert.match(
    uiSource,
    /selectStudioModel\(item\.id\);[\s\S]*?closeComposerPanel\("model"\);/,
    "selecting a model closes the panel through the focus-restoring path",
  );
});


test("readiness is fail-closed at both submission and primary-action boundaries", () => {
  assert.match(
    appSource,
    /const serverGenerationReadiness = hasServerReadinessEvidence[\s\S]*?resolveGenerationReadiness\(/,
  );
  assert.match(
    appSource,
    /const localReadinessBlockers = historicalRequestLocked[\s\S]*?prompt_required[\s\S]*?prompt_too_long[\s\S]*?image_required[\s\S]*?图生视频至少需要 1 张参考图。[\s\S]*?video_required[\s\S]*?视频参考至少需要 1 个参考视频。[\s\S]*?upload_in_progress/,
    "the visible readiness state narrows server evidence with current draft completeness",
  );
  assert.match(
    appSource,
    /const generationReadiness = localReadinessBlockers\.length > 0[\s\S]*?ready:\s*false,[\s\S]*?blockers:\s*\[[\s\S]*?\.\.\.serverGenerationReadiness\.blockers,[\s\S]*?\.\.\.localReadinessBlockers/,
  );
  assert.match(
    appSource,
    /const readinessBlocking = !historicalRequestLocked && !generationReadiness\.ready;/,
  );
  assert.match(
    appSource,
    /if \(!storedPending && readinessBlocking\) \{[\s\S]*?setFormError\(/,
  );
  assert.match(
    appSource,
    /if \(readinessBlocking\) \{[\s\S]*?source: "readiness"[\s\S]*?disabled: true/,
  );
  assert.match(
    uiSource,
    /disabled=\{composerSubmitState\.disabled\}/,
    "the visible generate action cannot remain clickable while readiness is blocked or unverified",
  );
  assert.match(appSource, /生成条件尚未确认，请刷新模型后重试。/);
});


test("the retired live shots branch stays unreachable by construction", () => {
  assert.match(appSource, /if \(activeNav === "shots"\) \{[\s\S]*?<CommunityHome/);
  assert.doesNotMatch(appSource, /if \(LIVE_MODE && activeNav === "shots"\)/);
  assert.doesNotMatch(appSource, /className="live-editor-view"/);
});


test("1056 by 640 keeps an in-flow execution spine below an internally scrollable panel", () => {
  assert.match(
    composerStyles,
    /\.community-composer\.is-expanded\s*\{[^}]*max-height:\s*min\(65dvh, 540px\);/,
  );
  assert.match(
    composerStyles,
    /\.community-composer\s*\{[^}]*position:\s*relative;[^}]*grid-template-rows:\s*auto minmax\(0, auto\) auto;[^}]*overflow:\s*hidden;/s,
  );
  assert.match(
    composerStyles,
    /:is\(\.community-composer,\s*\.workbench-generation-editor\) \.inspector-scroll\s*\{[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;[^}]*overscroll-behavior:\s*contain;/s,
  );
  assert.match(
    composerStyles,
    /\.director-execution-spine\s*\{[^}]*display:\s*grid;[^}]*padding:\s*var\(--space-2\) var\(--space-4\);[^}]*border-top:\s*0;/s,
  );
  assert.doesNotMatch(
    composerStyles,
    /\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*position:\s*fixed;/s,
  );
});


test("390 and 320 preserve the watch create and configure focus states", () => {
  const phoneRules = composerStyles.match(
    /@media \(max-width: 620px\) \{[\s\S]*?(?=\n@media \(max-width: 360px\))/,
  )?.[0] ?? "";
  const narrowRules = composerStyles.match(
    /@media \(max-width: 360px\) \{[\s\S]*?\n\}/,
  )?.[0] ?? "";

  assert.match(phoneRules, /\.community-composer\[data-mobile-open="false"\]\s*\{[^}]*height:\s*72px;[^}]*grid-template-rows:\s*72px;/);
  assert.match(phoneRules, /\.community-composer\[data-mobile-open="true"\]\s*\{[^}]*height:\s*100%;[^}]*grid-template-rows:\s*64px minmax\(0, 1fr\) auto;/);
  assert.match(phoneRules, /\.community-composer\[data-mobile-open="false"\] > :not\(\.composer-mobile-launcher\)\s*\{\s*display:\s*none;/);
  assert.match(phoneRules, /\.composer-mobile-edit-header > button\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/);
  assert.match(phoneRules, /\.director-intent-well\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/);
  assert.match(phoneRules, /\.community-composer\[data-panel\]:not\(\[data-panel="closed"\]\) \.composer-prompt-section\s*\{\s*display:\s*none;/);
  assert.match(phoneRules, /\.composer-mobile-settings-row > button\s*\{[^}]*min-height:\s*48px;/);
  assert.match(phoneRules, /:is\(\.community-composer,\s*\.workbench-generation-editor\) \.generate-button\s*\{[^}]*min-width:\s*122px;[^}]*min-height:\s*48px;/);
  assert.match(phoneRules, /\.director-readiness-evidence\s*\{[^}]*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/);
  assert.match(narrowRules, /\.composer-mobile-edit-header\s*\{[^}]*grid-template-columns:\s*44px minmax\(0, 1fr\) 116px;/);
  assert.match(narrowRules, /:is\(\.community-composer,\s*\.workbench-generation-editor\) \.generate-button\s*\{[^}]*min-width:\s*120px;/);
  assert.match(narrowRules, /:is\(\.community-composer,\s*\.workbench-generation-editor\) \.cost-row\s*\{\s*display:\s*none;/);
  assert.match(narrowRules, /\.generate-button-mobile-cost\s*\{[^}]*display:\s*block;/);
  assert.doesNotMatch(phoneRules + narrowRules, /position:\s*fixed/);
  assert.doesNotMatch(phoneRules + narrowRules, /\.inspector-actions[^}]*display:\s*none;/);
});
