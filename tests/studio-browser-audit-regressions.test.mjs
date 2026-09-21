import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const editorSource = await readFile(new URL("../src/components/GenerationEditor.jsx", import.meta.url), "utf8");
const uiSource = `${appSource}\n${editorSource}`;
const identitySource = await readFile(
  new URL("../src/app/useStudioIdentity.js", import.meta.url),
  "utf8",
);
const accountSource = await readFile(new URL("../src/AccountCenter.jsx", import.meta.url), "utf8");
const artworksSource = await readFile(new URL("../src/pages/studio/ArtworksView.jsx", import.meta.url), "utf8");
const historySource = await readFile(new URL("../src/pages/studio/HistoryView.jsx", import.meta.url), "utf8");
const studioRoutes = await readFile(new URL("../src/design-system/studio-routes.css", import.meta.url), "utf8");
const composerStyles = await readFile(new URL("../src/design-system/composer.css", import.meta.url), "utf8");
const shellStyles = await readFile(new URL("../src/design-system/shells.css", import.meta.url), "utf8");
const chromeStyles = await readFile(new URL("../src/design-system/chrome.css", import.meta.url), "utf8");
const brandingStyles = await readFile(new URL("../src/design-system/branding.css", import.meta.url), "utf8");

test("the Studio shell is one continuous paper plane without crossing divider rules", () => {
  assert.match(chromeStyles, /\.app-shell > \.topbar\s*\{[^}]*border-bottom:\s*0;[^}]*background:\s*var\(--chrome-surface\);/s);
  assert.match(chromeStyles, /\.app-shell \.brand\s*\{[^}]*border-right:\s*0;/s);
  assert.match(chromeStyles, /\.app-shell > \.side-nav\s*\{[^}]*border-right:\s*0;[^}]*background:\s*var\(--surface\);/s);
  assert.match(chromeStyles, /\.app-shell \.side-nav-footer\s*\{[^}]*border-top:\s*0;/s);
  assert.match(chromeStyles, /@media \(max-width: 900px\)[\s\S]*?\.app-shell > \.side-nav\s*\{[^}]*border-bottom:\s*0;/s);
  assert.match(chromeStyles, /> \.side-nav > \.side-nav-scroll-forward\s*\{[^}]*border-left:\s*0;/s);
  const studioBrandRule = brandingStyles.match(/\.app-shell\[data-theme\] > \.topbar \.brand\s*\{[^}]*\}/s);
  assert.ok(studioBrandRule, "the Studio brand rule remains explicit");
  assert.doesNotMatch(studioBrandRule[0], /border-inline-end:/);
});

test("desktop command controls keep independent hit regions before mobile chrome takes over", () => {
  assert.match(
    chromeStyles,
    /\.topbar-command-cluster\s*\{[^}]*flex:\s*0 0 auto;[^}]*gap:\s*10px;/s,
  );
  assert.match(
    chromeStyles,
    /\.topbar-command-cluster > \.surface-switch\s*\{\s*flex:\s*0 0 auto;\s*\}/,
  );
  assert.match(
    chromeStyles,
    /@media \(max-width: 1240px\)[\s\S]*?\.app-shell \.project-select\s*\{[^}]*min-width:\s*132px;[^}]*flex-basis:\s*132px;/s,
  );
});

test("company-only switching invalidates stale evidence without restoring a personal context", () => {
  assert.match(identitySource, /const accountKind = sessionAccountKind\(catalog\)/);
  assert.match(identitySource, /accountKind === "company" && selectedCompanyClient[\s\S]*?getCompanyMe/);
  assert.match(identitySource, /accountKind === "platform"[\s\S]*?getPlatformAdminMe/);
  assert.match(identitySource, /accountKind === "personal"[\s\S]*?getPersonalMe/);
  assert.match(appSource, /const canSwitchCompany = hasCompanySession && activeCompanyContexts\.length > 1;/);
  assert.match(appSource, /const studioWorkspaceEvidenceKeyRef = useRef\(""\)/);
  assert.match(appSource, /studioWorkspaceEvidenceKeyRef\.current = studioWorkspaceKey/);
  assert.match(
    appSource,
    /nextWorkspaceKey !== studioWorkspaceEvidenceKeyRef\.current[\s\S]*?invalidateStudioWorkspaceEvidence\(\)/,
  );
  assert.match(appSource, /submitting \|\| cancelling \|\| uploadingKind/);
  assert.match(appSource, /changeCompanyContext\(companyId, \{ targetSurface: "company" \}\)/);
  assert.match(appSource, /item\.company_id === nextCompanyId && isActiveCompanyContext\(item\)/);
  assert.match(identitySource, /setIdentityResolved\(false\)[\s\S]*?setCompanyIdentity\(null\)/);
  assert.match(
    appSource,
    /const studioWorkspaceKey = isPersonalWorkspace[\s\S]*?: activeCompanyId \|\| sessionIdentity\?\.company_id/,
  );
  assert.doesNotMatch(appSource, /<option value="personal">个人空间<\/option>/);
});

test("the media library search is one unified icon and input control", () => {
  assert.match(appSource, /className="search-field media-library-search"[\s\S]*?<MagnifyingGlass[\s\S]*?<input/);
  assert.match(studioRoutes, /\.media-library-search\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*38px minmax\(0, 1fr\);[^}]*border:\s*0;[^}]*border-radius:\s*999px;/s);
  assert.match(studioRoutes, /\.app-shell\[data-theme\] \.media-library-search input\s*\{[^}]*width:\s*100%;[^}]*border:\s*0;/s);
});

test("account controls keep the default desktop and touch-size mobile contracts", () => {
  assert.match(accountSource, /className="account-skin-switcher"/);
  assert.match(studioRoutes, /\.account-center :is\([\s\S]*?\.account-profile-form input,[\s\S]*?min-height:\s*var\(--control-size-default\);/);
  assert.match(studioRoutes, /\.account-center :is\([\s\S]*?\.account-primary-button,[\s\S]*?min-height:\s*var\(--control-size-default\);/);
  assert.match(studioRoutes, /@media \(max-width: 560px\)[\s\S]*?\.account-skin-switcher\s*\{[^}]*width:\s*100%;[^}]*min-height:\s*var\(--control-size-touch\);/s);
  assert.match(studioRoutes, /\.app-shell\[data-theme\] \.account-skin-switcher \.skin-switcher-trigger\s*\{[^}]*width:\s*100%;[^}]*min-width:\s*0;[^}]*min-height:\s*var\(--control-size-touch\);/s);
});

test("artwork filters and actions keep one clear continuation path", () => {
  assert.match(artworksSource, /<div className="history-toolbar artwork-toolbar" aria-label="作品筛选">/);
  assert.match(artworksSource, /artwork-toolbar-count[\s\S]*?件作品/);
  const actionStart = artworksSource.indexOf('<footer className="artwork-action-row">');
  const actionEnd = artworksSource.indexOf("</footer>", actionStart);
  const artworkActions = artworksSource.slice(actionStart, actionEnd);
  assert.notEqual(actionStart, -1, "作品操作区必须存在");
  assert.equal((artworkActions.match(/className="is-primary"/g) || []).length, 1);
  assert.match(artworkActions, /className="is-primary"[\s\S]*?onClick=\{\(\) => onAdjust\?\.\(artwork\)\}[\s\S]*?继续创作/);
  assert.match(artworkActions, /className="download-button"[\s\S]*?下载作品/);
  assert.match(artworkActions, /className="text-button"[\s\S]*?去发布/);
  assert.match(artworkActions, /className="artwork-more-actions"[\s\S]*?<summary>更多<\/summary>[\s\S]*?查看任务[\s\S]*?存入素材库/);
  assert.doesNotMatch(artworkActions, /复用设置|打开并调整/);
  assert.match(studioRoutes, /\.artwork-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\);/s);
  assert.match(studioRoutes, /\.artwork-item\.is-featured\s*\{[^}]*grid-column:\s*1 \/ -1;[^}]*grid-template-columns:/s);
  assert.match(studioRoutes, /\.artwork-action-row > button\.is-primary\s*\{[^}]*background:\s*var\(--signal\);/s);
  assert.match(studioRoutes, /\.artwork-more-actions > div\s*\{[^}]*position:\s*absolute;[^}]*min-width:\s*150px;/s);
  assert.match(studioRoutes, /@media \(max-width: 760px\)[\s\S]*?\.artwork-grid\s*\{[^}]*grid-template-columns:\s*1fr;/s);
});

test("history exposes exceptional task detail in the ledger row", () => {
  assert.match(
    historySource,
    /const attentionDetail = \["failed", "timed_out", "reconciliation_required", "unknown"\]\.includes\(statusDefinition\.status\)[\s\S]*?studioErrorMessage\(task\.failure_reason, statusDefinition\.detail\)/,
  );
  assert.match(
    historySource,
    /className=\{`task-row-detail \$\{attentionDetail \? `is-\$\{statusDefinition\.tone\}` : ""\}`\}[\s\S]*?\{attentionDetail \|\| artifactSummary\}/,
  );
  assert.match(studioRoutes, /\.task-row-detail\s*\{[^}]*flex:\s*1 1 260px;[^}]*line-height:\s*1\.5;/s);
  assert.match(studioRoutes, /\.task-row-detail\.is-danger\s*\{[^}]*color:\s*var\(--danger\);/s);
});

test("material names can shrink and wrap without widening the document", () => {
  assert.match(studioRoutes, /\.asset-item\s*\{[^}]*padding:\s*0;[^}]*text-align:\s*left;/s);
  assert.match(
    studioRoutes,
    /\.asset-item > span\s*\{[^}]*min-width:\s*0;[^}]*overflow-wrap:\s*anywhere;[^}]*word-break:\s*break-word;/s,
  );
});

test("the material heading and task rail stretch to the shared phone gutter", () => {
  assert.match(
    studioRoutes,
    /@media \(max-width: 760px\)[\s\S]*?\.secondary-heading,[\s\S]*?\.publication-heading\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);[^}]*justify-content:\s*stretch;/s,
  );
  assert.match(
    studioRoutes,
    /@media \(max-width: 760px\)[\s\S]*?\.media-library-heading-actions\s*\{[^}]*width:\s*100%;[^}]*justify-self:\s*stretch;/s,
  );
  assert.match(
    studioRoutes,
    /@media \(max-width: 560px\)[\s\S]*?\.taskbar\s*\{[^}]*width:\s*auto;[^}]*justify-self:\s*stretch;[^}]*margin-inline:\s*var\(--shell-page-gutter\);/s,
  );
});

test("the extreme-phone composer separates watching, creating, and configuring", () => {
  assert.match(uiSource, /composer-kind-label[\s\S]*?导演控制台/);
  assert.match(uiSource, /id="composer-references-panel"[\s\S]*?参考素材[\s\S]*?支持多选和逐项移除/);
  assert.match(composerStyles, /:is\(\.community-composer,\s*\.workbench-generation-editor\) \.inspector-scroll\s*\{[^}]*overflow-y:\s*auto;/s);
  assert.match(uiSource, /\{isQuick && <>\s*<button\s*id="mobile-composer-launcher"[\s\S]*?创作下一版/);
  assert.match(uiSource, /className="composer-mobile-edit-header"[\s\S]*?返回当前成片[\s\S]*?返回首页灵感[\s\S]*?创作设置/);
  assert.match(uiSource, /className="composer-mobile-draft-summary"[\s\S]*?返回编辑/);
  assert.match(composerStyles, /@media \(max-width: 620px\)[\s\S]*?\.community-composer\[data-mobile-open="false"\]\s*\{[^}]*height:\s*72px;/s);
  assert.match(composerStyles, /@media \(max-width: 620px\)[\s\S]*?\.community-composer\[data-mobile-open="true"\]\s*\{[^}]*height:\s*100%;[^}]*max-height:\s*none;/s);
  assert.match(composerStyles, /@media \(max-width: 620px\)[\s\S]*?\.community-composer-header\s*\{\s*display:\s*none;/s);
  assert.match(shellStyles, /\.app-shell\.is-community-home\.is-mobile-composer-open > \.main-canvas\s*\{\s*display:\s*none;/);
  assert.match(shellStyles, /\.app-shell\.is-community-home\.is-mobile-composer-open > \.community-composer\s*\{[^}]*grid-row:\s*3;[^}]*min-height:\s*0;/);
});

test("the active phone route is brought into the visible navigation rail", () => {
  assert.match(appSource, /window\.matchMedia\("\(max-width: 900px\)"\)\.matches/);
  assert.match(appSource, /track\.querySelector\("\.is-active"\)/);
  assert.match(appSource, /activeItem\.offsetLeft - \(track\.clientWidth - activeItem\.offsetWidth\) \/ 2/);
  assert.match(appSource, /track\.scrollTo\(\{ left: Math\.max\(0, targetLeft\), behavior: "auto" \}\)/);
});
