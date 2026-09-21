import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { resolveTaskStatus } from "../src/taskStatus.js";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const demoFixtureSource = await readFile(
  new URL("../src/demo/studioDemoFixtures.js", import.meta.url),
  "utf8",
);
const hubSource = await readFile(new URL("../src/CreationHub.jsx", import.meta.url), "utf8");
const workbenchSource = await readFile(
  new URL("../src/pages/studio/CreationWorkbenchViews.jsx", import.meta.url),
  "utf8",
);
const lineageSource = await readFile(
  new URL("../src/pages/studio/LineageGraphWorkspace.jsx", import.meta.url),
  "utf8",
);
const creationSurfaceSource = `${hubSource}\n${workbenchSource}\n${lineageSource}`;
const resultDetailSource = await readFile(
  new URL("../src/pages/studio/ResultDetailView.jsx", import.meta.url),
  "utf8",
);
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

function functionBody(source, name, nextName) {
  const start = source.indexOf(`const ${name} =`);
  const end = source.indexOf(`const ${nextName} =`, start + 1);
  assert.notEqual(start, -1, `${name} must exist`);
  assert.notEqual(end, -1, `${nextName} must follow ${name}`);
  return source.slice(start, end);
}

test("successful results restore a current-capability draft without submitting or charging", () => {
  const body = functionBody(appSource, "prepareHistoricalTask", "retryHistoryTask");
  assert.match(body, /pendingCreateRef\.current/);
  assert.match(body, /reconcileGenerationDraft/);
  assert.match(body, /activeAssetsById/);
  assert.match(body, /creationSession\.updateQuickDraft\(restoredDraft/);
  assert.doesNotMatch(body, /applyReconciledDraft\(|setModelId\(|setPrompt\(/);
  assert.match(body, /closeResultDialog\(\)/);
  assert.doesNotMatch(body, /setCurrentTask\(|setCurrentTaskId\(|setStage\(/);
  assert.doesNotMatch(body, /startGeneration\(|liveClient\.createTask\(|WalletService|RelayOutbox/i);
});

test("reuse and adjust restore drafts; advanced import and transfer never auto-submit", () => {
  assert.match(appSource, /createAgainFromTask[\s\S]{0,180}expanded: false/);
  assert.match(appSource, /adjustHistoricalTask[\s\S]{0,180}expanded: true/);
  assert.match(appSource, /onContinueTask=\{createAgainFromTask\}/);
  assert.match(appSource, /actionLabel: "用此设置新建草稿"/);
  assert.match(resultDetailSource, /只恢复草稿并按当前能力重新校验，不会立即创建任务或扣费/);
  assert.match(creationSurfaceSource, /onContinueTask\?\.\(activeTake\.task\)/);
  assert.match(creationSurfaceSource, /复用当前设置/);
  assert.match(creationSurfaceSource, /contextualController\.onImportTask/);
  assert.match(workbenchSource, /controller\.onImportTask\(take\.task\)/);
  assert.match(workbenchSource, /复制为新草稿，不覆盖已有内容/);
  assert.match(workbenchSource, /controller\.onTransfer\(path\.id\)/);
  assert.match(lineageSource, /从精确 Take 分叉/);
  assert.match(lineageSource, /sourceTake:\s*selectedTake/);
  assert.doesNotMatch(creationSurfaceSource, /带此设置进入极速档/);
  assert.doesNotMatch(creationSurfaceSource, /startGeneration|createTask\(/);
  assert.doesNotMatch(creationSurfaceSource, /再次生成|重新生成/);
});

test("notebook chosen shots require explicit version selection and do not claim an assembled movie", () => {
  const start = workbenchSource.indexOf("function selectedEntryTake(");
  const end = workbenchSource.indexOf("function WorkbenchHeading(", start);
  assert.ok(start >= 0 && end > start);
  // Exercise the production pure selector, without JSX or React scheduling.
  const selectedEntryTake = new Function(`${workbenchSource.slice(start, end)}\nreturn selectedEntryTake;`)();
  const takes = [
    { id: "task-1:artifact-1", taskId: "task-1", artifactId: "artifact-1", task: { id: "task-1" }, status: "succeeded", artifact: { artifact_id: "artifact-1", asset_id: "asset-1" } },
    { id: "task-1:artifact-2", taskId: "task-1", artifactId: "artifact-2", task: { id: "task-1" }, status: "succeeded", artifact: { artifact_id: "artifact-2", asset_id: "asset-2" } },
    { id: "task-2:artifact-3", taskId: "task-2", artifactId: "artifact-3", task: { id: "task-2" }, status: "succeeded", artifact: { artifact_id: "artifact-3", asset_id: "asset-3" } },
  ];
  assert.equal(selectedEntryTake({ takes, selectedTaskId: "" }, true), null);
  assert.equal(selectedEntryTake({ takes, selectedTaskId: "unknown" }, true), null);
  assert.equal(selectedEntryTake({ takes, selectedTaskId: "task-1" }, true), takes[0]);
  assert.equal(selectedEntryTake({ takes, selectedTaskId: "task-1", selectedArtifactId: "artifact-2" }, true), takes[1]);
  assert.equal(selectedEntryTake({ takes, selectedTaskId: "task-1", selectedArtifactId: "unknown" }, true), null);
  assert.equal(selectedEntryTake({ takes, selectedTaskId: "" }), takes[2], "Latest take is a preview fallback, not an approved selection");
  assert.match(workbenchSource, /selectedEntryTake\(entry, true\)[\s\S]*?filter\(\(\{ take \}\) => take\?\.status === "succeeded" && take\.artifact\)/);
  assert.match(workbenchSource, /尚未拼接成片/);
  assert.doesNotMatch(workbenchSource, /自动拼接|自动合成成片|onCreatePublication|approvePublicationJob/);
});

test("workbench versions derive one stable artifact identity from Platform task evidence", () => {
  const start = hubSource.indexOf("function taskId(");
  const end = hubSource.indexOf("function compactTaskId(", start);
  assert.ok(start >= 0 && end > start);
  const workbenchArtifactId = new Function(`${hubSource.slice(start, end)}\nreturn workbenchArtifactId;`)();
  assert.equal(
    workbenchArtifactId({ id: "task-1" }, { artifact_id: "artifact-explicit", asset_id: "asset-1" }),
    "artifact-explicit",
  );
  assert.equal(
    workbenchArtifactId({ task_id: "task-1" }, { asset_id: "asset-1" }),
    "task-1:asset-1",
  );
  assert.equal(workbenchArtifactId({ id: "task-1" }, {}), "");
  assert.match(hubSource, /artifact_id: canonicalArtifactId/);
  assert.match(workbenchSource, /onSelectVersion\?\.\(entry\.id, taskId, artifactId\)/);
});

test("advanced result actions preserve successful-artifact and permission gates for the exact selected take", () => {
  const start = workbenchSource.indexOf("function WorkbenchResultActions(");
  const end = workbenchSource.indexOf("function WorkbenchVersions(", start);
  assert.ok(start >= 0 && end > start);
  const actions = workbenchSource.slice(start, end);
  assert.match(actions, /take\.status === "succeeded" && take\.artifact/);
  for (const action of ["Promote", "Download", "Publish"]) {
    assert.match(actions, new RegExp(`disabled=\\{!props\\.can${action}Artifact\\}`));
    assert.match(actions, new RegExp(`props\\.on${action}Artifact\\(take\\.task, take\\.artifact\\)`));
  }
  assert.doesNotMatch(actions, /createTask|createPublicationJob|approvePublicationJob|provider_url|signed_url/);
});

test("unknown submission copy preserves no-retry and no-channel-switch safety", () => {
  const reconciliation = resolveTaskStatus("reconciliation_required");

  assert.match(reconciliation.detail, /提交结果尚未确认/);
  assert.match(reconciliation.detail, /不会自动再次提交/);
  assert.match(reconciliation.detail, /不会.*改用其他渠道/);
  assert.match(reconciliation.detail, /避免重复提交/);
});

test("demo artworks carry the same model identity required by the live continuation contract", () => {
  const artworks = demoFixtureSource.slice(
    demoFixtureSource.indexOf("DEVELOPMENT_DEMO_ARTWORKS"),
  );
  assert.match(artworks, /artifact_id: "demo-artwork-1"[\s\S]*?model_id: "cinemox"/);
  assert.match(artworks, /artifact_id: "demo-artwork-2"[\s\S]*?model_id: "frameflow"/);
});

test("exact archived artifacts hand off to publishing without creating a publication", () => {
  const body = functionBody(appSource, "openPublicationForArtifact", "retryGeneration");
  assert.match(body, /artifact\?\.artifact_id/);
  assert.match(body, /canStartPublication/);
  assert.match(body, /setPublicationIntent/);
  assert.match(body, /artwork:\s*\{[\s\S]*?\.\.\.artifact,[\s\S]*?task_id:/);
  assert.match(body, /scope: publicationScope/);
  assert.match(body, /navigateStudio\("publish"\)/);
  assert.doesNotMatch(body, /createPublicationJob|approvePublicationJob/);
  assert.match(appSource, /initialArtifactId=\{publicationIntent\?\.artifactId \|\| ""\}/);
  assert.match(appSource, /initialArtwork=\{publicationIntent\?\.artwork \?\? null\}/);
  assert.match(appSource, /initialArtworkScope=\{publicationIntent\?\.scope \|\| "mine"\}/);
  assert.match(appSource, /openComposerRequest=\{publicationIntent\?\.request \?\? null\}/);
});

test("artifact promotion stays inside the Platform and is permission gated", () => {
  assert.match(appSource, /canPromoteArtifacts=\{LIVE_MODE && canManageAssets\}/);
  assert.match(appSource, /companyClient\.promoteArtifactToInputAsset/);
  assert.match(appSource, /if \(!canAccessArtifacts \|\| isPersonalWorkspace\)/);
  assert.match(appSource, /idempotencyKey: `promote-\$\{taskId\}-\$\{artifact\.asset_id\}`/);
  assert.match(clientSource, /promoteArtifactToInputAsset/);
  assert.match(clientSource, /\/input-asset/);
  assert.match(clientSource, /body: \{ idempotency_key: stableIdempotencyKey \}/);
  const promotionClient = clientSource.slice(
    clientSource.indexOf("promoteArtifactToInputAsset:"),
    clientSource.indexOf("createTask: async", clientSource.indexOf("promoteArtifactToInputAsset:")),
  );
  assert.doesNotMatch(promotionClient, /provider/i);
});

test("the result evidence dialog imports its shared download status", () => {
  assert.match(
    appSource,
    /const ResultDetailView = lazyNamed\([\s\S]*?import\("\.\/pages\/studio\/ResultDetailView\.jsx"\)[\s\S]*?"ResultDetailView"/,
  );
  assert.match(
    resultDetailSource,
    /import\s*\{\s*DownloadStatus,\s*IconButton\s*\}\s*from\s*"\.\.\/\.\.\/components\/studio\/StudioWorkspaceViews\.jsx"/,
  );
});
