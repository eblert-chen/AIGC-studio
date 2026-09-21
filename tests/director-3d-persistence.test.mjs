import assert from "node:assert/strict";
import test from "node:test";
import {
  assertDirectorSceneRecoveryCandidate,
  createDirectorScenePersistenceSession,
  DirectorSceneConflictError,
  DirectorSceneRecoveryRequiredError,
  MAX_DIRECTOR_SCENE_BYTES,
  validateDirectorSceneSnapshot,
} from "../src/components/director3d/directorPersistence.js";

function snapshot(backgroundColor = "#000000") {
  return {
    viewMode: "director",
    selectedObjectId: null,
    selectedObjectIds: [],
    selectedCrowdId: null,
    directorInspectorMode: "scene",
    transformMode: "translate",
    viewportAspectRatio: "auto",
    viewportRuleOfThirdsEnabled: false,
    viewportPanelsCollapsed: false,
    project: {
      version: 1,
      scene: { backgroundColor },
      assets: [],
      objects: [],
      cameras: [],
      activeCameraId: null,
      panoramaAssetId: null,
    },
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function recoveryFixture(initialRecord) {
  let active = structuredClone(initialRecord);
  let quarantineSequence = 0;
  const quarantines = new Map();
  const adapter = {
    async load(id) {
      if (!active) return { snapshot: null, revision: 0 };
      if (active.id === id && active.schemaVersion === 1 && Number.isSafeInteger(active.revision) && active.revision > 0) {
        try {
          return { snapshot: validateDirectorSceneSnapshot(active.snapshot).snapshot, revision: active.revision };
        } catch { /* Preserve and quarantine the invalid active record below. */ }
      }
      const quarantineId = `fixture-quarantine-${++quarantineSequence}`;
      quarantines.set(quarantineId, {
        quarantineId,
        originalId: id,
        quarantinedAt: "2026-09-02T00:00:00.000Z",
        record: structuredClone(active),
      });
      throw new DirectorSceneRecoveryRequiredError(quarantineId);
    },
    async recover(id, quarantineId) {
      const quarantine = quarantines.get(quarantineId);
      assertDirectorSceneRecoveryCandidate(id, quarantineId, active, quarantine);
      active = null;
      return { revision: 0, quarantineId };
    },
    async save(id, value, { expectedRevision }) {
      const currentRevision = active?.revision || 0;
      if (currentRevision !== expectedRevision) throw new DirectorSceneConflictError();
      active = {
        id,
        schemaVersion: 1,
        revision: currentRevision + 1,
        snapshot: structuredClone(value),
      };
      return { revision: active.revision, size: 1 };
    },
  };
  return {
    adapter,
    getActive: () => structuredClone(active),
    setActive: (record) => { active = structuredClone(record); },
    getQuarantine: (token) => structuredClone(quarantines.get(token)),
  };
}

test("director persistence rejects secrets, unsupported roots, non-finite values, deep trees and oversized data", () => {
  const valid = snapshot();
  const checked = validateDirectorSceneSnapshot(valid);
  assert.deepEqual(checked.snapshot, valid);
  assert.notEqual(checked.snapshot, valid);
  assert.ok(checked.size < MAX_DIRECTOR_SCENE_BYTES);

  assert.throws(() => validateDirectorSceneSnapshot({ ...snapshot(), quote: { points: 2 } }), /不受支持|凭据/);
  assert.throws(() => validateDirectorSceneSnapshot({
    ...snapshot(),
    project: { ...snapshot().project, scene: { backgroundColor: "#000", api_key: "secret" } },
  }), /凭据/);
  assert.throws(() => validateDirectorSceneSnapshot({
    ...snapshot(),
    project: { ...snapshot().project, scene: { backgroundColor: "#000", scale: Number.NaN } },
  }), /无效数值/);

  let nested = { value: "end" };
  for (let index = 0; index < 40; index += 1) nested = { nested };
  assert.throws(() => validateDirectorSceneSnapshot({
    ...snapshot(),
    project: { ...snapshot().project, scene: { backgroundColor: "#000", nested } },
  }), /嵌套过深/);
  assert.throws(() => validateDirectorSceneSnapshot({
    ...snapshot(),
    project: { ...snapshot().project, scene: { backgroundColor: "#000", localData: "x".repeat(8 * 1024 * 1024 + 1) } },
  }), /过大的内嵌资源/);
});

test("director persistence coalesces queued scene saves to the latest snapshot", async () => {
  const firstGate = deferred();
  const writes = [];
  let revision = 0;
  const adapter = {
    load: async () => ({ snapshot: null, revision }),
    save: async (_id, value, { expectedRevision, signal }) => {
      assert.equal(expectedRevision, revision);
      writes.push(value.project.scene.backgroundColor);
      if (writes.length === 1) {
        await Promise.race([
          firstGate.promise,
          new Promise((_, reject) => signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true })),
        ]);
      }
      revision += 1;
      return { revision, size: 1 };
    },
  };
  const session = createDirectorScenePersistenceSession(`coalesce-${crypto.randomUUID()}`, { adapter });
  await session.load();
  const first = session.save(snapshot("#111111"));
  await Promise.resolve();
  const middle = session.save(snapshot("#222222"));
  const latest = session.save(snapshot("#333333"));
  assert.deepEqual(await middle, { superseded: true, revision: 0 });
  firstGate.resolve();
  assert.equal((await first).revision, 1);
  assert.equal((await latest).revision, 2);
  assert.deepEqual(writes, ["#111111", "#333333"]);
  await session.close();
});

test("same-shot remount aborts the old writer and waits before loading", async () => {
  const started = deferred();
  const records = new Map();
  const adapter = {
    load: async (id) => records.get(id) || { snapshot: null, revision: 0 },
    save: async (id, value, { expectedRevision, signal }) => {
      started.resolve();
      await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, 5_000);
        signal.addEventListener("abort", () => {
          clearTimeout(timer);
          reject(new DOMException("aborted", "AbortError"));
        }, { once: true });
      });
      const next = { snapshot: value, revision: expectedRevision + 1 };
      records.set(id, next);
      return { revision: next.revision, size: 1 };
    },
  };
  const id = `remount-${crypto.randomUUID()}`;
  const oldSession = createDirectorScenePersistenceSession(id, { adapter });
  await oldSession.load();
  const oldSave = oldSession.save(snapshot("#111111"));
  await started.promise;
  const newSession = createDirectorScenePersistenceSession(id, { adapter });
  await assert.rejects(oldSave, { name: "AbortError" });
  const loaded = await newSession.load();
  assert.equal(loaded.revision, 0);
  assert.equal(records.has(id), false, "an aborted old mount must not overwrite the new mount");
  await newSession.close();
});

test("a revision conflict fails closed and does not publish a saved result", async () => {
  let saveCalls = 0;
  const adapter = {
    load: async () => ({ snapshot: snapshot("#111111"), revision: 4 }),
    save: async () => {
      saveCalls += 1;
      throw new DirectorSceneConflictError();
    },
  };
  const session = createDirectorScenePersistenceSession(`conflict-${crypto.randomUUID()}`, { adapter });
  await session.load();
  await assert.rejects(session.save(snapshot("#222222")), DirectorSceneConflictError);
  assert.equal(saveCalls, 1);
  assert.equal(session.getRevision(), 4);
  await session.close();
});

test("legacy scene remains active and fully quarantined until explicit empty-scene recovery", async () => {
  const id = `legacy-recovery-${crypto.randomUUID()}`;
  const legacy = { id, version: 1, legacySnapshot: snapshot("#112233"), evidenceMarker: "quarantine-only" };
  const fixture = recoveryFixture(legacy);
  const session = createDirectorScenePersistenceSession(id, { adapter: fixture.adapter });
  let recoveryToken = "";
  await assert.rejects(session.load(), (error) => {
    recoveryToken = error.recoveryToken;
    return error instanceof DirectorSceneRecoveryRequiredError && Boolean(recoveryToken);
  });

  assert.deepEqual(fixture.getActive(), legacy, "load failure must not replace the active legacy record");
  assert.deepEqual(fixture.getQuarantine(recoveryToken).record, legacy, "quarantine retains the complete original record");
  await Promise.resolve();
  assert.deepEqual(fixture.getActive(), legacy, "no explicit recovery means no active-record mutation");

  await session.recover(recoveryToken);
  assert.equal(fixture.getActive(), null);
  assert.deepEqual(fixture.getQuarantine(recoveryToken).record, legacy, "explicit recovery must retain quarantine evidence");
  assert.deepEqual(await session.load(), { snapshot: null, revision: 0 });
  await session.save(snapshot("#334455"));
  assert.equal(fixture.getActive().revision, 1, "a valid empty-scene session can create a new active record");
  assert.deepEqual(fixture.getQuarantine(recoveryToken).record, legacy);
  await session.close();
});

test("explicit recovery rejects concurrent active-record drift and a now-valid scene", async () => {
  const id = `drift-recovery-${crypto.randomUUID()}`;
  const damaged = { id, schemaVersion: 1, revision: 4, snapshot: { malformed: true } };
  const fixture = recoveryFixture(damaged);
  const session = createDirectorScenePersistenceSession(id, { adapter: fixture.adapter });
  let recoveryToken = "";
  await assert.rejects(session.load(), (error) => { recoveryToken = error.recoveryToken; return Boolean(recoveryToken); });

  fixture.setActive({ ...damaged, concurrentWriter: "other-tab" });
  await assert.rejects(session.recover(recoveryToken), DirectorSceneConflictError);
  assert.equal(fixture.getActive().concurrentWriter, "other-tab");
  assert.deepEqual(fixture.getQuarantine(recoveryToken).record, damaged);

  const validRecord = { id, schemaVersion: 1, revision: 5, snapshot: snapshot("#556677") };
  fixture.setActive(validRecord);
  await assert.rejects(session.recover(recoveryToken), /已经有效/);
  assert.deepEqual(fixture.getActive(), validRecord, "normal records cannot be cleared through the recovery action");
  await session.close();
});
