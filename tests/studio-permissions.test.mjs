import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  resolveCompanyStudioAccess,
  studioRouteAvailable,
} from "../src/studioAccess.js";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const taskRuntimeSource = await readFile(
  new URL("../src/app/useGenerationTaskRuntime.js", import.meta.url),
  "utf8",
);
const creationSource = await readFile(new URL("../src/CreationHub.jsx", import.meta.url), "utf8");
const workbenchSource = await readFile(
  new URL("../src/pages/studio/CreationWorkbenchViews.jsx", import.meta.url),
  "utf8",
);
const creationSurfaceSource = `${creationSource}\n${workbenchSource}`;

test("company Studio permissions remain independent and fail closed", () => {
  const createOnly = resolveCompanyStudioAccess(["tasks.create"]);
  assert.deepEqual(createOnly, {
    canReadModels: false,
    canReadAssets: false,
    canManageAssets: false,
    canReadTasks: false,
    canCreateTasks: true,
    canReadArtworks: false,
    canAccessArtifacts: false,
  });
  assert.equal(studioRouteAvailable("create", createOnly), true);
  assert.equal(studioRouteAvailable("history", createOnly), false);
  assert.equal(studioRouteAvailable("artworks", createOnly), false);

  const assetManager = resolveCompanyStudioAccess(["assets.manage"]);
  assert.equal(studioRouteAvailable("media", assetManager), true);
  assert.equal(assetManager.canReadAssets, false);
});

test("Studio controllers gate each request and polling loop with its actual permission", () => {
  assert.match(appSource, /canReadStudioModels = [\s\S]*companyStudioAccess\.canReadModels/);
  assert.match(appSource, /canReadStudioAssets = [\s\S]*companyStudioAccess\.canReadAssets/);
  assert.match(appSource, /canReadStudioTasks = [\s\S]*companyStudioAccess\.canReadTasks/);
  assert.match(appSource, /canReadStudioArtworks = [\s\S]*companyStudioAccess\.canReadArtworks/);
  assert.match(appSource, /!canReadStudioAssets[\s\S]*\.listAssets/);
  assert.match(taskRuntimeSource, /!canReadStudioTasks[\s\S]*!runtime\.currentTaskId[\s\S]*studioClient\.getTask\(runtime\.currentTaskId/);
  assert.doesNotMatch(appSource, /canReadStudioModels = [\s\S]{0,100}: hasCompanySession\s*;/);
});

test("tasks.create without tasks.read gets an honest non-polling state", () => {
  assert.match(appSource, /taskTrackingUnavailable/);
  assert.match(appSource, /当前账号不能查看任务进度，页面不会继续轮询/);
  assert.match(appSource, /historyAccessAvailable=\{!LIVE_MODE \|\| canReadStudioTasks\}/);
  assert.match(creationSurfaceSource, /无法查看创作记录/);
  assert.match(creationSurfaceSource, /暂时不会更新任务进度/);
  assert.doesNotMatch(creationSurfaceSource, /tasks\.read|tasks\.create/);
});
