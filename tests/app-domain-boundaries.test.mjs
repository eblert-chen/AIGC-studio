import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { PlatformApiError } from "../src/api/platformClient.js";
import {
  filesFromPendingRequest,
  readPendingCreate,
  rememberPendingCreate,
  taskRequestFingerprint,
} from "../src/app/pendingGeneration.js";
import {
  fetchArtworkPage,
  fetchTaskHistoryPage,
} from "../src/app/useStudioCollections.js";
import { discoverStudioIdentity } from "../src/app/useStudioIdentity.js";
import {
  createGenerationTaskRuntimeState,
  generationTaskRuntimeReducer,
  generationTaskStage,
} from "../src/app/useGenerationTaskRuntime.js";

const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const editorSource = await readFile(new URL("../src/components/GenerationEditor.jsx", import.meta.url), "utf8");
const uiSource = `${appSource}\n${editorSource}`;
const demoSource = await readFile(
  new URL("../src/demo/studioDemoFixtures.js", import.meta.url),
  "utf8",
);
const routeStateSource = await readFile(
  new URL("../src/app/useStudioRouteState.js", import.meta.url),
  "utf8",
);
const taskRuntimeSource = await readFile(
  new URL("../src/app/useGenerationTaskRuntime.js", import.meta.url),
  "utf8",
);

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

function validPendingCreate(workspaceKey = "company-1") {
  const requestPayload = {
    mode: "image_to_video",
    prompt: "保留同一次生成请求",
    duration_seconds: 10,
    aspect_ratio: "16:9",
    resolution: "1080p",
    output_count: 1,
    face_enabled: false,
    assets: [{ asset_id: "asset-1", media_type: "image" }],
  };
  const value = {
    version: 5,
    workspaceKey,
    modelId: "model-1",
    capabilityVersion: 3,
    quoteRevision: `sha256:${"a".repeat(64)}`,
    requestPayload,
    idempotencyKey: "stable-idempotency-key",
  };
  return {
    ...value,
    fingerprint: taskRequestFingerprint(JSON.stringify({
      modelId: value.modelId,
      capabilityVersion: value.capabilityVersion,
      quoteRevision: value.quoteRevision,
      requestPayload,
    })),
  };
}

test("App delegates bounded domains without duplicating the persistent Director Deck", () => {
  assert.match(appSource, /useStudioIdentity\(\{/);
  assert.match(appSource, /useStudioRouteState\(\)/);
  assert.match(appSource, /useStudioTaskCollections\(\{/);
  assert.match(appSource, /useStudioArtworkCollection\(\{/);
  assert.match(appSource, /useGenerationDraft\(\{/);
  assert.match(appSource, /useGenerationTaskRuntime\(\{/);
  assert.match(appSource, /useGenerationTaskLifecycle\(\{/);
  assert.match(taskRuntimeSource, /fetchTaskHistoryPage\(/);
  assert.match(taskRuntimeSource, /studioClient\.getTask\(runtime\.currentTaskId/);
  assert.doesNotMatch(appSource, /Promise\.all\(\[\s*fetchTaskHistoryPage/);
  assert.equal((uiSource.match(/ref=\{composerRef\}/g) || []).length, 1);
  assert.equal((appSource.match(/<GenerationEditor\b/g) || []).length, 1);
  assert.equal((appSource.match(/const startGeneration = async/g) || []).length, 1);
  assert.doesNotMatch(editorSource, /\buse(?:State|Effect|LayoutEffect|Reducer)\(|\b(?:fetch|createTask)\(/);
  assert.match(appSource, /activeNav === "shots" \|\| activeNav === "create"/);
  assert.ok(appSource.split(/\r?\n/).length < 4_700, "App must not regain its former 5,800-line shape");
  assert.ok((appSource.match(/useState\(/g) || []).length <= 36, "task runtime state must remain outside the shell");
  assert.ok((appSource.match(/useEffect\(/g) || []).length <= 17, "task polling effects must remain outside the shell");
  assert.doesNotMatch(appSource, /addEventListener\?\.\("popstate"/);
  assert.match(routeStateSource, /addEventListener\?\.\("popstate", handlePopState\)/);
  assert.match(routeStateSource, /history\[replace \? "replaceState" : "pushState"\]/);
});

test("generation task runtime applies lifecycle transitions atomically", () => {
  const initial = createGenerationTaskRuntimeState({
    demoMode: false,
    pendingCreate: { idempotencyKey: "same-request" },
  });
  assert.equal(initial.stage, "idle");
  assert.equal(initial.progress, 0);
  assert.match(initial.formError, /同一次请求/);

  const queued = generationTaskRuntimeReducer(initial, {
    type: "patch",
    value: { stage: "queued", progress: 6, submitting: true },
  });
  assert.deepEqual(
    { stage: queued.stage, progress: queued.progress, submitting: queued.submitting },
    { stage: "queued", progress: 6, submitting: true },
  );

  const active = generationTaskRuntimeReducer(queued, {
    type: "activate-task",
    task: { id: "task-1", status: "processing" },
    scope: "company",
  });
  assert.equal(active.currentTaskId, "task-1");
  assert.equal(active.currentTaskScope, "company");
  assert.equal(active.stage, generationTaskStage("processing", active.currentTask));

  const incompleteArtifact = generationTaskRuntimeReducer(active, {
    type: "activate-task",
    task: { id: "task-2", status: "succeeded", output_artifacts: [] },
    scope: "mine",
  });
  assert.equal(incompleteArtifact.stage, "artifact-evidence-missing");
  assert.equal(incompleteArtifact.progress, 100);

  const reset = generationTaskRuntimeReducer(incompleteArtifact, {
    type: "reset-workspace",
    formError: "恢复原提交",
  });
  assert.equal(reset.currentTaskId, "");
  assert.equal(reset.currentTask, null);
  assert.equal(reset.stage, "idle");
  assert.equal(reset.progress, 0);
  assert.equal(reset.formError, "恢复原提交");
});

test("all three Studio demo fixture groups remain behind production compile-time branches", () => {
  assert.match(demoSource, /DEVELOPMENT_DEMO_MODEL_RESPONSES = import\.meta\.env\.PROD \? \[\] : \[/);
  assert.match(demoSource, /DEVELOPMENT_DEMO_HISTORY_TASKS = import\.meta\.env\.PROD \? \[\] : \[/);
  assert.match(demoSource, /DEVELOPMENT_DEMO_ARTWORKS = import\.meta\.env\.PROD \? \[\] : \[/);
  assert.doesNotMatch(appSource, /id: "demo-task-complete"/);
  assert.doesNotMatch(appSource, /id: "cinemox"/);
});

test("pending generation evidence survives an uncertain retry but rejects drift", () => {
  const storage = memoryStorage();
  const pending = validPendingCreate();
  rememberPendingCreate("company-1", pending, storage);
  assert.deepEqual(readPendingCreate("company-1", storage), pending);
  assert.equal(readPendingCreate("company-2", storage), null);

  const storedKey = "ai-video.pending-create:company-1";
  const tampered = { ...pending, requestPayload: { ...pending.requestPayload, prompt: "changed" } };
  storage.setItem(storedKey, JSON.stringify(tampered));
  assert.equal(readPendingCreate("company-1", storage), null);

  storage.setItem(storedKey, JSON.stringify({
    ...pending,
    requestPayload: { ...pending.requestPayload, unknown: true },
  }));
  assert.equal(readPendingCreate("company-1", storage), null);
});

test("restored pending assets stay grouped by the exact media contract", () => {
  assert.deepEqual(
    filesFromPendingRequest({
      assets: [
        { asset_id: "image-1", media_type: "image" },
        { asset_id: "video-1", media_type: "video" },
        { asset_id: "ignored", media_type: "document" },
      ],
    }),
    {
      image: [{
        id: "image-1",
        asset_id: "image-1",
        media_type: "image",
        original_filename: "已上传图片",
        status: "active",
      }],
      video: [{
        id: "video-1",
        asset_id: "video-1",
        media_type: "video",
        original_filename: "已上传视频",
        status: "active",
      }],
      audio: [],
    },
  );
});

test("collection compatibility fallbacks preserve filters and artifact evidence", async () => {
  const taskClient = {
    listTaskHistory: async () => {
      throw new PlatformApiError("missing", { status: 404 });
    },
    listTasks: async () => [
      { id: "queued", status: "queued" },
      { id: "done", status: "succeeded" },
    ],
  };
  const history = await fetchTaskHistoryPage(
    taskClient,
    { page: 2, page_size: 24, status: "queued" },
  );
  assert.deepEqual(history.items.map((task) => task.id), ["queued"]);
  assert.equal(history.legacy, true);

  const artworkClient = {
    listArtworks: async () => {
      throw new PlatformApiError("missing", { status: 405 });
    },
    listTasks: async () => [{
      id: "task-1",
      status: "succeeded",
      request_payload: { output_count: 1 },
      output_artifacts: [{ asset_id: "output-1", media_type: "video" }],
    }],
  };
  const artworks = await fetchArtworkPage(
    artworkClient,
    { page: 1, page_size: 24, media_type: "video", downloaded: "" },
  );
  assert.equal(artworks.legacy, true);
  assert.equal(artworks.items[0].asset_id, "output-1");
});

test("identity discovery binds a personal response to the server-authorized workspace", async () => {
  const result = await discoverStudioIdentity({
    activeCompanyId: "",
    runtimePlatformConfig: { baseUrl: "https://platform.invalid", companyId: "" },
    liveClient: {
      getSessionSurfaces: async () => ({
        account_type: "personal",
        user: { id: "user-1", email: "user@example.test", display_name: "用户" },
        personal: {
          workspace_id: "personal-1",
          label: "个人空间",
          capabilities: { generation: true, tasks: true },
        },
      }),
      getPersonalMe: async () => ({ user_id: "user-1", display_name: "用户" }),
    },
  });
  assert.equal(result.accountKind, "personal");
  assert.equal(result.personalIdentity.workspace_id, "personal-1");
  assert.equal(result.personalIdentity.company_id, null);
  assert.equal(result.personalIdentity.is_platform_admin, false);
  assert.equal(result.companyIdentity, null);
  assert.equal(result.platformIdentity, null);
  assert.equal(result.identityError, "");
});

test("identity discovery never falls through a suspended company or authentication failure", async () => {
  let lowerScopeCalls = 0;
  const suspended = await discoverStudioIdentity({
    activeCompanyId: "company-1",
    runtimePlatformConfig: { baseUrl: "https://platform.invalid", companyId: "company-1" },
    liveClient: {
      getSessionSurfaces: async () => ({
        account_type: "company",
        companies: [{ company_id: "company-1", name: "暂停企业", status: "suspended" }],
      }),
      getPersonalMe: async () => {
        lowerScopeCalls += 1;
        return { user_id: "must-not-run" };
      },
      getPlatformAdminMe: async () => {
        lowerScopeCalls += 1;
        return { user_id: "must-not-run" };
      },
    },
  });
  assert.equal(suspended.accountKind, "company_unavailable");
  assert.equal(suspended.companyIdentity, null);
  assert.equal(suspended.personalIdentity, null);
  assert.equal(suspended.platformIdentity, null);
  assert.match(suspended.identityError, /不能回退到个人创作空间/);
  assert.equal(lowerScopeCalls, 0);

  const authError = new PlatformApiError("expired", { status: 401 });
  await assert.rejects(
    discoverStudioIdentity({
      activeCompanyId: "company-1",
      runtimePlatformConfig: { baseUrl: "https://platform.invalid", companyId: "company-1" },
      clientFactory: () => ({
        getCompanyMe: async () => {
          throw authError;
        },
      }),
      liveClient: {
        getSessionSurfaces: async () => ({
          account_type: "company",
          companies: [{ company_id: "company-1", name: "企业", status: "active" }],
        }),
      },
    }),
    (error) => error === authError,
  );
});
