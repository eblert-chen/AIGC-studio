import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = (
  await Promise.all([
    "../src/App.jsx",
    "../src/app/useStudioRouteState.js",
    "../src/app/useStudioIdentity.js",
    "../src/app/usePersonalModelCatalog.js",
  ].map((file) => readFile(new URL(file, import.meta.url), "utf8")))
).join("\n");
const editorSource = await readFile(new URL("../src/components/GenerationEditor.jsx", import.meta.url), "utf8");
const uiSource = `${appSource}\n${editorSource}`;
const clientSource = (
  await Promise.all([
    "platformClient.js",
    "platformCore.js",
    "sessionPersonalApi.js",
    "companyApi.js",
    "publishingApi.js",
    "platformAdminApi.js",
    "assetsTasksApi.js",
  ].map((file) => readFile(new URL(`../src/api/${file}`, import.meta.url), "utf8")))
).join("\n");

test("personal Studio discovers a real session surface and never invents a company", () => {
  assert.match(appSource, /liveClient\.getSessionSurfaces/);
  assert.doesNotMatch(appSource, /legacyIdentityProbe/);
  assert.match(appSource, /liveClient\.getPersonalMe/);
  assert.match(appSource, /workspace_kind: "personal"/);
  assert.match(appSource, /company_id: null/);
  assert.match(appSource, /surfacePath\(surface === "personal" \? "personal" : "studio", nextNav\)/);
  assert.doesNotMatch(clientSource, /X-User-ID/);
});

test("personal generation uses isolated points, APIs and idempotency storage", () => {
  assert.match(appSource, /listModels: \(\.\.\.args\) => liveClient\.listPersonalModels/);
  assert.match(appSource, /createTask: \(\.\.\.args\) => liveClient\.createPersonalTask/);
  assert.match(appSource, /const creationScopeKey = identityReady && studioWorkspaceKey && sessionIdentity\?\.user_id\s*\? JSON\.stringify\(\[DEMO_MODE \? "demo" : "live", runtimePlatformConfig\.baseUrl, sessionIdentity\.user_id, isPersonalWorkspace \? "personal" : "company", studioWorkspaceKey\]\)/);
  assert.match(appSource, /const pendingStorageKey = creationScopeKey/);
  assert.match(appSource, /const submissionStorageKey = submissionContext\.scopeKey/);
  assert.match(appSource, /rememberPendingCreate\(submissionStorageKey, pendingCreateRef\.current\)/);
  assert.match(appSource, /version: 6,\s*workspaceKey: submissionStorageKey,\s*creationContext: submissionContext/);
  assert.doesNotMatch(appSource, /rememberPendingCreate\(studioWorkspaceKey, pendingCreateRef\.current\)/,
    "new personal requests must not fall back to a workspace-only namespace shared by different users or environments");
  assert.match(appSource, /includeAssets: LIVE_MODE && !isPersonalWorkspace/);
  assert.match(appSource, /unitPricePoints/);
  assert.match(uiSource, /个人余额不与企业钱包混用/);
});

test("personal model catalog is visible without becoming selectable generation evidence", () => {
  assert.match(appSource, /listModelCatalog: \(\.\.\.args\) => liveClient\.listPersonalModelCatalog/);
  assert.match(appSource, /items\.map\(normalizePersonalModelCatalogEntry\)\.filter\(Boolean\)/);
  assert.match(appSource, /setModels\(normalized\)/);
  assert.match(editorSource, /const catalogOnlyModels = isPersonalWorkspace/);
  assert.match(editorSource, /personalModelCatalog\.filter\(\(item\) => !availableModelIds\.has\(item\.id\)\)/);
  assert.match(editorSource, /className="director-model-option is-unavailable"[\s\S]{0,180}aria-disabled="true"/);
  assert.doesNotMatch(
    editorSource,
    /catalogOnlyModels\.map\(\(item\) =>[\s\S]{0,1800}(?:onClick|selectStudioModel)/,
    "catalog-only rows must never acquire model selection behavior",
  );
  assert.match(editorSource, /disabled=\{modelsLoading \|\| \(!isPersonalWorkspace && models\.length === 0/);
  assert.match(editorSource, /const modelPanelError = modelsError && personalModelCatalogError/);
  assert.match(editorSource, /item\.available\s*\? "暂不可选"/);
  assert.match(editorSource, /item\.available\s*\? "可用状态正在同步，请刷新后再选择。"/);
});

test("personal unsupported capabilities are visible and fail closed", () => {
  assert.match(appSource, /capability="素材"/);
  assert.match(appSource, /capability="发布"/);
  assert.match(appSource, /个人任务取消接口尚未开放/);
  assert.match(appSource, /!canAccessArtifacts \|\| isPersonalWorkspace/);
  assert.match(appSource, /canPromoteArtifacts=\{LIVE_MODE && canManageAssets\}/);
});

test("personal artifact access uses task-bound personal endpoints without company scope", () => {
  assert.match(clientSource, /getPersonalArtifactPreview/);
  assert.match(clientSource, /getPersonalArtifactDownload/);
  assert.match(clientSource, /personal\/tasks\/\$\{encodeURIComponent\(taskId\)\}\/artifacts\/\$\{encodeURIComponent\(assetId\)\}\/preview/);
  assert.match(appSource, /studioClient\.getArtifactPreview/);
  assert.match(appSource, /\.\.\.\(!isPersonalWorkspace \? \{ scope \} : \{\}\)/);
});
