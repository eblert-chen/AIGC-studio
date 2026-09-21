import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { parse } from "postcss";

const routes = await readFile(
  new URL("../src/design-system/studio-routes.css", import.meta.url),
  "utf8",
);
const mobileStudio = await readFile(
  new URL("../src/design-system/mobile-studio.css", import.meta.url),
  "utf8",
);
const workbenches = await readFile(
  new URL("../src/design-system/workbenches.css", import.meta.url),
  "utf8",
);
const tokens = await readFile(
  new URL("../src/design-system/tokens.css", import.meta.url),
  "utf8",
);
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
const communitySource = await readFile(
  new URL("../src/CommunityHome.jsx", import.meta.url),
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
const resultSource = await readFile(
  new URL("../src/pages/studio/ResultDetailView.jsx", import.meta.url),
  "utf8",
);
const statusSource = await readFile(
  new URL("../src/pages/studio/StudioStatusViews.jsx", import.meta.url),
  "utf8",
);
const workspaceSource = await readFile(
  new URL("../src/components/studio/StudioWorkspaceViews.jsx", import.meta.url),
  "utf8",
);
const presentationSource = await readFile(
  new URL("../src/components/studio/studioPresentation.js", import.meta.url),
  "utf8",
);
const publishingSource = await readFile(
  new URL("../src/PublishingCenter.jsx", import.meta.url),
  "utf8",
);

function mediaBlock(source, maxWidth) {
  const marker = `@media (max-width: ${maxWidth}px)`;
  const start = source.indexOf(marker);
  assert.notEqual(start, -1, `${marker} must exist`);
  const open = source.indexOf("{", start);
  let depth = 1;
  let cursor = open + 1;
  while (cursor < source.length && depth > 0) {
    if (source[cursor] === "{") depth += 1;
    if (source[cursor] === "}") depth -= 1;
    cursor += 1;
  }
  assert.equal(depth, 0, `${marker} must close`);
  return source.slice(open + 1, cursor - 1);
}

test("Studio route stylesheet parses and cannot leak into Company or Operations", () => {
  assert.doesNotThrow(() => parse(routes));
  assert.match(routes, /^\/\* Authored Studio routes\. Each creation surface keeps its own working metaphor\./);
  assert.doesNotMatch(routes, /(?:^|\n)\s*:root\s*\{/);
  assert.doesNotMatch(routes, /\.control-shell|\.ops-console/);
  assert.doesNotMatch(routes, /\.community-home|\.community-composer/);
});

test("Studio route layer keeps the near-white continuous-surface and type contracts", () => {
  assert.match(tokens, /--canvas:\s*#fafaf7;/);
  assert.match(tokens, /--surface:\s*#ffffff;/);
  assert.match(tokens, /--selection-violet:\s*#5a55d2;/);
  assert.match(tokens, /--selection-violet-strong:\s*#4a45c2;/);
  assert.match(tokens, /--selection-violet-soft:\s*#eeedfb;/);
  assert.match(tokens, /--workflow-orange:\s*#ce360a;/);
  assert.match(tokens, /--radius-control:\s*0\.625rem;/);
  assert.match(tokens, /--radius-md:\s*0\.75rem;/);
  assert.match(tokens, /--radius-lg:\s*1rem;/);
  assert.match(
    routes,
    /\.secondary-heading,[\s\S]*?\.publication-heading\s*\{[^}]*min-height:\s*54px;[^}]*padding-bottom:\s*14px;/,
  );
  assert.match(
    routes,
    /\.secondary-heading h1,[\s\S]*?\.publication-heading h1\s*\{[^}]*font-size:\s*clamp\(24px, 2\.4vw, 30px\);/,
  );
  assert.doesNotMatch(routes, /\.secondary-heading,[\s\S]{0,220}border-bottom:/);
  assert.doesNotMatch(routes, /font(?:-size)?\s*:[^;\n]*\b(?:[0-9]|1[01])px\b/);
});

test("creation uses an invitation entry and three real production workbenches", () => {
  assert.match(
    creationSource,
    /const CREATION_WORKBENCHES = new Set\(\["console", "notebook", "canvas"\]\)/,
  );
  assert.match(
    creationSource,
    /data-ui="creation-route"[\s\S]*?data-creation-workbench=\{workbench\}/,
  );
  assert.match(
    creationSource,
    /workbench === "console"[\s\S]*?<DirectorConsoleView[\s\S]*?workbench === "notebook"[\s\S]*?<DirectorNotebookView[\s\S]*?workbench === "canvas"[\s\S]*?<LineageCanvasView[\s\S]*?<CreationEntryView/,
  );
  assert.match(
    creationSource,
    /const sharedWorkbenchProps = \{[\s\S]*?onStartCreation,[\s\S]*?onContinueTask: continueTask,[\s\S]*?onOpenTask,/,
  );
  assert.match(workbenchSource, /data-ui="creation-invitation-entry"[\s\S]*?<h2 id="creation-entry-title">今天想创作什么？<\/h2>/);
  assert.match(workbenchSource, /className="wb-pathways"[\s\S]*?data-workbench=\{id\}[\s\S]*?onOpenWorkbench\(id\)/);
  assert.match(workbenchSource, /className="creation-ideas"[\s\S]*?onUsePrompt\?\.\(idea\.prompt\)/);
  assert.match(workbenchSource, /data-ui="director-console"[\s\S]*?<StoryAiDirectorDesk[\s\S]*?aria-label="当前镜头生成与版本"/);
  assert.match(workbenchSource, /data-ui="director-notebook"[\s\S]*?aria-label="分场手记"/);
  assert.match(workbenchSource, /lazy\(\(\) => import\("\.\/LineageGraphWorkspace\.jsx"\)\)/);
  assert.match(lineageSource, /data-ui="lineage-canvas"[\s\S]*?role="region" aria-label="方向关系画布"/);
  assert.match(
    workbenchSource,
    /onClick=\{\(\) => onContinueTask\?\.\(activeTake\.task\)\}[\s\S]*?复用当前设置/,
  );
  assert.match(
    workbenchSource,
    /onClick=\{\(\) => props\.onPublishArtifact\(take\.task, take\.artifact\)\}/,
  );
  assert.doesNotMatch(workbenchSource, /下一张示例中展开|仅供视觉预览|alert\(/);

  assert.match(workbenches, /\.wb-pathways\s*\{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\);/);
  assert.match(workbenches, /\.wb3d-production\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1\.15fr\) minmax\(300px, \.72fr\);/);
  assert.match(workbenches, /\.wb-notebook-paper\s*\{[^}]*border-radius:\s*var\(--radius-lg\);[^}]*background:\s*#fffcf5;/);
  assert.match(workbenches, /\.lineage-graph\s*\{[^}]*height:\s*clamp\(/s);
  assert.match(lineageSource, /<Background variant=\{BackgroundVariant\.Dots\} gap=\{22\}/);
  assert.match(workbenches, /\.wb-result-actions button\s*\{[^}]*font-size:\s*var\(--text-body-sm\);/);
});

test("materials and works remain dense, content-aware real-media contact sheets", () => {
  assert.match(
    routes,
    /\.asset-grid\s*\{[^}]*grid-template-columns:\s*repeat\(4, minmax\(0, 1fr\)\);[^}]*gap:\s*14px;/,
  );
  assert.match(
    routes,
    /\.asset-item,[\s\S]*?\.asset-upload\s*\{[^}]*border-radius:\s*16px;[^}]*background:\s*var\(--surface\);[^}]*box-shadow:/,
  );
  assert.match(
    routes,
    /\.artwork-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\);[^}]*gap:\s*14px;/,
  );
  assert.match(routes, /\.artwork-item\.is-featured\s*\{[^}]*grid-column:\s*1 \/ -1;[^}]*grid-template-columns:/);
  assert.match(artworksSource, /index === 0 && page === 1 && !hasActiveFilters/);
  assert.match(
    routes,
    /\.artwork-media\s*\{[^}]*aspect-ratio:\s*16 \/ 9;[^}]*background:\s*var\(--stage-surface\);/,
  );
});

test("Studio collections use media-first actions and user-facing state language", () => {
  assert.ok(
    artworksSource.indexOf('className="artwork-media"') < artworksSource.indexOf("<dl>"),
    "作品媒体应先于审计信息",
  );
  const actionStart = artworksSource.indexOf('<footer className="artwork-action-row">');
  const actionEnd = artworksSource.indexOf("</footer>", actionStart);
  const artworkActions = artworksSource.slice(actionStart, actionEnd);
  assert.equal((artworkActions.match(/className="is-primary"/g) || []).length, 1);
  assert.match(artworkActions, /onClick=\{\(\) => onAdjust\?\.\(artwork\)\}[\s\S]*?继续创作/);
  assert.match(artworkActions, /className="download-button"[\s\S]*?下载作品/);
  assert.match(artworkActions, /className="text-button"[\s\S]*?去发布/);
  assert.match(artworkActions, /className="artwork-more-actions"[\s\S]*?更多[\s\S]*?查看任务[\s\S]*?存入素材库/);
  assert.doesNotMatch(artworkActions, /复用设置|打开并调整/);
  assert.match(historySource, /恢复为草稿/);
  assert.doesNotMatch(`${artworksSource}\n${historySource}\n${resultSource}`, /再次生成|按原参数重试|调整后再创作/);

  assert.match(presentationSource, /label: "下载链接已生成"/);
  assert.match(presentationSource, /label: "已确认下载"/);
  assert.match(workspaceSource, /aria-label=\{`下载状态：\$\{state\.label\}。\$\{state\.detail\}`\}/);
  assert.match(resultSource, /生成下载链接不代表下载完成；完成状态会在系统确认后更新/);
});

test("Studio collections distinguish empty, filtered, denied, failed, and demo states", () => {
  assert.match(communitySource, /没有符合当前筛选的灵感/);
  assert.match(communitySource, /暂无\$\{activeSection\}灵感/);
  assert.match(artworksSource, /没有符合当前筛选的作品/);
  assert.match(artworksSource, /还没有作品/);
  assert.match(artworksSource, /作品读取失败/);
  assert.match(historySource, /没有符合当前筛选的任务/);
  assert.match(historySource, /还没有任务/);
  assert.match(historySource, /任务历史读取失败/);
  assert.match(statusSource, /当前账号不能查看\$\{objectName\}/);
  assert.match(communitySource, /示例内容/);
  assert.match(artworksSource, /以下为示例作品/);
  assert.match(historySource, /以下为示例任务/);
  assert.match(resultSource, /示例结果，不包含真实文件/);
});

test("Studio collection UI does not expose implementation or audit jargon", () => {
  const collectionUiSource = [
    communitySource,
    artworksSource,
    historySource,
    resultSource,
    statusSource,
  ].join("\n");
  assert.doesNotMatch(
    collectionUiSource,
    /Platform|canonical|签发|归档|产物|能力合同|访问 URL|权限码|\b(?:assets|tasks|publish)\.[a-z.]+\b/,
  );
});

test("history, settings and publishing use ledgers with a contextual publishing sidebar", () => {
  assert.match(
    routes,
    /\.history-list\s*\{[^}]*border-radius:\s*16px;[^}]*background:\s*var\(--surface\);[^}]*box-shadow:/,
  );
  assert.match(
    routes,
    /\.task-audit-grid\s*\{[^}]*grid-template-columns:\s*repeat\(4, 1fr\);[^}]*background:\s*var\(--surface-soft\);/,
  );
  assert.match(
    routes,
    /\.account-settings-layout\s*\{[^}]*grid-template-columns:\s*210px minmax\(0, 1fr\);/,
  );
  assert.match(
    routes,
    /\.setting-row\s*\{[^}]*min-height:\s*82px;[^}]*justify-content:\s*space-between;/,
  );
  assert.match(
    routes,
    /\.publication-layout\s*\{[^}]*grid-template-columns:\s*minmax\(280px, 330px\) minmax\(0, 1fr\);[^}]*grid-template-areas:\s*"requirements requirements"\s*"connections jobs";[^}]*gap:\s*16px;/,
  );
  assert.match(routes, /\.publication-handoff-index\s*\{\s*grid-area:\s*requirements;\s*\}/);
  assert.match(routes, /\.publication-jobs\s*\{\s*grid-area:\s*jobs;\s*\}/);
  assert.match(routes, /\.publication-connections\s*\{\s*grid-area:\s*connections;\s*\}/);
  assert.match(
    routes,
    /\.publication-handoff-index \.publication-job-list\s*\{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\);/,
  );
  assert.match(
    publishingSource,
    /className="publication-artwork-select publication-handoff-index"[\s\S]*?<h2 id="publication-handoff-title">发布前要求<\/h2>[\s\S]*?className="publication-jobs"[\s\S]*?<h2 id="publication-jobs-title">发布安排<\/h2>[\s\S]*?<aside className="publication-connections"[\s\S]*?<h2 id="publication-connections-title">发布账号<\/h2>/,
  );
  assert.match(
    routes,
    /\.publication-primary-button\s*\{[^}]*border:\s*0;[^}]*background:\s*var\(--signal\);[^}]*border-radius:\s*10px;/,
  );
  assert.match(
    routes,
    /\.publication-dialog\s*\{[^}]*overflow:\s*auto;[^}]*border-radius:\s*16px;[^}]*background:\s*var\(--surface\);/,
  );
});

test("mobile routes keep quick creation available and gate advanced workbenches", () => {
  const compact = mediaBlock(routes, 980);
  const phone = mediaBlock(routes, 560);
  const workbenchPhone = mediaBlock(workbenches, 720);
  assert.match(
    mobileStudio,
    /@media \(max-width:\s*720px\)[\s\S]*?\.app-shell\[data-theme\]\s*\{[^}]*--studio-mobile-nav-height:\s*48px;[^}]*--shell-topbar-size:\s*56px;/,
  );
  assert.match(
    mobileStudio,
    /\.app-shell\[data-theme\]\.is-advanced-workbench\s*\{[^}]*grid-template-rows:\s*minmax\(0, 1fr\);[\s\S]*?\.app-shell\[data-theme\]\.is-advanced-workbench > \.topbar,[\s\S]*?\.app-shell\[data-theme\]\.is-advanced-workbench > \.side-nav\s*\{\s*display:\s*none;/,
  );
  assert.match(
    workbenchPhone,
    /\.wb-pathways\s*\{[^}]*grid-template-columns:\s*1fr;/,
  );
  assert.match(
    workbenches,
    /\.wb-desktop-gate-header\s*\{[^}]*grid-template-columns:\s*72px minmax\(0, 1fr\) 72px;[^}]*min-height:\s*56px;/,
  );
  assert.match(
    workbenches,
    /\.wb-desktop-gate-body \.wb-primary\s*\{[^}]*min-height:\s*48px;/,
  );
  assert.match(creationSource, /desktopGateActive \? \([\s\S]*?<DesktopWorkbenchGate kind=\{workbench\}/);
  assert.match(workbenchSource, /advancedWorkbenchesDesktopOnly && <span className="wb-desktop-badge">仅桌面端<\/span>/);
  assert.match(workbenchSource, /!advancedWorkbenchesDesktopOnly && workbenchController\?\.onTransfer/);
  assert.match(
    compact,
    /\.publication-layout\s*\{[^}]*grid-template-columns:\s*1fr;[^}]*grid-template-areas:\s*"requirements"\s*"jobs"\s*"connections";/,
  );
  assert.match(routes, /\.publication-handoff-index \.publication-job-list\s*\{[^}]*grid-template-columns:\s*1fr;/);
  assert.match(phone, /\.asset-grid,[\s\S]*?\.artwork-grid\s*\{[^}]*grid-template-columns:\s*1fr;/);
  assert.match(phone, /\.task-audit-grid\s*\{[^}]*grid-template-columns:\s*1fr;/);
  assert.match(
    mobileStudio,
    /\.app-shell\[data-theme\] \.creation-record-browser button,[\s\S]*?\.app-shell\[data-theme\] \.secondary-view button,[\s\S]*?\.app-shell\[data-theme\] \.taskbar button\s*\{[^}]*min-height:\s*44px;/,
  );
});

test("route layer avoids specificity and decorative-effect debt", () => {
  const creationLayers = `${routes}\n${workbenches}`;
  assert.doesNotMatch(creationLayers, /!important/);
  const radialGradients = creationLayers.match(/radial-gradient\(/g) || [];
  assert.equal(radialGradients.length, 0, "the React Flow Background owns the authored Canvas dot field");
  assert.match(lineageSource, /<Background variant=\{BackgroundVariant\.Dots\}/);
  assert.doesNotMatch(creationLayers, /\.creation-entry[^}]*radial-gradient\(|\.wb-notebook[^}]*radial-gradient\(|\.wb-console[^}]*radial-gradient\(/);
  assert.doesNotMatch(creationLayers, /:has\(/);
  assert.doesNotMatch(routes, /\.creation-atlas-|\.community-composer|\.inspector-scroll|\.inspector-actions/);
});
