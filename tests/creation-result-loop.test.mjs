import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { studioSource } from "./studio-source.mjs";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
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
  assert.match(body, /navigateStudio\("create"\)/);
  assert.match(body, /closeResultDialog\(\)/);
  assert.doesNotMatch(body, /setCurrentTask\(|setCurrentTaskId\(|setStage\(/);
  assert.doesNotMatch(body, /startGeneration\(|liveClient\.createTask\(|WalletService|RelayOutbox/i);
});

test("again and adjust actions differ only in composer detail and never auto-submit", () => {
  assert.match(appSource, /createAgainFromTask[\s\S]{0,180}expanded: false/);
  assert.match(appSource, /adjustHistoricalTask[\s\S]{0,180}expanded: true/);
  assert.match(studioSource, /再次生成/);
  assert.match(studioSource, /调整后再创作/);
  assert.match(resultDetailSource, /只恢复草稿并按当前能力重新校验，不会立即创建任务或扣费/);
});

test("demo artworks carry the same model identity required by the live continuation contract", () => {
  const artworks = appSource.slice(
    appSource.indexOf("const DEMO_ARTWORKS"),
    appSource.indexOf("export function App"),
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

test("the result evidence dialog imports its extracted download badge", () => {
  assert.match(
    appSource,
    /import\s*\{\s*ResultDetailView\s*\}\s*from\s*"\.\/pages\/studio\/ResultDetailView\.jsx"/,
  );
  assert.match(
    resultDetailSource,
    /import\s*\{\s*DownloadBadge\s*\}\s*from\s*"\.\.\/\.\.\/components\/studio\/StudioCollectionControls\.jsx"/,
  );
});
