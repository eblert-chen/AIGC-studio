import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const lineageSource = await readFile(
  new URL("../src/pages/studio/LineageGraphWorkspace.jsx", import.meta.url),
  "utf8",
);
const viewsSource = await readFile(
  new URL("../src/pages/studio/CreationWorkbenchViews.jsx", import.meta.url),
  "utf8",
);
const hubSource = await readFile(new URL("../src/CreationHub.jsx", import.meta.url), "utf8");
const appSource = await readFile(new URL("../src/App.jsx", import.meta.url), "utf8");
const stateSource = await readFile(
  new URL("../src/app/creationWorkspaces.js", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../src/design-system/workbenches.css", import.meta.url),
  "utf8",
);

function loadLineageHelpers() {
  const start = lineageSource.indexOf("const DIRECTION_WIDTH");
  const end = lineageSource.indexOf("function LineageShotNode", start);
  assert.ok(start >= 0 && end > start, "lineage pure-helper section must stay extractable");
  const body = lineageSource.slice(start, end).replaceAll("export function", "function");
  return new Function(`${body}\nreturn { selectedLineageTake, canonicalTake, formatTakeSources, lineageDraftDelta, compareTakeFacts, canonicalDirectionPath, layoutLineageDirections };`)();
}

const helpers = loadLineageHelpers();

function direction(overrides = {}) {
  return {
    id: "root",
    kind: "canvas",
    shotId: "shot-1",
    parentId: "",
    relation: "",
    sourceTaskId: "",
    sourceArtifactId: "",
    order: 0,
    x: 0,
    y: 0,
    ...overrides,
  };
}

test("lineage helpers preserve exact Take identity, human-readable sources and explicit draft deltas", () => {
  const outputA = {
    taskId: "task-1",
    artifactId: "artifact-a",
    status: "succeeded",
    artifact: { artifact_id: "artifact-a" },
    sources: [{ label: "首帧", count: 2 }],
  };
  const outputB = {
    taskId: "task-1",
    artifactId: "artifact-b",
    status: "succeeded",
    artifact: { artifact_id: "artifact-b" },
    sources: [{ type: "视频参考", count: 1 }, "角色参考"],
  };
  const entry = {
    selectedTaskId: "task-1",
    selectedArtifactId: "artifact-b",
    takes: [outputA, outputB],
  };

  assert.equal(helpers.selectedLineageTake(entry, true), outputB);
  assert.equal(
    helpers.selectedLineageTake({ ...entry, selectedArtifactId: "missing" }, true),
    null,
    "an unreadable exact artifact must not fall back to another output of the task",
  );
  assert.equal(
    helpers.selectedLineageTake({ ...entry, selectedArtifactId: "missing" }),
    outputB,
    "the newest Take remains available only as a visual fallback",
  );
  assert.equal(helpers.formatTakeSources(outputB.sources), "视频参考 1、角色参考");
  assert.doesNotMatch(helpers.formatTakeSources(outputB.sources), /\[object Object\]/);

  const delta = helpers.lineageDraftDelta({
    sourceDraft: {
      prompt: "远景",
      ratio: "16:9",
      files: { image: [{ asset_id: "asset-a" }] },
    },
    draft: {
      prompt: "近景",
      ratio: "16:9",
      files: { image: [{ asset_id: "asset-b" }] },
    },
  });
  assert.deepEqual(delta.map((item) => item.label), ["制作说明", "参考素材"]);
  const facts = helpers.compareTakeFacts(
    { ...outputA, prompt: "远景" },
    { ...outputB, prompt: "近景" },
  );
  assert.deepEqual(facts.map((item) => item.label), ["制作说明", "参考素材"]);
  assert.equal(facts[1].before, "首帧 2");
  assert.equal(facts[1].after, "视频参考 1、角色参考");
});

test("lineage layout and director path derive only from recorded parent relations", () => {
  const root = direction();
  const child = direction({
    id: "child",
    parentId: "root",
    relation: "lineage",
    sourceTaskId: "task-root",
    sourceArtifactId: "artifact-root",
    order: 1,
  });
  const manualGrandchild = direction({
    id: "grandchild",
    parentId: "child",
    relation: "lineage",
    sourceTaskId: "task-child",
    sourceArtifactId: "artifact-child",
    order: 2,
    x: 620,
    y: 180,
  });
  const sameTimeButUnrelated = direction({ id: "legacy", parentId: "root", relation: "settings", order: 3 });
  const entries = [sameTimeButUnrelated, manualGrandchild, child, root];

  const layout = helpers.layoutLineageDirections(entries, "shot-1");
  assert.deepEqual(layout.directions.map((entry) => entry.id), ["root", "child", "grandchild", "legacy"]);
  assert.deepEqual(layout.roots.map((entry) => entry.id), ["root", "legacy"]);
  assert.deepEqual(layout.positions.get("grandchild"), { x: 620, y: 180 });

  const mainline = helpers.canonicalDirectionPath(entries, {
    canonicalEntryId: "grandchild",
    canonicalTaskId: "task-grandchild",
    canonicalArtifactId: "artifact-grandchild",
  });
  assert.deepEqual([...mainline], ["grandchild", "child", "root"]);
  assert.equal(mainline.has("legacy"), false, "task order alone must not invent a lineage edge");
});

test("graph interaction is fail-closed for exact Takes, locks, archives and React Flow deletion", () => {
  assert.match(lineageSource, /const graphEntries = useMemo\(/);
  assert.match(lineageSource, /const selectedTake = selectedLineageTake\(entry, true\)/);
  assert.match(lineageSource, /sourceTake:\s*selectedTake/);
  assert.match(lineageSource, /deleteKeyCode=\{null\}/);
  assert.match(lineageSource, /draggable:\s*!controller\?\.locked && !entry\.archived/);
  assert.match(lineageSource, /onNodeDragStop=\{\(_, node\) => \{[\s\S]*?entry && !entry\.archived && !controller\?\.locked[\s\S]*?onMoveEntryOnCanvas/);
  assert.match(lineageSource, /className="lineage-node-restore nodrag nopan"[\s\S]*?onRestore\?\.\(\)/);
  assert.match(stateSource, /if \(!archived\)[\s\S]*?let ancestor = entry\.parentId[\s\S]*?affected\.add\(ancestor\.id\)/);
  assert.match(lineageSource, /<div role="listitem"[\s\S]*?<button[\s\S]*?aria-pressed=\{selected\}/);
  assert.doesNotMatch(lineageSource, /<button[^>]*role="listitem"/);
  assert.match(lineageSource, /mainlineState:\s*canonicalRecordExists[\s\S]*?"unavailable"/);
  assert.match(lineageSource, /主线证据待读取/);
});

test("lineage lazy loading, global import ownership and keyboard semantics remain wired", () => {
  assert.match(viewsSource, /lazy\(\(\) => import\("\.\/LineageGraphWorkspace\.jsx"\)\)/);
  assert.doesNotMatch(viewsSource, /import LineageGraphWorkspace from/);
  assert.match(hubSource, /desktopGateActive \? \([\s\S]*?<DesktopWorkbenchGate/);
  assert.match(hubSource, /ownedTaskIds:\s*normalizedOwnedTaskIds/);
  assert.match(appSource, /ownedTaskIds=\{ownedCreationTaskIds\}/);
  assert.match(viewsSource, /const imported = new Set\(\[[\s\S]*?\.\.\.ownedTaskIds,[\s\S]*?\.\.\.\(controller\?\.ownedTaskIds \|\| \[\]\),[\s\S]*?\.flatMap/);

  assert.match(lineageSource, /ref=\{drawerRef\}[\s\S]*?role="dialog"[\s\S]*?aria-modal="true"/);
  assert.match(lineageSource, /drawerTitleRef\.current\?\.focus\(\)/);
  assert.match(lineageSource, /const last = focusable\.at\(-1\)[\s\S]*?last\.focus\(\)/);
  assert.match(lineageSource, /drawerRestoreFocusRef\.current\?\.focus\?\.\(\)/);
  assert.match(lineageSource, /role="radiogroup"[\s\S]*?role="radio"[\s\S]*?tabIndex=\{branchKind === option\.id \? 0 : -1\}/);
  assert.match(lineageSource, /\["ArrowRight", "ArrowDown"\]/);
  assert.match(lineageSource, /\["ArrowLeft", "ArrowUp"\]/);
  assert.match(lineageSource, /event\.key === "Home"/);
  assert.match(lineageSource, /event\.key === "End"/);
  assert.match(lineageSource, /ariaLabel:\s*`\$\{entry\.title \|\| "未命名方向"\}，\$\{entry\.archived/);

  assert.doesNotMatch(styles, /\.wb-canvas-viewport|\.wb-node-field|\.wb-lineage-edges/);
  assert.match(styles, /\.lineage-graph\s*\{/);
  assert.match(styles, /@media \(max-width: 640px\)[\s\S]*?\.wb-surface\.wb-lineage\s*\{\s*display:\s*none;/);
});
