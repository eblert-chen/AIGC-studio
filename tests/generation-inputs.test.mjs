import assert from "node:assert/strict";
import test from "node:test";
import { appendGenerationInputAssets, createGenerationUploadGate } from "../src/app/useGenerationDraft.js";

const emptyFiles = () => ({ image: [], video: [], audio: [] });
const assets = (kind, count, prefix = "asset") => Array.from({ length: count }, (_, index) => ({
  id: `${prefix}-${kind}-${index}`, media_type: kind,
}));
const capability = (image, video = 0, audio = 0) => ({
  inputMediaTypes: Object.entries({ image, video, audio }).filter(([, count]) => count > 0).map(([kind]) => kind),
  supportsFace: false,
  limits: { maxImages: image, maxVideos: video, maxAudio: audio },
});

test("every draft entry point can append only the exact declared count, never a hardcoded nine", () => {
  for (const limit of [1, 4, 6, 9]) {
    const inputs = assets("image", limit + 1);
    const original = emptyFiles();
    const first = appendGenerationInputAssets(capability(limit), original, "image", inputs);
    assert.equal(first.addedCount, limit);
    assert.equal(first.overflowCount, 1);
    assert.deepEqual(first.files.image, inputs.slice(0, limit));
    assert.deepEqual(original, emptyFiles(), "does not mutate the prior draft");
    const repeated = appendGenerationInputAssets(capability(limit), first.files, "image", inputs);
    assert.equal(repeated.addedCount, 0);
    assert.equal(repeated.duplicateCount, limit);
    assert.equal(repeated.overflowCount, 1);
    assert.deepEqual(repeated.files, first.files);
  }
});

test("successive appends are deduplicated by canonical asset id and cannot steal occupied slots", () => {
  const cap = capability(2, 1, 1);
  const first = appendGenerationInputAssets(cap, emptyFiles(), "image", [{ id: "a", media_type: "image" }]);
  const second = appendGenerationInputAssets(cap, first.files, "image", [
    { asset_id: "a", media_type: "image" }, { id: "b", media_type: "image" }, { id: "c", media_type: "image" },
  ]);
  assert.equal(second.duplicateCount, 1);
  assert.equal(second.overflowCount, 1);
  assert.deepEqual(second.files.image.map((asset) => asset.id), ["a", "b"]);
  const promoted = appendGenerationInputAssets(cap, second.files, "image", [{ id: "new-result", media_type: "image" }]);
  assert.deepEqual(promoted.files.image, second.files.image, "a promoted result must not silently replace existing references");
  assert.equal(promoted.addedCount, 0);
  const video = appendGenerationInputAssets(cap, second.files, "video", assets("video", 2));
  assert.equal(video.files.video.length, 1);
  assert.deepEqual(video.files.image, second.files.image);
});

test("unsupported kinds never gain inputs through an append", () => {
  for (const cap of [null, capability(0), { ...capability(9), inputMediaTypes: [] }]) {
    const result = appendGenerationInputAssets(cap, emptyFiles(), "image", assets("image", 2));
    assert.equal(result.addedCount, 0);
    assert.equal(result.overflowCount, 2);
    assert.deepEqual(result.files, emptyFiles());
  }
  assert.deepEqual(appendGenerationInputAssets(capability(9), emptyFiles(), "unknown", []).files, emptyFiles());
});

test("malformed and mismatched asset responses cannot occupy valid input slots", () => {
  const result = appendGenerationInputAssets(capability(1), emptyFiles(), "image", [
    null, [], {}, { id: 123, media_type: "image" }, { id: " ", media_type: "image" },
    { id: "video", media_type: "video" }, { id: "image", media_type: "image" },
  ]);
  assert.equal(result.invalidCount, 6);
  assert.equal(result.addedCount, 1);
  assert.equal(result.overflowCount, 0);
  assert.deepEqual(result.files.image, [{ id: "image", media_type: "image" }]);
});

test("one upload owner holds the gate until its entire batch completes", () => {
  const gate = createGenerationUploadGate();
  const context = { workspaceKey: "company:a", draftRevision: 1, locked: false };
  assert.equal(gate.busy, false);
  const first = gate.begin(context);
  assert.equal(gate.busy, true);
  assert.equal(gate.begin(context), null, "another media type cannot start a parallel batch");
  assert.equal(gate.canAppend(first, context), true);
  assert.equal(gate.finish({ ...first }), false);
  assert.equal(gate.busy, true, "non-owner completion cannot release the submit lock");
  assert.equal(gate.finish(first), true);
  const next = gate.begin(context);
  assert.notEqual(first, next);
  assert.equal(gate.finish(first), false, "a late old completion cannot unlock a new upload");
  assert.equal(gate.busy, true);
  assert.equal(gate.canAppend(first, context), false);
  assert.equal(gate.finish(next), true);
});

test("late uploaded assets cannot mutate a different model, workspace, mode, or locked submission", () => {
  const gate = createGenerationUploadGate();
  const original = { workspaceKey: "company:a", draftRevision: 1, locked: false };
  const upload = gate.begin(original);
  for (const current of [
    { ...original, draftRevision: 2 },
    { ...original, workspaceKey: "personal:b" },
    { ...original, locked: true },
  ]) {
    assert.equal(gate.canAppend(upload, current), false);
    assert.equal(gate.busy, true, "the pending transfer must still block submission");
  }
  assert.equal(gate.canAppend(upload, original), true);
  assert.equal(gate.finish(upload), true);
  assert.equal(gate.canAppend(upload, original), false);
  assert.equal(gate.canAppend(null, original), false);
});
