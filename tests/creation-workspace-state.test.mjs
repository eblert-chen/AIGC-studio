import assert from "node:assert/strict";
import test from "node:test";

import {
  attachWorkbenchTask,
  createWorkbenchEntry,
  emptyCreationWorkspace,
  moveWorkbenchEntry,
  readCreationWorkspace,
  sanitizeCreationDraft,
  selectLineageShot,
  selectWorkbenchEntry,
  setLineageBranchArchived,
  setLineageCanonicalTake,
  updateWorkbenchEntry,
  writeCreationWorkspace,
} from "../src/app/creationWorkspaces.js";

const SCOPE = "user:alice|company:first";
const NOW = "2026-08-31T00:00:00.000Z";
const clone = (value) => JSON.parse(JSON.stringify(value));
function memoryStorage() {
  const values = new Map();
  const writes = [];
  return {
    values, writes,
    getItem(key) { return values.get(key) ?? null; },
    setItem(key, value) { writes.push({ key, value }); values.set(key, value); },
  };
}
function fixture(kind = "notebook", entryId = "scene-1", draft = {}) {
  return createWorkbenchEntry(emptyCreationWorkspace(SCOPE), kind, { id: entryId, now: NOW, draft });
}
function persistRaw(raw, scopeKey = SCOPE) {
  const storage = memoryStorage();
  assert.equal(writeCreationWorkspace(emptyCreationWorkspace(scopeKey), storage).ok, true);
  storage.values.set([...storage.values.keys()][0], typeof raw === "string" ? raw : JSON.stringify(raw));
  return storage;
}

test("empty contexts contain a blank quick draft and no inferred workbench entries", () => {
  const state = emptyCreationWorkspace(SCOPE);
  assert.deepEqual(state, {
    version: 2, scopeKey: SCOPE, revision: 0,
    quick: { draft: {
      modelId: "", generationMode: "", prompt: "", ratio: "", resolution: "", duration: null,
      outputCount: null, faceEnabled: false, files: { image: [], video: [], audio: [] },
    }, panel: null, mobileOpen: false },
    active: { console: "", notebook: "", canvas: "" },
    lineage: { activeShotId: "", shots: [] }, entries: [],
  });
  const storage = memoryStorage();
  assert.deepEqual(readCreationWorkspace(SCOPE, storage), { state, notice: "" });
  assert.equal(storage.writes.length, 0, "Reading an empty store must not create persisted records");
});

test("version 1 local documents migrate to version 2 without inventing lineage edges or losing task bindings", () => {
  const draft = sanitizeCreationDraft({ prompt: "旧版草稿", modelId: "model-old" });
  const entry = (overrides) => ({
    id: "legacy-scene", kind: "notebook", title: "旧场次", order: 0,
    parentId: "", relation: "", x: 0, y: 0, draft, taskIds: [], selectedTaskId: "",
    createdAt: NOW, updatedAt: NOW, ...overrides,
  });
  const legacy = {
    version: 1, scopeKey: SCOPE, revision: 7,
    quick: { draft: sanitizeCreationDraft({ prompt: "旧快捷草稿" }), panel: "model", mobileOpen: true },
    active: { console: "", notebook: "legacy-scene", canvas: "legacy-child" },
    entries: [
      entry({}),
      entry({ id: "legacy-root", kind: "canvas", title: "明确根方向", order: 0,
        taskIds: ["task-root"], selectedTaskId: "task-root" }),
      entry({ id: "legacy-child", kind: "canvas", title: "明确旧分支", order: 1,
        parentId: "legacy-root", relation: "settings" }),
      entry({ id: "legacy-root-two", kind: "canvas", title: "另一个独立根方向", order: 2 }),
    ],
  };
  const storage = persistRaw(legacy);
  const original = [...storage.values.values()][0];
  const writesBeforeRead = storage.writes.length;
  const loaded = readCreationWorkspace(SCOPE, storage);
  assert.equal(loaded.notice, "");
  assert.equal(storage.writes.length, writesBeforeRead, "Migration reads never rewrite local storage");
  assert.equal([...storage.values.values()][0], original);
  assert.equal(loaded.state.version, 2);
  assert.equal(loaded.state.revision, 7);
  assert.deepEqual(loaded.state.entries.find((item) => item.id === "legacy-root").taskIds, ["task-root"]);
  assert.equal(loaded.state.entries.find((item) => item.id === "legacy-root").selectedTaskId, "",
    "A v1 task-only selection is not promoted to an exact Take");
  assert.equal(loaded.state.entries.find((item) => item.id === "legacy-root").selectedArtifactId, "");
  assert.equal(loaded.state.entries.find((item) => item.id === "legacy-child").parentId, "legacy-root");
  assert.equal(loaded.state.lineage.shots.length, 2, "Independent roots become separate shots, never inferred edges");
  assert.equal(loaded.state.entries.find((item) => item.id === "legacy-root").shotId,
    loaded.state.entries.find((item) => item.id === "legacy-child").shotId);
  assert.notEqual(loaded.state.entries.find((item) => item.id === "legacy-root").shotId,
    loaded.state.entries.find((item) => item.id === "legacy-root-two").shotId);
  assert.equal(loaded.state.lineage.activeShotId,
    loaded.state.entries.find((item) => item.id === "legacy-child").shotId);

  const changed = updateWorkbenchEntry(loaded.state, "legacy-child", { title: "迁移后修改" });
  assert.equal(writeCreationWorkspace(changed, storage).ok, true);
  const persisted = JSON.parse([...storage.values.values()][0]);
  assert.equal(persisted.version, 2);
  assert.ok(persisted.lineage);
  assert.equal(persisted.entries.find((item) => item.id === "legacy-child").title, "迁移后修改");

  const conflictStorage = persistRaw(legacy);
  const migrated = readCreationWorkspace(SCOPE, conflictStorage).state;
  const conflictKey = [...conflictStorage.values.keys()][0];
  const external = clone(legacy);
  external.revision += 1;
  external.quick.draft.prompt = "另一页面先改了旧版草稿";
  const externalBytes = JSON.stringify(external);
  conflictStorage.values.set(conflictKey, externalBytes);
  const localChange = updateWorkbenchEntry(migrated, "legacy-child", { title: "本页过期修改" });
  assert.equal(writeCreationWorkspace(localChange, conflictStorage).ok, false,
    "An explicit migration write still honors the exact read baseline");
  assert.equal(conflictStorage.getItem(conflictKey), externalBytes);
});

test("quick and advanced drafts, panel and explicit task selection survive a storage refresh", () => {
  const storage = memoryStorage();
  let { state } = fixture("notebook", "scene-1", { prompt: "第一场\n雨夜街道。", modelId: "model-id" });
  state = attachWorkbenchTask(state, "scene-1", "task-1");
  state = updateWorkbenchEntry(state, "scene-1", { selectedTaskId: "task-1", selectedArtifactId: "artifact-1" });
  state = {
    ...state, revision: state.revision + 1,
    quick: { draft: sanitizeCreationDraft({ prompt: "快捷草稿另存。" }), panel: "references", mobileOpen: true },
  };
  assert.deepEqual(writeCreationWorkspace(state, storage), { ok: true, notice: "" });
  const loaded = readCreationWorkspace(SCOPE, storage);
  assert.equal(loaded.notice, "");
  assert.deepEqual(loaded.state, state);
  loaded.state.entries[0].draft.prompt = "只改读回副本";
  assert.equal(readCreationWorkspace(SCOPE, storage).state.entries[0].draft.prompt, "第一场\n雨夜街道。");
});

test("all supported quick disclosure states round-trip, unrelated state is rejected", () => {
  for (const panel of [null, "references", "specs", "model", "recipe", "readiness"]) {
    const state = emptyCreationWorkspace(SCOPE);
    state.quick.panel = panel;
    const storage = memoryStorage();
    assert.equal(writeCreationWorkspace(state, storage).ok, true);
    assert.equal(readCreationWorkspace(SCOPE, storage).state.quick.panel, panel);
  }
  const state = emptyCreationWorkspace(SCOPE);
  state.quick.panel = "auth-settings";
  assert.equal(writeCreationWorkspace(state, memoryStorage()).ok, false);
});

test("user/workspace scope keys isolate reads and writes without delimiter collisions", () => {
  const storage = memoryStorage();
  const scopes = [SCOPE, "user:bob|company:first", "user:alice|company:second", "user:alice%7Ccompany:first"];
  for (const [index, scopeKey] of scopes.entries()) {
    const { state } = createWorkbenchEntry(emptyCreationWorkspace(scopeKey), "console", {
      id: `shot-${index}`, now: NOW, draft: { prompt: `scope ${index}` },
    });
    assert.equal(writeCreationWorkspace(state, storage).ok, true);
  }
  assert.equal(storage.values.size, scopes.length);
  for (const [index, scopeKey] of scopes.entries()) {
    assert.equal(readCreationWorkspace(scopeKey, storage).state.entries[0].draft.prompt, `scope ${index}`);
  }
  assert.deepEqual(readCreationWorkspace("user:anonymous|company:third", storage).state.entries, []);
});

test("foreign-scope persisted payload fails closed and its exact original bytes are retained", () => {
  const { state } = fixture();
  state.scopeKey = "user:bob|company:first";
  const storage = persistRaw(state);
  const original = [...storage.values.values()][0];
  const loaded = readCreationWorkspace(SCOPE, storage);
  assert.equal(loaded.state.scopeKey, SCOPE);
  assert.equal(loaded.state.entries.length, 0);
  assert.ok(loaded.notice);
  assert.equal(writeCreationWorkspace(emptyCreationWorkspace(SCOPE), storage).ok, false);
  assert.equal([...storage.values.values()][0], original);
});

test("draft sanitizer stores only editable values and minimal asset references", () => {
  const raw = {
    modelId: "model-id", generationMode: "image_to_video", prompt: "镜头缓慢推进。", ratio: "16:9",
    resolution: "1080p", duration: 5, outputCount: 1, faceEnabled: true,
    apiKey: "never-persist-key", cookies: "never-persist-cookie", modelCatalog: [{ secret: "catalog" }],
    capability: { grant: "not-authority" },
    files: {
      image: [{ id: "asset-1", media_type: "image", original_filename: "参考.png", status: "active",
        signed_url: "https://example.invalid/private?token=secret", previewUrl: "blob:private",
        owner: { email: "private@example.invalid" } }],
      video: [], audio: [],
    },
  };
  const cleaned = sanitizeCreationDraft(raw);
  assert.deepEqual(cleaned.files.image, [
    { asset_id: "asset-1", media_type: "image", original_filename: "参考.png", status: "active" },
  ]);
  assert.deepEqual(Object.keys(cleaned).sort(), [
    "modelId", "generationMode", "prompt", "ratio", "resolution", "duration", "outputCount", "faceEnabled", "files",
  ].sort());
  const storage = memoryStorage();
  const { state } = fixture("console", "shot-1", raw);
  assert.equal(writeCreationWorkspace(state, storage).ok, true);
  assert.doesNotMatch([...storage.values.values()][0], /secret|private|cookie|apiKey|catalog|signed_url|previewUrl|blob:/);
  assert.equal(raw.files.image[0].signed_url, "https://example.invalid/private?token=secret", "Caller data is not mutated");
  cleaned.files.image[0].original_filename = "other";
  assert.equal(raw.files.image[0].original_filename, "参考.png");
});

test("missing asset status remains unknown instead of becoming active", () => {
  const result = sanitizeCreationDraft({ files: { image: [{ asset_id: "asset-1", media_type: "image" }] } });
  assert.equal(result.files.image[0].status, "");
});

test("new workbench contexts never inherit quick or parent drafts implicitly", () => {
  let state = emptyCreationWorkspace(SCOPE);
  state.quick.draft.prompt = "不要自动带入";
  let created = createWorkbenchEntry(state, "canvas", { id: "parent", now: NOW, draft: { prompt: "父节点" } });
  created = createWorkbenchEntry(created.state, "canvas", { id: "child", now: NOW, parentId: "parent", relation: "settings" });
  assert.equal(created.entry.draft.prompt, "");
  assert.equal(created.state.quick.draft.prompt, "不要自动带入");
  assert.equal(created.state.entries.find((entry) => entry.id === "parent").draft.prompt, "父节点");
  const draft = { prompt: "显式传入", files: { image: [], video: [], audio: [] } };
  const imported = createWorkbenchEntry(created.state, "notebook", { id: "scene", now: NOW, draft, relation: "import" });
  draft.prompt = "后来改的";
  assert.equal(imported.entry.draft.prompt, "显式传入");
  assert.equal(imported.entry.parentId, "");
});

test("scene task versions append idempotently without creating scenes or choosing takes", () => {
  let { state } = fixture();
  const initial = clone(state);
  state = attachWorkbenchTask(state, "scene-1", "task-a");
  assert.equal(state.entries[0].selectedTaskId, "", "Completion is not user approval/selection");
  state = attachWorkbenchTask(state, "scene-1", "task-b");
  assert.equal(state.entries.length, 1);
  assert.deepEqual(state.entries[0].taskIds, ["task-a", "task-b"]);
  assert.equal(state.revision, initial.revision + 2);
  assert.equal(attachWorkbenchTask(state, "scene-1", "task-a"), state);
  state = updateWorkbenchEntry(state, "scene-1", { selectedTaskId: "task-a", selectedArtifactId: "artifact-a" });
  const selected = state;
  assert.equal(attachWorkbenchTask(state, "scene-1", "task-b"), selected);
  state = attachWorkbenchTask(state, "scene-1", "task-c");
  assert.equal(state.entries[0].selectedTaskId, "task-a", "New results do not change an explicit chosen take");
  assert.equal(state.entries[0].selectedArtifactId, "artifact-a");
  assert.deepEqual(initial.entries[0].taskIds, []);
});

test("task snapshots and unattached selections are rejected, only caller-supplied IDs are accepted", () => {
  let { state } = fixture();
  assert.throws(() => attachWorkbenchTask(state, "scene-1", { id: "task-1", status: "succeeded" }), TypeError);
  state = attachWorkbenchTask(state, "scene-1", "task-1");
  assert.throws(() => updateWorkbenchEntry(state, "scene-1", {
    selectedTaskId: "task-outside", selectedArtifactId: "artifact-outside",
  }), TypeError);
  assert.throws(() => updateWorkbenchEntry(state, "scene-1", { selectedTaskId: "task-1" }), /同时绑定/);
  assert.throws(() => updateWorkbenchEntry(state, "scene-1", { selectedArtifactId: "artifact-1" }), /同时绑定/);
  assert.throws(() => attachWorkbenchTask(state, "missing", "task-1"), TypeError);
  assert.throws(() => attachWorkbenchTask(state, "scene-1", "https://private.invalid/task"), TypeError);
});

test("one Platform task binds to exactly one authoring context", () => {
  let state = fixture("notebook", "scene-1").state;
  state = createWorkbenchEntry(state, "console", { id: "shot-1", now: NOW }).state;
  state = attachWorkbenchTask(state, "scene-1", "task-one-owner");
  const before = clone(state);
  assert.throws(() => attachWorkbenchTask(state, "shot-1", "task-one-owner"), /多个工作项/);
  assert.deepEqual(state, before);

  const injected = clone(state);
  injected.entries.find((entry) => entry.id === "shot-1").taskIds.push("task-one-owner");
  const storage = persistRaw(injected);
  const original = [...storage.values.values()][0];
  const read = readCreationWorkspace(SCOPE, storage);
  assert.ok(read.notice);
  assert.equal(read.state.entries.length, 0);
  assert.equal([...storage.values.values()][0], original);
});

test("lineage branches require an exact parent task reference and explicit human intent", () => {
  let state = fixture("canvas", "root", { prompt: "远景" }).state;
  state = attachWorkbenchTask(state, "root", "task-root");
  const sourceDraft = sanitizeCreationDraft({ prompt: "远景", modelId: "model-1" });
  const created = createWorkbenchEntry(state, "canvas", {
    id: "camera-branch", parentId: "root", relation: "lineage", now: NOW,
    sourceTaskId: "task-root", sourceArtifactId: "artifact-root", sourceDraft,
    branchKind: "camera", branchReason: "改成缓慢推近",
  });
  assert.equal(created.entry.parentId, "root");
  assert.equal(created.entry.sourceTaskId, "task-root");
  assert.equal(created.entry.sourceArtifactId, "artifact-root");
  assert.equal(created.entry.branchKind, "camera");
  assert.equal(created.entry.sourceDraft.prompt, "远景");
  assert.equal(created.entry.shotId, created.state.entries.find((entry) => entry.id === "root").shotId);

  for (const overrides of [
    { sourceTaskId: "task-not-owned" },
    { sourceArtifactId: "" },
    { branchKind: "" },
    { branchReason: "   " },
    { sourceDraft: null },
  ]) {
    assert.throws(() => createWorkbenchEntry(state, "canvas", {
      id: `bad-${Object.keys(overrides)[0]}`, parentId: "root", relation: "lineage", now: NOW,
      sourceTaskId: "task-root", sourceArtifactId: "artifact-root", sourceDraft,
      branchKind: "camera", branchReason: "明确意图", ...overrides,
    }), TypeError);
  }
});

test("version 2 keeps the exact artifact selection when one task returns multiple outputs", () => {
  const storage = memoryStorage();
  let state = fixture("canvas", "root", { prompt: "同一任务生成两个候选画面" }).state;
  state = attachWorkbenchTask(state, "root", "task-multi-output");

  state = updateWorkbenchEntry(state, "root", {
    selectedTaskId: "task-multi-output",
    selectedArtifactId: "artifact-output-a",
  });
  state = updateWorkbenchEntry(state, "root", {
    selectedTaskId: "task-multi-output",
    selectedArtifactId: "artifact-output-b",
  });
  assert.deepEqual(
    [state.entries[0].selectedTaskId, state.entries[0].selectedArtifactId],
    ["task-multi-output", "artifact-output-b"],
    "Selecting another output of the same task must change the artifact half of the Take identity",
  );

  const sourceTake = {
    taskId: state.entries[0].selectedTaskId,
    artifactId: state.entries[0].selectedArtifactId,
    draft: state.entries[0].draft,
  };
  const branch = createWorkbenchEntry(state, "canvas", {
    id: "branch-from-output-b",
    parentId: "root",
    relation: "lineage",
    now: NOW,
    sourceTaskId: sourceTake.taskId,
    sourceArtifactId: sourceTake.artifactId,
    sourceDraft: sourceTake.draft,
    branchKind: "composition",
    branchReason: "只调整画面构图",
  });
  state = branch.state;
  assert.deepEqual(
    [branch.entry.sourceTaskId, branch.entry.sourceArtifactId],
    ["task-multi-output", "artifact-output-b"],
    "A branch records the exact source output, not only its owning task",
  );

  state = setLineageCanonicalTake(state, "root", "task-multi-output", "artifact-output-b", NOW);
  assert.deepEqual(
    [
      state.lineage.shots[0].canonicalEntryId,
      state.lineage.shots[0].canonicalTaskId,
      state.lineage.shots[0].canonicalArtifactId,
    ],
    ["root", "task-multi-output", "artifact-output-b"],
  );
  assert.equal(writeCreationWorkspace(state, storage).ok, true);
  const restored = readCreationWorkspace(SCOPE, storage).state;
  assert.deepEqual(
    [
      restored.entries.find((entry) => entry.id === "root").selectedTaskId,
      restored.entries.find((entry) => entry.id === "root").selectedArtifactId,
      restored.entries.find((entry) => entry.id === "branch-from-output-b").sourceTaskId,
      restored.entries.find((entry) => entry.id === "branch-from-output-b").sourceArtifactId,
      restored.lineage.shots[0].canonicalTaskId,
      restored.lineage.shots[0].canonicalArtifactId,
    ],
    [
      "task-multi-output", "artifact-output-b",
      "task-multi-output", "artifact-output-b",
      "task-multi-output", "artifact-output-b",
    ],
  );
});

test("canonical take, shot selection and archive transitions remain explicit and fail closed", () => {
  let state = fixture("canvas", "root").state;
  state = attachWorkbenchTask(state, "root", "task-root");
  state = setLineageCanonicalTake(state, "root", "task-root", "artifact-root", NOW);
  const shot = state.lineage.shots[0];
  assert.deepEqual(
    [shot.canonicalEntryId, shot.canonicalTaskId, shot.canonicalArtifactId],
    ["root", "task-root", "artifact-root"],
  );
  assert.equal(state.entries[0].selectedTaskId, "task-root");
  assert.equal(state.entries[0].selectedArtifactId, "artifact-root");
  assert.throws(() => setLineageCanonicalTake(state, "root", "task-outside", "artifact", NOW), TypeError);

  state = createWorkbenchEntry(state, "canvas", {
    id: "branch", parentId: "root", relation: "settings", now: NOW,
  }).state;
  state = createWorkbenchEntry(state, "canvas", {
    id: "branch-child", parentId: "branch", relation: "settings", now: NOW,
  }).state;
  assert.throws(() => setLineageBranchArchived(state, "root", true, NOW), /初始方向/);
  state = selectWorkbenchEntry(state, "canvas", "root");
  state = setLineageBranchArchived(state, "branch", true, NOW);
  assert.equal(state.entries.find((entry) => entry.id === "branch").archived, true);
  assert.equal(state.entries.find((entry) => entry.id === "branch-child").archived, true);
  assert.equal(state.active.canvas, "root");
  state = setLineageBranchArchived(state, "branch-child", false, NOW);
  assert.equal(state.entries.find((entry) => entry.id === "branch").archived, false);
  assert.equal(state.entries.find((entry) => entry.id === "branch-child").archived, false);
  assert.equal(selectLineageShot(state, shot.id), state, "Selecting the current shot is idempotent");
});

test("archiving a lineage branch cascades to descendants and restoring its root revives the whole subtree", () => {
  let state = fixture("canvas", "root", { prompt: "初始方向" }).state;
  state = attachWorkbenchTask(state, "root", "task-root");
  state = createWorkbenchEntry(state, "canvas", {
    id: "branch", parentId: "root", relation: "lineage", now: NOW,
    sourceTaskId: "task-root", sourceArtifactId: "artifact-root",
    sourceDraft: state.entries[0].draft,
    branchKind: "camera", branchReason: "改为低机位",
  }).state;
  state = attachWorkbenchTask(state, "branch", "task-branch");
  state = createWorkbenchEntry(state, "canvas", {
    id: "branch-child", parentId: "branch", relation: "lineage", now: NOW,
    sourceTaskId: "task-branch", sourceArtifactId: "artifact-branch",
    sourceDraft: state.entries.find((entry) => entry.id === "branch").draft,
    branchKind: "performance", branchReason: "保留机位并调整动作",
  }).state;
  state = attachWorkbenchTask(state, "branch-child", "task-branch-child");
  state = setLineageCanonicalTake(state, "branch-child", "task-branch-child", "artifact-child", NOW);
  assert.throws(
    () => setLineageBranchArchived(state, "branch", true, NOW),
    /主线所在支线不能归档/,
    "A subtree containing the director mainline must remain visible",
  );

  state = setLineageCanonicalTake(state, "root", "task-root", "artifact-root", NOW);
  state = selectWorkbenchEntry(state, "canvas", "branch-child");
  state = setLineageBranchArchived(state, "branch", true, NOW);
  assert.equal(state.entries.find((entry) => entry.id === "branch").archived, true);
  assert.equal(state.entries.find((entry) => entry.id === "branch-child").archived, true);
  assert.equal(state.active.canvas, "root", "Archiving the active subtree returns selection to its live parent");
  assert.deepEqual(
    [state.lineage.shots[0].canonicalEntryId, state.lineage.shots[0].canonicalTaskId, state.lineage.shots[0].canonicalArtifactId],
    ["root", "task-root", "artifact-root"],
  );

  state = setLineageBranchArchived(state, "branch", false, NOW);
  assert.equal(state.entries.find((entry) => entry.id === "branch").archived, false);
  assert.equal(state.entries.find((entry) => entry.id === "branch-child").archived, false);
  assert.equal(state.active.canvas, "root", "Restore does not silently change the director's current direction");
});

test("a duplicate task import across workbenches fails atomically and keeps its original owner", () => {
  let state = fixture("canvas", "canvas-origin").state;
  state = attachWorkbenchTask(state, "canvas-origin", "task-imported-once");
  state = createWorkbenchEntry(state, "notebook", {
    id: "scene-import-target", now: NOW, relation: "import", draft: { prompt: "导入任务" },
  }).state;
  const before = clone(state);

  assert.throws(
    () => attachWorkbenchTask(state, "scene-import-target", "task-imported-once"),
    /多个工作项/,
  );
  assert.deepEqual(state, before, "A rejected duplicate import must not detach or move the original task");
  assert.deepEqual(state.entries.find((entry) => entry.id === "canvas-origin").taskIds, ["task-imported-once"]);
  assert.deepEqual(state.entries.find((entry) => entry.id === "scene-import-target").taskIds, []);
  assert.equal(attachWorkbenchTask(state, "canvas-origin", "task-imported-once"), state,
    "Retrying the same import into its original owner is idempotent");
});

test("manual canvas coordinates round-trip without altering task, artifact or lineage identity", () => {
  const storage = memoryStorage();
  let state = fixture("canvas", "positioned-direction").state;
  state = attachWorkbenchTask(state, "positioned-direction", "task-positioned");
  state = updateWorkbenchEntry(state, "positioned-direction", {
    selectedTaskId: "task-positioned",
    selectedArtifactId: "artifact-positioned",
    x: 481.25,
    y: 196.5,
  });
  assert.equal(writeCreationWorkspace(state, storage).ok, true);
  const restored = readCreationWorkspace(SCOPE, storage).state;
  const entry = restored.entries[0];
  assert.deepEqual(
    [entry.x, entry.y, entry.selectedTaskId, entry.selectedArtifactId, entry.shotId],
    [481.25, 196.5, "task-positioned", "artifact-positioned", state.entries[0].shotId],
  );
  assert.throws(() => updateWorkbenchEntry(restored, entry.id, { x: Number.POSITIVE_INFINITY }), /画布坐标无效/);
  assert.throws(() => updateWorkbenchEntry(restored, entry.id, { y: 1_000_001 }), /画布坐标无效/);
});

test("entry changes are immutable, merge partial drafts and keep other contexts intact", () => {
  const { state } = fixture("notebook", "scene-1", { modelId: "model-id", prompt: "原稿", duration: 5 });
  const before = clone(state);
  const next = updateWorkbenchEntry(state, "scene-1", { title: "新场次名", draft: { prompt: "修改后的原稿" } });
  assert.deepEqual(state, before);
  assert.equal(next.entries[0].draft.modelId, "model-id");
  assert.equal(next.entries[0].draft.duration, 5);
  assert.equal(next.entries[0].draft.prompt, "修改后的原稿");
  assert.deepEqual(next.quick, state.quick);
  assert.equal(next.revision, state.revision + 1);
  assert.equal(updateWorkbenchEntry(next, "scene-1", { title: "新场次名" }), next);
});

test("entry identity, parent, version inventory and ordering cannot be mutated through a patch", () => {
  const { state } = fixture();
  for (const patch of [
    { id: "changed" }, { kind: "canvas" }, { parentId: "forged" }, { taskIds: ["forged"] },
    { order: 9 }, { relation: "settings" }, { createdAt: NOW }, { secrets: "do-not-store" },
  ]) assert.throws(() => updateWorkbenchEntry(state, "scene-1", patch), TypeError);
  assert.throws(() => updateWorkbenchEntry(state, "missing", { title: "name" }), TypeError);
});

test("updating one media group preserves the other groups and rejects an invalid files patch", () => {
  const { state } = fixture("console", "shot-1", { files: {
    image: [{ asset_id: "image-1", media_type: "image" }],
    video: [{ asset_id: "video-1", media_type: "video" }],
  } });
  const next = updateWorkbenchEntry(state, "shot-1", { draft: { files: { image: [] } } });
  assert.deepEqual(next.entries[0].draft.files.image, []);
  assert.deepEqual(next.entries[0].draft.files.video, state.entries[0].draft.files.video);
  assert.throws(() => updateWorkbenchEntry(state, "shot-1", { draft: { files: null } }), TypeError);
});

test("active selection is per-workbench, fails on the wrong kind, and a repeat is a no-op", () => {
  let { state } = fixture("notebook", "scene-1");
  state = createWorkbenchEntry(state, "notebook", { id: "scene-2", now: NOW }).state;
  state = createWorkbenchEntry(state, "console", { id: "shot-1", now: NOW }).state;
  const next = selectWorkbenchEntry(state, "notebook", "scene-1");
  assert.deepEqual(next.active, { console: "shot-1", notebook: "scene-1", canvas: "" });
  assert.equal(next.revision, state.revision + 1);
  assert.equal(selectWorkbenchEntry(next, "notebook", "scene-1"), next);
  assert.throws(() => selectWorkbenchEntry(next, "canvas", "scene-1"), TypeError);
  assert.throws(() => selectWorkbenchEntry(next, "notebook", "missing"), TypeError);
  assert.throws(() => selectWorkbenchEntry(next, "__proto__", "scene-1"), TypeError);
});

test("notebook movement changes order without identity, versions, chosen take or active-scene drift", () => {
  let { state } = fixture("notebook", "scene-1");
  state = attachWorkbenchTask(state, "scene-1", "task-first");
  state = updateWorkbenchEntry(state, "scene-1", { selectedTaskId: "task-first", selectedArtifactId: "artifact-first" });
  state = createWorkbenchEntry(state, "console", { id: "shot-1", now: NOW }).state;
  state = createWorkbenchEntry(state, "notebook", { id: "scene-2", now: NOW }).state;
  state = createWorkbenchEntry(state, "notebook", { id: "scene-3", now: NOW }).state;
  const moved = moveWorkbenchEntry(state, "scene-2", -1);
  assert.deepEqual(moved.entries.filter((entry) => entry.kind === "notebook").map((entry) => entry.id), ["scene-2", "scene-1", "scene-3"]);
  assert.deepEqual(moved.entries.filter((entry) => entry.kind === "notebook").map((entry) => entry.order), [0, 1, 2]);
  const first = moved.entries.find((entry) => entry.id === "scene-1");
  assert.deepEqual(first.taskIds, ["task-first"]);
  assert.equal(first.selectedTaskId, "task-first");
  assert.equal(first.selectedArtifactId, "artifact-first");
  assert.equal(first.createdAt, NOW);
  assert.deepEqual(moved.active, state.active);
  assert.deepEqual(moved.entries.find((entry) => entry.kind === "console"), state.entries.find((entry) => entry.kind === "console"));
  assert.equal(moveWorkbenchEntry(moved, "scene-2", -1), moved);
  assert.equal(moveWorkbenchEntry(moved, "scene-3", 1), moved);
  assert.throws(() => moveWorkbenchEntry(moved, "scene-1", "up"), TypeError);
  assert.deepEqual(moveWorkbenchEntry(moved, "scene-2", 1).entries.map((entry) => entry.id), state.entries.map((entry) => entry.id));
});

test("same-time and reordered canvas records have no edges without an explicit parent relationship", () => {
  let { state } = fixture("canvas", "root-a");
  state = createWorkbenchEntry(state, "canvas", { id: "root-b", now: NOW }).state;
  state = createWorkbenchEntry(state, "canvas", { id: "root-c", now: "2026-08-30T00:00:00.000Z", relation: "import" }).state;
  state = moveWorkbenchEntry(state, "root-c", -1);
  for (const entry of state.entries) assert.equal(entry.parentId, "");
  const child = createWorkbenchEntry(state, "canvas", { id: "child", now: NOW, parentId: "root-b", relation: "settings" });
  assert.equal(child.entry.parentId, "root-b");
  assert.equal(child.entry.relation, "settings");
  assert.equal(child.state.entries.filter((entry) => entry.parentId).length, 1);
});

test("unknown, cross-kind and incomplete parent relations are rejected atomically", () => {
  let { state } = fixture("notebook", "scene-1");
  state = createWorkbenchEntry(state, "canvas", { id: "parent", now: NOW }).state;
  const before = clone(state);
  for (const [type, parentId, relation] of [
    ["canvas", "missing", "settings"], ["canvas", "scene-1", "settings"],
    ["notebook", "parent", "settings"], ["canvas", "parent", ""],
    ["canvas", "", "settings"], ["canvas", "parent", "import"],
    ["canvas", null, ""], ["canvas", "", null],
  ]) assert.throws(() => createWorkbenchEntry(state, type, { id: "child", now: NOW, parentId, relation }), TypeError);
  assert.throws(() => createWorkbenchEntry(state, "canvas", { id: "parent", now: NOW }), TypeError);
  assert.throws(() => createWorkbenchEntry(state, "canvas", { id: null, now: NOW }), TypeError);
  assert.throws(() => createWorkbenchEntry(state, "constructor"), TypeError);
  assert.deepEqual(state, before);
});

test("invalid draft and asset inputs reject without coercion, trimming or silent filtering", () => {
  const invalid = [
    null, [], "text", { prompt: null }, { prompt: 42 }, { prompt: "control\u0000" },
    { duration: "5" }, { duration: 0 }, { duration: NaN }, { outputCount: -1 },
    { faceEnabled: "true" }, { modelId: { id: "model" } }, { files: [] },
    { files: { image: "no" } }, { files: { image: null } }, { files: { other: [] } },
    { files: { image: [null] } }, { files: { image: [{ asset_id: "asset-1", media_type: "video" }] } },
    { files: { image: [{ asset_id: "", media_type: "image" }] } },
    { files: { image: [{ id: "different", asset_id: "asset-1", media_type: "image" }] } },
    { files: { image: [{ asset_id: "asset-1", media_type: "image", status: "fake-approved" }] } },
    { files: { image: [{ asset_id: "asset-1", media_type: "image" }, { asset_id: "asset-1", media_type: "image" }] } },
  ];
  for (const draft of invalid) assert.throws(() => sanitizeCreationDraft(draft), TypeError);
  const sparse = new Array(1);
  assert.throws(() => sanitizeCreationDraft({ files: { image: sparse } }), TypeError);
});

test("prototype pollution, custom prototypes, accessors and dangerous nested keys never enter state", () => {
  assert.throws(() => sanitizeCreationDraft(JSON.parse('{"__proto__":{"polluted":true}}')), TypeError);
  assert.throws(() => sanitizeCreationDraft(Object.create({ prompt: "inherited" })), TypeError);
  let called = false;
  const getter = { get prompt() { called = true; return "secret"; } };
  assert.throws(() => sanitizeCreationDraft(getter), TypeError);
  assert.equal(called, false);
  assert.throws(() => sanitizeCreationDraft({ files: { image: [JSON.parse('{"asset_id":"asset-1","media_type":"image","constructor":{"secret":true}}')] } }), TypeError);
  const { state } = fixture();
  assert.throws(() => updateWorkbenchEntry(state, "scene-1", JSON.parse('{"__proto__":{"polluted":true}}')), TypeError);
  assert.equal({}.polluted, undefined);
});

test("prompt storage limit counts Unicode code points and never truncates", () => {
  const prompt = "🎬".repeat(10_000);
  assert.equal(sanitizeCreationDraft({ prompt }).prompt, prompt);
  assert.throws(() => sanitizeCreationDraft({ prompt: prompt + "场" }), RangeError);
  const { state } = fixture();
  assert.throws(() => updateWorkbenchEntry(state, "scene-1", { draft: { prompt: "字".repeat(10_001) } }), RangeError);
  assert.equal(state.entries[0].draft.prompt, "");
});

test("workspace and take ceilings fail safely without evicting old entries", () => {
  const template = fixture("notebook", "scene-template").entry;
  let state = emptyCreationWorkspace(SCOPE);
  state.entries = Array.from({ length: 500 }, (_, index) => ({ ...clone(template), id: `scene-${index}`, order: index }));
  state.active.notebook = "scene-0";
  assert.throws(() => createWorkbenchEntry(state, "console", { id: "extra" }), RangeError);
  assert.equal(state.entries.length, 500);
  state = fixture().state;
  state.entries[0].taskIds = Array.from({ length: 100 }, (_, index) => `task-${index}`);
  assert.throws(() => attachWorkbenchTask(state, "scene-1", "task-extra"), RangeError);
  assert.equal(attachWorkbenchTask(state, "scene-1", "task-99"), state);
  assert.equal(state.entries[0].taskIds.length, 100);
  state.revision = Number.MAX_SAFE_INTEGER;
  assert.throws(() => updateWorkbenchEntry(state, "scene-1", { title: "would-overflow" }), RangeError);
});

test("unavailable or throwing browser storage never reports a successful save", () => {
  for (const storage of [null, {}, {
    getItem() { throw new Error("SecurityError"); }, setItem() {},
  }]) {
    const read = readCreationWorkspace(SCOPE, storage);
    assert.ok(read.notice);
    assert.deepEqual(read.state.entries, []);
    const write = writeCreationWorkspace(emptyCreationWorkspace(SCOPE), storage);
    assert.equal(write.ok, false);
    assert.ok(write.notice);
  }
});

test("a throwing localStorage getter is caught before reading or writing", () => {
  const old = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get() { throw new Error("SecurityError"); } });
  try {
    assert.ok(readCreationWorkspace(SCOPE).notice);
    assert.equal(writeCreationWorkspace(emptyCreationWorkspace(SCOPE)).ok, false);
  } finally {
    if (old) Object.defineProperty(globalThis, "localStorage", old);
    else delete globalThis.localStorage;
  }
});

test("quota failure leaves previous stored bytes intact and a silent dropped write is not success", () => {
  const storage = memoryStorage();
  let { state } = fixture();
  assert.equal(writeCreationWorkspace(state, storage).ok, true);
  const before = [...storage.values.values()][0];
  state = updateWorkbenchEntry(state, "scene-1", { draft: { prompt: "不能丢失" } });
  storage.setItem = () => { throw new Error("QuotaExceededError"); };
  assert.equal(writeCreationWorkspace(state, storage).ok, false);
  assert.equal([...storage.values.values()][0], before);
  storage.setItem = () => {};
  assert.equal(writeCreationWorkspace(state, storage).ok, false);
  assert.equal([...storage.values.values()][0], before);
  assert.equal(state.entries[0].draft.prompt, "不能丢失");
});

test("stale and same-revision divergent tabs are refused without overwriting newer drafts", () => {
  const storage = memoryStorage();
  const { state } = fixture();
  assert.equal(writeCreationWorkspace(state, storage).ok, true);
  const left = updateWorkbenchEntry(state, "scene-1", { draft: { prompt: "左边的新稿" } });
  const right = updateWorkbenchEntry(state, "scene-1", { draft: { prompt: "右边的新稿" } });
  assert.equal(writeCreationWorkspace(left, storage).ok, true);
  assert.equal(writeCreationWorkspace(right, storage).ok, false);
  assert.equal(writeCreationWorkspace(state, storage).ok, false);
  const writes = storage.writes.length;
  assert.equal(writeCreationWorkspace(left, storage).ok, true);
  assert.equal(storage.writes.length, writes, "Exact replay does not add a write");
  assert.equal(readCreationWorkspace(SCOPE, storage).state.entries[0].draft.prompt, "左边的新稿");
});

test("an externally changed storage baseline cannot be bypassed by incrementing a stale revision", () => {
  const storage = memoryStorage();
  const { state } = fixture();
  assert.equal(writeCreationWorkspace(state, storage).ok, true);
  readCreationWorkspace(SCOPE, storage);
  const external = updateWorkbenchEntry(state, "scene-1", { draft: { prompt: "另一标签页内容" } });
  const key = [...storage.values.keys()][0];
  const externalBytes = JSON.stringify(external);
  // Simulate another browser tab, with its own module instance, writing storage.
  storage.values.set(key, externalBytes);
  let stale = updateWorkbenchEntry(state, "scene-1", { draft: { prompt: "本页尚未合并" } });
  assert.equal(writeCreationWorkspace(stale, storage).ok, false);
  stale = updateWorkbenchEntry(stale, "scene-1", { draft: { prompt: "继续输入也不应覆盖" } });
  assert.ok(stale.revision > external.revision);
  assert.equal(writeCreationWorkspace(stale, storage).ok, false);
  assert.equal(storage.getItem(key), externalBytes);
  const reloaded = readCreationWorkspace(SCOPE, storage).state;
  const deliberate = updateWorkbenchEntry(reloaded, "scene-1", { title: "重新读取后修改" });
  assert.equal(writeCreationWorkspace(deliberate, storage).ok, true);
  assert.equal(readCreationWorkspace(SCOPE, storage).state.entries[0].draft.prompt, "另一标签页内容");
});

test("invalid persisted schemas, cycles and injected task snapshots fail closed without repair", () => {
  const base = fixture("canvas", "first").state;
  const variants = [null, [], "{broken", { ...base, version: 3 }, { ...base, revision: -1 },
    { ...base, entries: [base.entries[0], base.entries[0]] },
    { ...base, active: { ...base.active, canvas: "missing" } },
    { ...base, credentials: "forged" },
  ];
  const mutate = (callback) => { const value = clone(base); callback(value); variants.push(value); };
  mutate((state) => { delete state.quick.mobileOpen; });
  mutate((state) => { delete state.entries[0].createdAt; });
  mutate((state) => { delete state.entries[0].draft.modelId; });
  mutate((state) => { state.entries[0].draft.signed_url = "https://private.invalid"; });
  mutate((state) => { state.entries[0].taskIds = [{ id: "task", status: "succeeded" }]; });
  mutate((state) => { state.entries[0].taskIds = ["task", "task"]; });
  mutate((state) => { state.entries[0].selectedTaskId = "outside"; state.entries[0].selectedArtifactId = "artifact"; });
  mutate((state) => { state.entries[0].selectedTaskId = "task"; });
  mutate((state) => { state.entries[0].selectedArtifactId = "artifact"; });
  mutate((state) => { state.entries[0].x = "0"; });
  mutate((state) => { state.entries[0].order = 1; });
  mutate((state) => { state.entries[0].parentId = "first"; state.entries[0].relation = "settings"; });
  mutate((state) => { delete state.lineage; });
  mutate((state) => { state.lineage.activeShotId = "missing-shot"; });
  mutate((state) => { state.active.canvas = ""; });
  mutate((state) => { state.lineage.shots[0].createdAt = Date.parse(NOW); });
  mutate((state) => { state.entries[0].updatedAt = "2026-08-31T08:00:00+08:00"; });
  mutate((state) => { state.lineage.shots[0].credential = "forged"; });
  mutate((state) => { state.entries[0].archived = true; });
  mutate((state) => {
    state.entries.push({ ...clone(state.entries[0]), id: "second-root", order: 1 });
  });
  const cyclic = createWorkbenchEntry(base, "canvas", { id: "second", now: NOW, parentId: "first", relation: "settings" }).state;
  cyclic.entries[0].parentId = "second";
  cyclic.entries[0].relation = "settings";
  variants.push(cyclic);
  const unboundSource = attachWorkbenchTask(base, "first", "task-first");
  const branch = createWorkbenchEntry(unboundSource, "canvas", {
    id: "lineage-child", parentId: "first", relation: "lineage", now: NOW,
    sourceTaskId: "task-first", sourceArtifactId: "artifact-first",
    branchKind: "camera", branchReason: "改变机位", sourceDraft: sanitizeCreationDraft({ prompt: "来源" }),
  }).state;
  const forgedSource = clone(branch);
  forgedSource.entries.find((entry) => entry.id === "lineage-child").sourceTaskId = "task-not-owned";
  variants.push(forgedSource);
  const incompleteSource = clone(branch);
  incompleteSource.entries.find((entry) => entry.id === "lineage-child").sourceArtifactId = "";
  variants.push(incompleteSource);
  const invalidBranchKind = clone(branch);
  invalidBranchKind.entries.find((entry) => entry.id === "lineage-child").branchKind = "provider-secret";
  variants.push(invalidBranchKind);
  const blankBranchIntent = clone(branch);
  blankBranchIntent.entries.find((entry) => entry.id === "lineage-child").branchReason = "   ";
  variants.push(blankBranchIntent);
  const partialCanonical = clone(unboundSource);
  partialCanonical.lineage.shots[0].canonicalEntryId = "first";
  partialCanonical.lineage.shots[0].canonicalTaskId = "task-first";
  variants.push(partialCanonical);
  const unboundCanonical = clone(unboundSource);
  Object.assign(unboundCanonical.lineage.shots[0], {
    canonicalEntryId: "first", canonicalTaskId: "task-not-owned", canonicalArtifactId: "artifact",
  });
  variants.push(unboundCanonical);
  const archivedHierarchy = createWorkbenchEntry(
    createWorkbenchEntry(base, "canvas", { id: "archive-parent", now: NOW, parentId: "first", relation: "settings" }).state,
    "canvas", { id: "archive-child", now: NOW, parentId: "archive-parent", relation: "settings" },
  ).state;
  archivedHierarchy.active.canvas = "first";
  archivedHierarchy.entries.find((entry) => entry.id === "archive-parent").archived = true;
  variants.push(archivedHierarchy);
  for (const invalid of variants) {
    const storage = persistRaw(invalid);
    const original = [...storage.values.values()][0];
    const read = readCreationWorkspace(SCOPE, storage);
    assert.ok(read.notice, `Expected notice for ${original.slice(0, 100)}`);
    assert.equal(read.state.entries.length, 0);
    assert.equal(writeCreationWorkspace(emptyCreationWorkspace(SCOPE), storage).ok, false);
    assert.equal([...storage.values.values()][0], original);
  }
});

test("blank and invalid scope values cannot create a shared anonymous storage bucket", () => {
  for (const scopeKey of ["", " ", " scope ", null, {}, "user\u0000company"]) {
    assert.throws(() => emptyCreationWorkspace(scopeKey), TypeError);
    assert.throws(() => readCreationWorkspace(scopeKey, memoryStorage()), TypeError);
  }
  const state = emptyCreationWorkspace(SCOPE);
  state.scopeKey = "";
  const storage = memoryStorage();
  assert.equal(writeCreationWorkspace(state, storage).ok, false);
  assert.equal(storage.writes.length, 0);
});
