import assert from "node:assert/strict";
import test from "node:test";

import {
  readPendingCreate,
  rememberPendingCreate,
  taskRequestFingerprint,
} from "../src/app/pendingGeneration.js";

const SCOPE = "user:alice|company:first";
const QUOTE = `sha256:${"a".repeat(64)}`;
const clone = (value) => JSON.parse(JSON.stringify(value));
function memoryStorage() {
  const values = new Map();
  const writes = [];
  return {
    values, writes,
    getItem(key) { return values.get(key) ?? null; },
    setItem(key, value) { writes.push([key, value]); values.set(key, value); },
    removeItem(key) { writes.push([key, null]); values.delete(key); },
  };
}

function fingerprint(record) {
  return taskRequestFingerprint(JSON.stringify(record.version >= 5 ? {
    modelId: record.modelId,
    capabilityVersion: record.capabilityVersion,
    quoteRevision: record.quoteRevision,
    requestPayload: record.requestPayload,
    ...(record.version >= 6 ? { creationContext: record.creationContext } : {}),
  } : record.version >= 4 ? {
    modelId: record.modelId,
    capabilityVersion: record.capabilityVersion,
    requestPayload: record.requestPayload,
  } : {
    modelId: record.modelId,
    requestPayload: record.requestPayload,
  }));
}

function pending(version = 6, kind = "notebook") {
  const record = {
    version,
    workspaceKey: SCOPE,
    modelId: "model-id",
    requestPayload: {
      mode: "text_to_video", prompt: "城市雨夜，镜头缓慢推进。", duration_seconds: 5,
      aspect_ratio: "16:9", resolution: "1080p", output_count: 1, face_enabled: false, assets: [],
    },
    idempotencyKey: "same-logical-submission-1234",
    uncertain: true,
  };
  if (version >= 4) record.capabilityVersion = 8;
  if (version >= 5) record.quoteRevision = QUOTE;
  if (version >= 6) record.creationContext = {
    scopeKey: SCOPE, kind, entryId: kind === "quick" ? "" : `wb-${kind}-1`, draftRevision: 12,
  };
  record.fingerprint = fingerprint(record);
  return record;
}

function stored(record, storage = memoryStorage()) {
  rememberPendingCreate(SCOPE, record, storage);
  return storage;
}

test("v6 recovers the exact pinned request and context for each quick/advanced surface", () => {
  for (const kind of ["quick", "console", "notebook", "canvas"]) {
    const record = pending(6, kind);
    const storage = stored(record);
    const original = [...storage.values.values()][0];
    const result = readPendingCreate(SCOPE, storage);
    assert.deepEqual(result, record);
    assert.equal(result.idempotencyKey, "same-logical-submission-1234");
    assert.equal(result.uncertain, true);
    assert.equal(result.creationContext.scopeKey, SCOPE);
    assert.equal(result.creationContext.kind, kind);
    assert.equal(result.creationContext.entryId, kind === "quick" ? "" : `wb-${kind}-1`);
    assert.equal(result.creationContext.draftRevision, 12);
    assert.equal([...storage.values.values()][0], original);
    assert.equal(storage.writes.length, 1, "Reading does not resubmit, clear or rewrite the pending request");
  }
});

test("v6 accepts zero and maximum-safe draft revisions without rounding", () => {
  for (const draftRevision of [0, Number.MAX_SAFE_INTEGER]) {
    const record = pending();
    record.creationContext.draftRevision = draftRevision;
    record.fingerprint = fingerprint(record);
    assert.equal(readPendingCreate(SCOPE, stored(record)).creationContext.draftRevision, draftRevision);
  }
});

test("v6 fingerprints bind context identity as well as request, capability and quote", () => {
  const original = pending();
  const changes = [
    (value) => { value.creationContext.kind = "canvas"; },
    (value) => { value.creationContext.entryId = "wb-other-scene"; },
    (value) => { value.creationContext.draftRevision += 1; },
    (value) => { value.creationContext.scopeKey = "user:bob|company:first"; },
    (value) => { value.requestPayload.prompt = "另一场不同的画面"; },
    (value) => { value.requestPayload.duration_seconds = 10; },
    (value) => { value.modelId = "different-model"; },
    (value) => { value.capabilityVersion += 1; },
    (value) => { value.quoteRevision = `sha256:${"b".repeat(64)}`; },
  ];
  for (const change of changes) {
    const modified = clone(original);
    change(modified);
    assert.notEqual(fingerprint(modified), original.fingerprint);
    assert.equal(readPendingCreate(SCOPE, stored(modified)), null);
  }
  // This fingerprint is a consistency check, not a signature/authorization proof.
});

test("pending storage lookup and context ownership remain isolated by full user/workspace scope", () => {
  const record = pending();
  const storage = stored(record);
  for (const otherScope of ["user:bob|company:first", "user:alice|company:second", "user:alice%7Ccompany:first"]) {
    assert.equal(readPendingCreate(otherScope, storage), null);
    const foreign = clone(record);
    foreign.creationContext.scopeKey = otherScope;
    foreign.fingerprint = fingerprint(foreign);
    assert.equal(readPendingCreate(SCOPE, stored(foreign)), null);
  }
  const mismatchedEnvelope = clone(record);
  mismatchedEnvelope.workspaceKey = "user:bob|company:first";
  assert.equal(readPendingCreate(SCOPE, stored(mismatchedEnvelope)), null);
});

test("v6 rejects missing or malformed context even when the consistency fingerprint is recomputed", () => {
  const valid = pending().creationContext;
  const invalid = [
    undefined, null, [], "not-a-context", {},
    { ...valid, scopeKey: undefined }, { ...valid, scopeKey: "" },
    { ...valid, kind: undefined }, { ...valid, kind: "entry" }, { ...valid, kind: "__proto__" },
    { ...valid, entryId: undefined }, { ...valid, entryId: null }, { ...valid, entryId: 1 },
    { ...valid, entryId: "" }, { ...valid, kind: "quick", entryId: "wb-notebook-1" },
    { ...valid, draftRevision: undefined }, { ...valid, draftRevision: null },
    { ...valid, draftRevision: "12" }, { ...valid, draftRevision: -1 },
    { ...valid, draftRevision: 1.5 }, { ...valid, draftRevision: Number.MAX_SAFE_INTEGER + 1 },
  ];
  for (const creationContext of invalid) {
    const record = { ...pending(), creationContext };
    record.fingerprint = fingerprint(record);
    assert.equal(readPendingCreate(SCOPE, stored(record)), null, JSON.stringify(creationContext));
  }
});

test("v6 rejects whitespace, control characters, URLs and overlong advanced entry IDs", () => {
  for (const entryId of [" ", "\t", "wb-scene\u0000", "wb-scene\n", "https://private.invalid/node", "x".repeat(257), "__proto__"]) {
    const record = pending();
    record.creationContext.entryId = entryId;
    record.fingerprint = fingerprint(record);
    assert.equal(readPendingCreate(SCOPE, stored(record)), null, `Invalid entry ID: ${JSON.stringify(entryId)}`);
  }
});

test("v6 context is the exact minimal metadata shape, not a task snapshot or secret container", () => {
  for (const extra of [
    { signed_url: "https://private.invalid?token=not-for-context" },
    { task: { id: "task-1", status: "succeeded" } },
    { credentials: "not-for-context" },
    JSON.parse('{"__proto__":{"scopeKey":"forged"}}'),
  ]) {
    const record = pending();
    record.creationContext = { ...record.creationContext, ...extra };
    record.fingerprint = fingerprint(record);
    assert.equal(readPendingCreate(SCOPE, stored(record)), null);
  }
});

test("changing a v6 record to a legacy version does not bypass its bound-context fingerprint", () => {
  for (const version of [1, 2, 3, 4, 5]) {
    const record = pending();
    record.version = version;
    assert.equal(readPendingCreate(SCOPE, stored(record)), null);
  }
});

test("versions 1 through 5 retain their own fingerprint contracts and exact request payloads", () => {
  for (const version of [1, 2, 3, 4, 5]) {
    const record = pending(version);
    const result = readPendingCreate(SCOPE, stored(record));
    assert.deepEqual(result, record, `Legacy version ${version}`);
    assert.equal(result.creationContext, undefined);
    assert.equal(result.idempotencyKey, record.idempotencyKey);
    assert.deepEqual(result.requestPayload, record.requestPayload);
  }
});

test("legacy companyId-scoped records remain recoverable without synthesizing a new workbench context", () => {
  const record = pending(1);
  delete record.workspaceKey;
  record.companyId = SCOPE;
  const recovered = readPendingCreate(SCOPE, stored(record));
  assert.deepEqual(recovered, record);
  assert.equal(recovered.creationContext, undefined);
});

test("v6 context does not weaken capability/quote/request validation", () => {
  const changes = [
    (value) => { delete value.capabilityVersion; },
    (value) => { value.capabilityVersion = 0; },
    (value) => { value.quoteRevision = "not-a-quote-revision"; },
    (value) => { value.requestPayload.mode = "unsupported_mode"; },
    (value) => { value.requestPayload.prompt = " "; },
    (value) => { value.requestPayload.output_count = 0; },
    (value) => { value.requestPayload.duration_seconds = 0; },
    (value) => { value.requestPayload.face_enabled = "true"; },
    (value) => { value.requestPayload.signed_url = "https://private.invalid"; },
    (value) => { value.requestPayload.mode = "image_to_video"; },
    (value) => { value.requestPayload.mode = "video_to_video"; },
    (value) => { value.idempotencyKey = "short"; },
  ];
  for (const change of changes) {
    const record = pending();
    change(record);
    record.fingerprint = fingerprint(record);
    assert.equal(readPendingCreate(SCOPE, stored(record)), null);
  }
});

test("the same pinned v6 request restores its exact reference-asset order and input types", () => {
  const record = pending(6, "canvas");
  record.requestPayload.mode = "video_to_video";
  record.requestPayload.assets = [
    { asset_id: "video-reference", media_type: "video" },
    { asset_id: "image-reference", media_type: "image" },
    { asset_id: "audio-reference", media_type: "audio" },
  ];
  record.fingerprint = fingerprint(record);
  const result = readPendingCreate(SCOPE, stored(record));
  assert.deepEqual(result.requestPayload.assets, record.requestPayload.assets);
  assert.deepEqual(result.creationContext, record.creationContext);
  assert.equal(result.fingerprint, record.fingerprint);
});

test("bad JSON and unavailable storage return null without rewriting or clearing pending evidence", () => {
  const storage = stored(pending());
  const key = [...storage.values.keys()][0];
  storage.values.set(key, "{broken");
  assert.equal(readPendingCreate(SCOPE, storage), null);
  assert.equal(storage.values.get(key), "{broken");
  assert.equal(storage.writes.length, 1);
  assert.equal(readPendingCreate(SCOPE, { getItem() { throw new Error("Storage blocked"); } }), null);
  assert.equal(readPendingCreate(SCOPE, null), null);
});

test("clearing a confirmed request only removes its own scope", () => {
  const storage = stored(pending());
  const otherScope = "user:bob|company:second";
  const other = pending();
  other.workspaceKey = otherScope;
  other.creationContext.scopeKey = otherScope;
  other.fingerprint = fingerprint(other);
  rememberPendingCreate(otherScope, other, storage);
  rememberPendingCreate(SCOPE, null, storage);
  assert.equal(readPendingCreate(SCOPE, storage), null);
  assert.deepEqual(readPendingCreate(otherScope, storage), other);
});

function assertPersistenceFailure(result) {
  assert.equal(result?.ok, false, "Persistence must fail explicitly, not return void or apparent success");
  assert.equal(typeof result.notice, "string");
  assert.ok(result.notice.trim(), "A failed persistence operation needs a visible recovery notice");
  assert.doesNotMatch(result.notice, /private-storage-token/);
}

test("pending persistence acknowledges only exact retained bytes and still restores versions 1 through 6", () => {
  for (const version of [1, 2, 3, 4, 5, 6]) {
    const record = pending(version);
    record.requestPayload.prompt = "保留换行\n镜头 🎬 缓慢推进，不改变原请求。";
    record.fingerprint = fingerprint(record);
    const storage = memoryStorage();
    const result = rememberPendingCreate(SCOPE, record, storage);
    assert.deepEqual(result, { ok: true, notice: "" });
    assert.equal(storage.writes.length, 1);
    assert.equal([...storage.values.values()][0], JSON.stringify(record));
    assert.deepEqual(readPendingCreate(SCOPE, storage), record, `Recover exact version ${version}`);
  }
});

test("pending persistence fails explicitly when storage or its read/write methods are unavailable", () => {
  for (const storage of [null, {}, { getItem() { return null; } }, { setItem() {} }]) {
    assertPersistenceFailure(rememberPendingCreate(SCOPE, pending(), storage));
  }
});

test("pending persistence catches the sessionStorage getter without leaking its error", () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    get() { throw new Error("SecurityError private-storage-token"); },
  });
  try {
    assertPersistenceFailure(rememberPendingCreate(SCOPE, pending()));
    assertPersistenceFailure(rememberPendingCreate(SCOPE, null));
  } finally {
    if (previous) Object.defineProperty(globalThis, "sessionStorage", previous);
    else delete globalThis.sessionStorage;
  }
});

test("throwing storage method getters and a missing default storage never acknowledge persistence", () => {
  const getItemGetter = { setItem() {}, get getItem() { throw new Error("private-storage-token"); } };
  const setItemGetter = { getItem() { return null; }, get setItem() { throw new Error("private-storage-token"); } };
  assertPersistenceFailure(rememberPendingCreate(SCOPE, pending(), getItemGetter));
  assertPersistenceFailure(rememberPendingCreate(SCOPE, pending(), setItemGetter));
  const previous = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: undefined });
  try { assertPersistenceFailure(rememberPendingCreate(SCOPE, pending())); }
  finally {
    if (previous) Object.defineProperty(globalThis, "sessionStorage", previous);
    else delete globalThis.sessionStorage;
  }
});

test("a quota/setItem failure retains the original pending bytes and the exact in-memory request", () => {
  const record = pending();
  const storage = stored(record);
  const originalBytes = [...storage.values.values()][0];
  const modified = clone(record);
  modified.creationContext.draftRevision += 1;
  modified.fingerprint = fingerprint(modified);
  const before = clone(modified);
  storage.setItem = () => { throw new Error("QuotaExceededError private-storage-token"); };
  assertPersistenceFailure(rememberPendingCreate(SCOPE, modified, storage));
  assert.equal([...storage.values.values()][0], originalBytes);
  assert.deepEqual(modified, before);
});

test("a getItem failure after setItem means durability is unconfirmed, not a successful save", () => {
  const calls = [];
  let attemptedWrite = false;
  const storage = {
    setItem(key, value) { attemptedWrite = true; calls.push(["set", key, value]); },
    getItem(key) {
      calls.push(["get", key]);
      if (!attemptedWrite) return null;
      throw new Error("private-storage-token");
    },
  };
  assertPersistenceFailure(rememberPendingCreate(SCOPE, pending(), storage));
  const writeIndex = calls.findIndex(([operation]) => operation === "set");
  assert.ok(writeIndex >= 0);
  assert.equal(calls[writeIndex + 1]?.[0], "get");
  assert.equal(calls[writeIndex][1], calls[writeIndex + 1][1], "Readback must verify the same scope key that was just written");
});

test("silently dropped, transformed and non-string pending writes all fail byte verification", () => {
  const record = pending();
  for (const readback of [null, undefined, "", JSON.stringify(record, null, 2), JSON.stringify({ ...record, uncertain: false }), record]) {
    let attemptedBytes;
    const storage = {
      setItem(_key, value) { attemptedBytes = value; },
      getItem() { return readback; },
    };
    assertPersistenceFailure(rememberPendingCreate(SCOPE, record, storage));
    assert.equal(attemptedBytes, JSON.stringify(record));
  }
});

test("pending serialization failure never writes an incomplete request", () => {
  const storage = memoryStorage();
  const record = pending();
  record.circular = record;
  assertPersistenceFailure(rememberPendingCreate(SCOPE, record, storage));
  assert.equal(storage.writes.length, 0);
  assert.equal(storage.values.size, 0);
});

test("confirmed-request cleanup reports success only after the exact scope key is absent", () => {
  const storage = stored(pending());
  const key = [...storage.values.keys()][0];
  const otherScope = "user:bob|company:second";
  const other = pending();
  other.workspaceKey = otherScope;
  other.creationContext.scopeKey = otherScope;
  other.fingerprint = fingerprint(other);
  assert.deepEqual(rememberPendingCreate(otherScope, other, storage), { ok: true, notice: "" });
  const result = rememberPendingCreate(SCOPE, null, storage);
  assert.deepEqual(result, { ok: true, notice: "" });
  assert.equal(storage.getItem(key), null);
  assert.deepEqual(readPendingCreate(otherScope, storage), other);
  assert.deepEqual(rememberPendingCreate(SCOPE, null, storage), { ok: true, notice: "" }, "Already-absent cleanup is idempotent");
});

test("cleanup failures and silent non-removal return a failure notice and do not claim confirmation", () => {
  const storage = stored(pending());
  const original = [...storage.values.values()][0];
  storage.removeItem = () => { throw new Error("private-storage-token"); };
  assertPersistenceFailure(rememberPendingCreate(SCOPE, null, storage));
  assert.equal([...storage.values.values()][0], original);
  storage.removeItem = () => {};
  assertPersistenceFailure(rememberPendingCreate(SCOPE, null, storage));
  assert.equal([...storage.values.values()][0], original);
  assertPersistenceFailure(rememberPendingCreate(SCOPE, null, null));
  assertPersistenceFailure(rememberPendingCreate(SCOPE, null, { getItem() { return null; } }));
});

test("cleanup readback and method-getter failures remain unconfirmed even if removeItem was called", () => {
  let removed = false;
  const storage = {
    removeItem() { removed = true; },
    getItem() { throw new Error("private-storage-token"); },
  };
  assertPersistenceFailure(rememberPendingCreate(SCOPE, null, storage));
  assert.equal(removed, true);
  assertPersistenceFailure(rememberPendingCreate(SCOPE, null, {
    getItem() { return null; },
    get removeItem() { throw new Error("private-storage-token"); },
  }));
});

test("missing scope cannot acknowledge saving or clearing a shared anonymous pending bucket", () => {
  const storage = memoryStorage();
  assertPersistenceFailure(rememberPendingCreate("", pending(), storage));
  assertPersistenceFailure(rememberPendingCreate("", null, storage));
  assert.equal(storage.writes.length, 0);
});
