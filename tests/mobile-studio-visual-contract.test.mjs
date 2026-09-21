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
const editorSource = await readFile(new URL("../src/components/GenerationEditor.jsx", import.meta.url), "utf8");
const uiSource = `${appSource}\n${editorSource}`;
const workbenchStyles = await readFile(new URL("../src/design-system/workbenches.css", import.meta.url), "utf8");
const creationSource = await readFile(
  new URL("../src/CreationHub.jsx", import.meta.url),
  "utf8",
);
const workbenchSource = await readFile(
  new URL("../src/pages/studio/CreationWorkbenchViews.jsx", import.meta.url),
  "utf8",
);
const lineageSource = await readFile(
  new URL("../src/pages/studio/LineageGraphWorkspace.jsx", import.meta.url),
  "utf8",
);
const composerStyles = await readFile(
  new URL("../src/design-system/composer.css", import.meta.url),
  "utf8",
);
const generationEditorStyles = await readFile(
  new URL("../src/design-system/generation-editor.css", import.meta.url),
  "utf8",
);
const studioRoutes = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);
const artworksSource = await readFile(
  new URL("../src/pages/studio/ArtworksView.jsx", import.meta.url),
  "utf8",
);
const historySource = await readFile(
  new URL("../src/pages/studio/HistoryView.jsx", import.meta.url),
  "utf8",
);
const publishingSource = await readFile(
  new URL("../src/PublishingCenter.jsx", import.meta.url),
  "utf8",
);
const navSource = appSource.match(/const NAV_ITEMS = \[[\s\S]*?\n\];/)?.[0] ?? "";

test("mobile Studio layer parses and cannot style Company or Operations", () => {
  assert.doesNotThrow(() => parse(mobileStudio));
  assert.doesNotMatch(mobileStudio, /\.control-shell|\.ops-console/);
  assert.doesNotMatch(mobileStudio, /(?:linear|radial)-gradient\(/);
  assert.doesNotMatch(mobileStudio, /!important/);
  assert.doesNotMatch(mobileStudio, /font-size:\s*(?:8|9|10|11)px\s*;/);
});

test("phone chrome uses one contextual command bar and preserves an unnumbered extensible route rail", () => {
  assert.match(
    mobileStudio,
    /@media \(max-width: 720px\)[\s\S]*?--studio-mobile-nav-height:\s*48px;[\s\S]*?--shell-topbar-size:\s*56px;/,
  );
  assert.match(
    mobileStudio,
    /\.app-shell\[data-theme\]\.is-advanced-workbench\s*\{[^}]*--shell-topbar-size:\s*0px;[^}]*grid-template-rows:\s*minmax\(0, 1fr\);/,
  );
  assert.match(mobileStudio, /\.app-shell\[data-theme\]\.is-advanced-workbench > \.topbar,[\s\S]*?\.app-shell\[data-theme\]\.is-advanced-workbench > \.side-nav\s*\{\s*display:\s*none;/);
  assert.match(mobileStudio, /\.app-shell\[data-theme\]\.is-advanced-workbench > \.main-canvas\s*\{[^}]*grid-row:\s*1;/);
  assert.match(appSource, /className="studio-mobile-commandbar"/);
  assert.match(appSource, /className="studio-mobile-context" aria-label="当前创作上下文"/);
  assert.match(
    appSource,
    /<details[\s\S]*?className="studio-mobile-command-menu"[\s\S]*?<summary aria-label="打开工作台命令菜单" aria-haspopup="true">/,
  );
  assert.match(
    appSource,
    /className="studio-mobile-command-panel"[\s\S]*?>管理入口<[\s\S]*?className="studio-mobile-surface-list"[\s\S]*?企业管理[\s\S]*?平台运营/,
  );
  assert.doesNotMatch(appSource, />个人创作<\/button>|>企业创作<\/button>/);
  assert.match(appSource, /const canSwitchCompany = hasCompanySession && activeCompanyContexts\.length > 1;/);
  assert.match(appSource, /canSwitchCompany && \([\s\S]*?className="studio-mobile-select"[\s\S]*?activeCompanyContexts\.map/);
  assert.match(appSource, /canSwitchCompany && \([\s\S]*?className="project-select workspace-select"[\s\S]*?activeCompanyContexts\.map/);
  assert.doesNotMatch(appSource, /<option value="personal">个人空间<\/option>/);
  assert.match(appSource, /className="studio-mobile-select"[\s\S]*?<DemoAccountSwitcher[\s\S]*?<SkinSwitcher/);
  assert.match(appSource, /event\.key !== "Escape"[\s\S]*?querySelector\("summary"\)\?\.focus\(\)/);
  assert.match(
    mobileStudio,
    /> \.topbar > :not\(\.studio-mobile-commandbar\)\s*\{\s*display:\s*none;/,
  );
  assert.match(
    mobileStudio,
    /\.studio-mobile-commandbar\s*\{[^}]*grid-template-columns:\s*44px minmax\(0, 1fr\) 44px;/,
  );
  assert.match(
    mobileStudio,
    /\.studio-mobile-command-panel\s*\{[^}]*width:\s*min\(332px, calc\(100vw - 20px\)\);[^}]*max-height:\s*calc\(100dvh - 74px\);[^}]*overflow-y:\s*auto;/,
  );
  assert.match(
    mobileStudio,
    /> \.side-nav \.side-nav-track\s*\{[^}]*display:\s*flex;[^}]*overflow-x:\s*auto;[^}]*overscroll-behavior-inline:\s*contain;/,
  );
  assert.ok(navSource, "Studio navigation catalog remains explicit");
  assert.match(navSource, /首页[\s\S]*?创作[\s\S]*?素材[\s\S]*?作品[\s\S]*?发布[\s\S]*?历史/);
  assert.doesNotMatch(navSource, /number\s*:|index\s*:|label:\s*["'`]\d/);
  assert.match(appSource, /className="side-nav-primary"[\s\S]*?visibleStudioNavItems\.map/);
  assert.match(appSource, /className="side-nav-footer"/);
  assert.match(appSource, />设置<\/span>/);
  assert.match(appSource, /className="side-nav-scroll-forward"[\s\S]*?track\.scrollTo/);
  assert.match(
    mobileStudio,
    /> \.side-nav \.side-nav-track button\s*\{[^}]*min-width:\s*74px;[^}]*min-height:\s*44px;[^}]*border-radius:\s*10px;/,
  );
  assert.match(
    mobileStudio,
    /> \.side-nav \.side-nav-track button\.is-active\s*\{[^}]*color:\s*var\(--accent-strong\);[^}]*background:\s*var\(--accent-soft\);/,
  );
  assert.match(mobileStudio, /> \.side-nav \.side-nav-track button\.is-active::before\s*\{[^}]*content:\s*none;/);
});

test("advanced workbench phone gate keeps one local header", () => {
  assert.match(
    mobileStudio,
    /\.is-advanced-workbench > \.topbar,[\s\S]*?\.is-advanced-workbench > \.side-nav\s*\{\s*display:\s*none;/,
  );
  assert.match(workbenchStyles, /\.wb-desktop-gate-header\s*\{[^}]*min-height:\s*56px;/);
  assert.match(workbenchStyles, /\.wb-desktop-gate-header \.wb-back\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/);
});

test("320px quick composer context rail remains readable", () => {
  assert.match(
    generationEditorStyles,
    /@media \(max-width:\s*360px\)[\s\S]*?#composer-specs-trigger\s*\{[^}]*flex:\s*1 1 auto;[^}]*overflow:\s*hidden;[\s\S]*?#composer-readiness-trigger\s*\{[^}]*flex:\s*0 0 auto;/,
  );
  assert.match(editorSource, /className="readiness-label-prefix">生成<\/span>就绪/);
});

test("phone keeps all advanced workbench paths visible but mounts only a desktop-required gate", () => {
  assert.match(appSource, /const PHONE_ADVANCED_WORKBENCH_QUERY = "\(max-width: 720px\)"/);
  assert.match(
    appSource,
    /const phoneAdvancedWorkbenchGate = Boolean\([\s\S]*?advancedWorkbenchesDesktopOnly[\s\S]*?ADVANCED_WORKBENCHES\.includes\(creationWorkbench\)/,
  );
  assert.match(appSource, /workbench: phoneAdvancedWorkbenchGate \? "entry" : creationWorkbench/);
  assert.match(appSource, /advancedWorkbenchesDesktopOnly=\{advancedWorkbenchesDesktopOnly\}/);
  assert.match(creationSource, /const desktopGateActive = advancedWorkbenchesDesktopOnly && CREATION_WORKBENCHES\.has\(workbench\)/);
  assert.match(creationSource, /desktopGateActive \? \([\s\S]*?<DesktopWorkbenchGate kind=\{workbench\}/);
  assert.match(workbenchSource, /advancedWorkbenchesDesktopOnly && <span className="wb-desktop-badge">仅桌面端<\/span>/);
  assert.match(workbenchSource, /!advancedWorkbenchesDesktopOnly && workbenchController\?\.onTransfer/);
  assert.match(workbenchSource, /data-ui="desktop-workbench-gate"/);
  assert.match(workbenchSource, /手机端不会加载编辑器/);
  assert.match(workbenchStyles, /@media \(max-width: 720px\)[\s\S]*?\.wb-pathways \{ grid-template-columns: 1fr;[\s\S]*?\.wb-pathway > p \{ display: none;/);
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

test("creation keeps records progressive and reserves the global composer only for quick creation", () => {
  assert.match(creationSource, /const \[recordBrowserOpen, setRecordBrowserOpen\] = useState\(false\)/);
  assert.match(creationSource, /<CreationRecordBrowser[\s\S]*?open=\{recordBrowserOpen\}[\s\S]*?onOpenChange=\{setRecordBrowserOpen\}/);
  assert.match(workbenchSource, /<details[\s\S]*?className="creation-record-browser"[\s\S]*?<summary>[\s\S]*?任务与筛选/);
  assert.match(workbenchSource, /data-ui="creation-invitation-entry"/);
  assert.match(workbenchSource, /data-ui="director-console"/);
  assert.match(workbenchSource, /data-ui="director-notebook"/);
  assert.match(workbenchSource, /lazy\(\(\) => import\("\.\/LineageGraphWorkspace\.jsx"\)\)/);
  assert.match(lineageSource, /data-ui="lineage-canvas"/);
  assert.match(appSource, /const isQuickStudioView = isPrimaryStudioView && !isAdvancedWorkbench/);
  assert.match(appSource, /\{isQuickStudioView && renderGenerationEditor\(\)\}/);
  for (const variant of ["console", "notebook"]) {
    assert.match(workbenchSource, new RegExp(`renderEditor\\?\\.\\(\\{ variant: "${variant}"`));
  }
  assert.match(lineageSource, /renderEditor\(\{ variant: "canvas"/);
  assert.match(
    mobileStudio,
    /\.app-shell\[data-theme\]\.is-creation-hub \.creation-record-filter-head,[\s\S]*?overflow-x:\s*auto;[\s\S]*?overscroll-behavior-inline:\s*contain;/,
  );
  assert.match(
    composerStyles,
    /@media \(max-width: 900px\)[\s\S]*?\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*grid-column:\s*1;[^}]*margin:\s*0 12px 12px;/,
  );
  assert.doesNotMatch(
    mobileStudio,
    /\.is-creation-hub[^{}]*> \.community-composer[^{}]*\{[^}]*position:\s*fixed;/,
  );
});

test("composer becomes one focused phone creation and configuration surface", () => {
  assert.match(
    composerStyles,
    /\.community-composer\s*\{[^}]*position:\s*relative;[^}]*display:\s*grid;[^}]*grid-template-rows:\s*auto minmax\(0, auto\) auto;/s,
  );
  assert.match(
    composerStyles,
    /@media \(max-width: 620px\)[\s\S]*?\.community-composer\[data-mobile-open="false"\]\s*\{[^}]*height:\s*72px;[\s\S]*?\.community-composer\[data-mobile-open="true"\]\s*\{[^}]*height:\s*100%;/s,
  );
  assert.doesNotMatch(composerStyles, /\.app-shell\.is-community-home > \.community-composer\s*\{[^}]*position:\s*fixed;/s);
  assert.match(uiSource, /data-composer-panel-trigger="specs"[\s\S]*?aria-expanded=\{composerPanel === "specs"\}/);
  assert.match(uiSource, /data-composer-panel-trigger="model"[\s\S]*?aria-expanded=\{composerPanel === "model"\}/);
  assert.match(uiSource, /id="model"/);
  assert.match(uiSource, /id="generation-mode"/);
  assert.match(uiSource, /\{isQuick && <>\s*<button\s*id="mobile-composer-launcher"/);
  assert.match(uiSource, /className="composer-mobile-settings-row"/);
});

test("phone collections keep one continuation action and publishing collapses in reading order", () => {
  assert.match(
    artworksSource,
    /className="is-primary"[\s\S]*?继续创作[\s\S]*?className="download-button"[\s\S]*?去发布[\s\S]*?className="artwork-more-actions"[\s\S]*?更多/,
  );
  assert.match(historySource, /className=\{`task-row-detail[\s\S]*?\{attentionDetail \|\| artifactSummary\}/);
  assert.match(
    publishingSource,
    /publication-handoff-index[\s\S]*?发布前要求[\s\S]*?className="publication-jobs"[\s\S]*?发布安排[\s\S]*?className="publication-connections"[\s\S]*?发布账号/,
  );
  assert.match(
    studioRoutes,
    /@media \(max-width: 980px\)[\s\S]*?\.publication-layout\s*\{[^}]*grid-template-columns:\s*1fr;[^}]*grid-template-areas:\s*"requirements"\s*"jobs"\s*"connections";/,
  );
  assert.match(
    studioRoutes,
    /@media \(max-width: 760px\)[\s\S]*?\.publication-handoff-index \.publication-job-list\s*\{[^}]*grid-template-columns:\s*1fr;/,
  );
});

test("short phones retain the focused editor header and primary generation action", () => {
  assert.match(
    composerStyles,
    /@media \(max-width: 360px\)[\s\S]*?\.composer-mobile-edit-header\s*\{[^}]*grid-template-columns:\s*44px minmax\(0, 1fr\) 116px;/,
  );
  assert.match(
    composerStyles,
    /@media \(max-width: 360px\)[\s\S]*?:is\(\.community-composer,\s*\.workbench-generation-editor\) \.generate-button\s*\{[^}]*min-width:\s*120px;/,
  );
  assert.match(composerStyles, /@media \(max-width: 360px\)[\s\S]*?\.generate-button-mobile-cost\s*\{[^}]*display:\s*block;/);
  assert.doesNotMatch(composerStyles, /@media \(max-width: 360px\)[\s\S]*?\.inspector-actions[^}]*display:\s*none;/);
});

test("mobile Studio controls retain the 44px touch and 12px type floors", () => {
  const touchDeclarations = mobileStudio.match(/min-height:\s*44px;/g) || [];
  assert.ok(touchDeclarations.length >= 5, "all grouped mobile control families keep 44px targets");
  assert.match(mobileStudio, /\.studio-mobile-command-menu > summary\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px;/);
  assert.match(mobileStudio, /\.studio-mobile-surface-list button\s*\{[^}]*min-height:\s*44px;/);
  assert.match(mobileStudio, /\.studio-mobile-surface-list\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit, minmax\(96px, 1fr\)\)/);
  assert.match(mobileStudio, /\.studio-mobile-select select\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*min-height:\s*44px;/);
  assert.match(mobileStudio, /\.studio-mobile-command-panel \.demo-account-switcher select\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/);
  assert.match(mobileStudio, /> \.topbar \.studio-mobile-command-panel \.skin-switcher-trigger\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*min-height:\s*44px;/);
  assert.match(composerStyles, /@media \(max-width: 620px\)[\s\S]*?\.composer-mobile-edit-header > button\s*\{[^}]*min-height:\s*44px;/);
  assert.match(composerStyles, /@media \(max-width: 620px\)[\s\S]*?\.composer-mobile-settings-row > button\s*\{[^}]*min-height:\s*48px;/);
  assert.match(
    mobileStudio,
    /\.app-shell\[data-theme\] \.creation-record-browser button,[\s\S]*?\.app-shell\[data-theme\] \.secondary-view button,[\s\S]*?\.app-shell\[data-theme\] \.taskbar button\s*\{\s*min-height:\s*44px;/,
  );
  assert.match(workbenchStyles, /\.wb-desktop-gate-header \.wb-back\s*\{[^}]*min-width:\s*44px;[^}]*min-height:\s*44px;/);
  assert.match(workbenchStyles, /\.wb-desktop-gate-body \.wb-primary\s*\{[^}]*min-height:\s*48px;/);
  assert.doesNotMatch(mobileStudio, /font(?:-size)?\s*:[^;\n]*\b(?:[0-9]|1[01])px\b/);
  assert.doesNotMatch(workbenchStyles, /font(?:-size)?\s*:[^;\n]*\b(?:[0-9]|1[01])px\b/);
});
