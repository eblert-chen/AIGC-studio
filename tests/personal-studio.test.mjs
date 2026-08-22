import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
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
  assert.match(appSource, /liveClient\.getPersonalMe/);
  assert.match(appSource, /workspace_kind: "personal"/);
  assert.match(appSource, /company_id: null/);
  assert.match(appSource, /surfacePath\(surface === "personal" \? "personal" : "studio", nextNav\)/);
  assert.doesNotMatch(clientSource, /X-User-ID/);
});

test("personal generation uses isolated points, APIs and idempotency storage", () => {
  assert.match(appSource, /listModels: \(\.\.\.args\) => liveClient\.listPersonalModels/);
  assert.match(appSource, /createTask: \(\.\.\.args\) => liveClient\.createPersonalTask/);
  assert.match(appSource, /rememberPendingCreate\(studioWorkspaceKey, pendingCreateRef\.current\)/);
  assert.match(appSource, /workspaceKey: studioWorkspaceKey/);
  assert.match(appSource, /includeAssets: LIVE_MODE && !isPersonalWorkspace/);
  assert.match(appSource, /unitPricePoints/);
  assert.match(appSource, /个人余额不与企业钱包混用/);
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
