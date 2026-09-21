const DATABASE_NAME = "xutian-director-local-v1";
const DATABASE_VERSION = 2;
const SCENE_STORE = "scenes";
const QUARANTINE_STORE = "scene_quarantine";

// Snapshots are structured-cloned, transported and retained by the editor.
// Keep one scene comfortably below per-origin memory and messaging limits.
export const MAX_DIRECTOR_SCENE_BYTES = 16 * 1024 * 1024;
const MAX_ASSETS = 128;
const MAX_OBJECTS = 1024;
const MAX_CAMERAS = 64;
const MAX_CAPTURES_PER_CAMERA = 48;
const MAX_TREE_DEPTH = 32;
const MAX_TREE_NODES = 50_000;
const MAX_STRING_BYTES = 8 * 1024 * 1024;
const ROOT_KEYS = new Set([
  "viewMode", "selectedObjectId", "selectedObjectIds", "selectedCrowdId",
  "directorInspectorMode", "transformMode", "viewportAspectRatio",
  "viewportRuleOfThirdsEnabled", "viewportPanelsCollapsed", "project",
]);
const PROJECT_KEYS = new Set([
  "version", "scene", "assets", "objects", "cameras", "activeCameraId", "panoramaAssetId",
]);
const FORBIDDEN_SECRET_KEY = /^(?:access[_-]?token|refresh[_-]?token|authorization|api[_-]?key|secret|idempotency[_-]?key|quote|price)$/i;

let databasePromise;
let databaseGeneration = 0;
const coordinators = new Map();

export class DirectorSceneConflictError extends Error {
  constructor(message = "导演场景已在另一个页面更新，请重新载入后继续。") {
    super(message);
    this.name = "DirectorSceneConflictError";
  }
}

export class DirectorSceneCorruptError extends Error {
  constructor(message = "本机导演场景记录已损坏，原记录已保留，未自动覆盖。") {
    super(message);
    this.name = "DirectorSceneCorruptError";
  }
}

export class DirectorSceneRecoveryRequiredError extends DirectorSceneCorruptError {
  constructor(recoveryToken, message = "本机导演场景记录已损坏，原记录已隔离保留，未自动覆盖。") {
    super(message);
    this.name = "DirectorSceneRecoveryRequiredError";
    this.recoveryToken = typeof recoveryToken === "string" ? recoveryToken : "";
  }
}

function requiredSceneId(value) {
  if (typeof value !== "string" || !value.trim() || value.length > 4096) {
    throw new TypeError("导演场景标识无效。");
  }
  return value.trim();
}

function isPlainObject(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function ensureExactKeys(value, allowed, label) {
  for (const key of Object.keys(value)) {
    if (!allowed.has(key)) throw new TypeError(`${label}包含不受支持的字段。`);
  }
}

function inspectSnapshotTree(root) {
  const encoder = new TextEncoder();
  const stack = [{ value: root, depth: 0 }];
  const seen = new Set();
  let nodes = 0;
  while (stack.length) {
    const { value, depth } = stack.pop();
    nodes += 1;
    if (nodes > MAX_TREE_NODES) throw new RangeError("导演场景结构过大，未保存。");
    if (depth > MAX_TREE_DEPTH) throw new RangeError("导演场景嵌套过深，未保存。");
    if (typeof value === "number" && !Number.isFinite(value)) throw new TypeError("导演场景包含无效数值。");
    if (typeof value === "string") {
      if (encoder.encode(value).byteLength > MAX_STRING_BYTES) {
        throw new RangeError("导演场景包含过大的内嵌资源，请减少本地模型或截图。");
      }
      continue;
    }
    if (!value || typeof value !== "object") continue;
    if (seen.has(value)) throw new TypeError("导演场景不能包含循环引用。");
    seen.add(value);
    if (Array.isArray(value)) {
      for (let index = value.length - 1; index >= 0; index -= 1) {
        stack.push({ value: value[index], depth: depth + 1 });
      }
      continue;
    }
    if (!isPlainObject(value)) throw new TypeError("导演场景只能包含可序列化数据。");
    for (const [key, child] of Object.entries(value)) {
      if (FORBIDDEN_SECRET_KEY.test(key)) throw new TypeError("导演场景不能包含凭据、报价或幂等信息。");
      stack.push({ value: child, depth: depth + 1 });
    }
  }
}

function cloneSnapshot(value) {
  if (typeof globalThis.structuredClone === "function") return globalThis.structuredClone(value);
  return JSON.parse(JSON.stringify(value));
}

export function validateDirectorSceneSnapshot(value) {
  if (!isPlainObject(value)) throw new TypeError("导演场景快照无效。");
  ensureExactKeys(value, ROOT_KEYS, "导演场景快照");
  const project = value.project;
  if (!isPlainObject(project)) throw new TypeError("导演场景结构无效。");
  ensureExactKeys(project, PROJECT_KEYS, "导演场景工程");
  if (
    project.version !== 1 || !isPlainObject(project.scene)
      || !Array.isArray(project.assets) || !Array.isArray(project.objects) || !Array.isArray(project.cameras)
      || project.assets.length > MAX_ASSETS || project.objects.length > MAX_OBJECTS || project.cameras.length > MAX_CAMERAS
      || project.cameras.some((camera) => Array.isArray(camera?.captures) && camera.captures.length > MAX_CAPTURES_PER_CAMERA)
  ) throw new TypeError("导演场景结构无效。");
  inspectSnapshotTree(value);
  let serialized;
  try { serialized = JSON.stringify(value); } catch { throw new TypeError("导演场景无法序列化。"); }
  const size = new TextEncoder().encode(serialized).byteLength;
  if (size > MAX_DIRECTOR_SCENE_BYTES) throw new RangeError("导演场景超过本机保存上限，请移除过大的模型或截图。");
  return { snapshot: cloneSnapshot(value), size };
}

function resetDatabaseReference(database, generation) {
  if (generation !== databaseGeneration) return;
  databasePromise = undefined;
  try { database?.close?.(); } catch { /* Already closed. */ }
}

function openDatabase(indexedDb = globalThis.indexedDB) {
  if (!indexedDb?.open) return Promise.reject(new Error("当前浏览器不支持导演场景的本机存储。"));
  if (databasePromise) return databasePromise;
  const generation = ++databaseGeneration;
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDb.open(DATABASE_NAME, DATABASE_VERSION);
    let settled = false;
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(SCENE_STORE)) database.createObjectStore(SCENE_STORE, { keyPath: "id" });
      if (!database.objectStoreNames.contains(QUARANTINE_STORE)) database.createObjectStore(QUARANTINE_STORE, { keyPath: "quarantineId" });
    };
    request.onsuccess = () => {
      const database = request.result;
      if (settled) {
        try { database.close(); } catch { /* Late success after blocked/error. */ }
        return;
      }
      settled = true;
      database.onversionchange = () => resetDatabaseReference(database, generation);
      database.onclose = () => { if (generation === databaseGeneration) databasePromise = undefined; };
      resolve(database);
    };
    request.onerror = () => {
      if (settled) return;
      settled = true;
      if (generation === databaseGeneration) databasePromise = undefined;
      reject(request.error || new Error("无法打开导演场景的本机存储。"));
    };
    request.onblocked = () => {
      if (settled) return;
      settled = true;
      if (generation === databaseGeneration) databasePromise = undefined;
      reject(new Error("导演场景存储正在被另一个页面升级，请关闭旧页面后重试。"));
    };
  });
  return databasePromise;
}

function requestResult(request, fallbackMessage) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error(fallbackMessage));
  });
}

function transactionComplete(transaction, fallbackMessage) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error || new Error(fallbackMessage));
    transaction.onabort = () => reject(transaction.error || new DOMException(fallbackMessage, "AbortError"));
  });
}

async function quarantineRecord(database, id, record, reason) {
  const quarantineId = `director-quarantine:${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`}`;
  const transaction = database.transaction([QUARANTINE_STORE], "readwrite");
  const completion = transactionComplete(transaction, "损坏的导演场景记录无法隔离。");
  transaction.objectStore(QUARANTINE_STORE).put({
    quarantineId,
    originalId: id,
    quarantinedAt: new Date().toISOString(),
    reason: String(reason || "invalid record").slice(0, 512),
    record,
  });
  await completion;
  return quarantineId;
}

function recoveryRecordsMatch(left, right) {
  const stack = [[left, right]];
  const pairs = new Map();
  let nodes = 0;
  while (stack.length) {
    const [a, b] = stack.pop();
    nodes += 1;
    if (nodes > MAX_TREE_NODES * 2) return false;
    if (Object.is(a, b)) continue;
    if (typeof a !== typeof b || !a || !b || typeof a !== "object") return false;
    if (pairs.has(a)) {
      if (pairs.get(a) !== b) return false;
      continue;
    }
    pairs.set(a, b);
    if (Array.isArray(a) || Array.isArray(b)) {
      if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
      for (let index = a.length - 1; index >= 0; index -= 1) stack.push([a[index], b[index]]);
      continue;
    }
    if (!isPlainObject(a) || !isPlainObject(b)) return false;
    const leftKeys = Object.keys(a);
    const rightKeys = Object.keys(b);
    if (leftKeys.length !== rightKeys.length) return false;
    const rightKeySet = new Set(rightKeys);
    for (let index = leftKeys.length - 1; index >= 0; index -= 1) {
      const key = leftKeys[index];
      if (!rightKeySet.has(key)) return false;
      stack.push([a[key], b[key]]);
    }
  }
  return true;
}

function normalizeRecord(record, id) {
  if (!record) return { snapshot: null, revision: 0 };
  if (!isPlainObject(record) || record.id !== id || record.schemaVersion !== 1 || !Number.isSafeInteger(record.revision) || record.revision < 1) {
    throw new DirectorSceneCorruptError();
  }
  const validated = validateDirectorSceneSnapshot(record.snapshot);
  return { snapshot: validated.snapshot, revision: record.revision };
}

export function assertDirectorSceneRecoveryCandidate(instanceId, recoveryToken, current, quarantine) {
  const id = requiredSceneId(instanceId);
  const quarantineId = requiredSceneId(recoveryToken);
  if (!current || !isPlainObject(quarantine) || quarantine.quarantineId !== quarantineId || quarantine.originalId !== id) {
    throw new DirectorSceneConflictError("隔离副本或当前场景已经变化，未执行重建。");
  }
  try {
    normalizeRecord(current, id);
    throw new DirectorSceneConflictError("当前场景已经有效，未执行重建。");
  } catch (error) {
    if (error instanceof DirectorSceneConflictError) throw error;
  }
  if (!Object.prototype.hasOwnProperty.call(quarantine, "record") || !recoveryRecordsMatch(current, quarantine.record)) {
    throw new DirectorSceneConflictError("当前场景在确认后又发生变化，未执行重建。");
  }
  return true;
}

export async function loadDirectorScene(instanceId) {
  const id = requiredSceneId(instanceId);
  const database = await openDatabase();
  const transaction = database.transaction(SCENE_STORE, "readonly");
  const record = await requestResult(transaction.objectStore(SCENE_STORE).get(id), "无法读取导演场景。");
  await transactionComplete(transaction, "无法读取导演场景。");
  try { return normalizeRecord(record, id); } catch (error) {
    let recoveryToken = "";
    try { recoveryToken = await quarantineRecord(database, id, record, error?.message); } catch { /* Original stays untouched. */ }
    if (recoveryToken) throw new DirectorSceneRecoveryRequiredError(recoveryToken, error?.message || undefined);
    throw error instanceof DirectorSceneCorruptError ? error : new DirectorSceneCorruptError(error?.message || undefined);
  }
}

export async function recoverDirectorSceneFromQuarantine(instanceId, recoveryToken) {
  const id = requiredSceneId(instanceId);
  const quarantineId = requiredSceneId(recoveryToken);
  const database = await openDatabase();
  const transaction = database.transaction([SCENE_STORE, QUARANTINE_STORE], "readwrite");
  const completion = transactionComplete(transaction, "导演场景恢复事务未能完成。");
  const abort = () => { try { transaction.abort(); } catch { /* Already complete. */ } };
  try {
    const sceneStore = transaction.objectStore(SCENE_STORE);
    const quarantineStore = transaction.objectStore(QUARANTINE_STORE);
    const [current, quarantine] = await Promise.all([
      requestResult(sceneStore.get(id), "无法确认当前导演场景。"),
      requestResult(quarantineStore.get(quarantineId), "无法读取导演场景隔离副本。"),
    ]);
    assertDirectorSceneRecoveryCandidate(id, quarantineId, current, quarantine);
    sceneStore.delete(id);
    quarantineStore.put({
      ...quarantine,
      explicitlyReleasedAt: new Date().toISOString(),
      recoveryAction: "empty-scene-rebuild",
    });
    await completion;
    return { revision: 0, quarantineId };
  } catch (error) {
    abort();
    await completion.catch(() => undefined);
    throw error;
  }
}

export async function saveDirectorScene(instanceId, snapshot, { expectedRevision = 0, writerEpoch = "", signal } = {}) {
  const id = requiredSceneId(instanceId);
  if (!Number.isSafeInteger(expectedRevision) || expectedRevision < 0) throw new TypeError("导演场景版本无效。");
  if (signal?.aborted) throw new DOMException("导演场景保存已取消。", "AbortError");
  const validated = validateDirectorSceneSnapshot(snapshot);
  const database = await openDatabase();
  const transaction = database.transaction(SCENE_STORE, "readwrite");
  const abort = () => { try { transaction.abort(); } catch { /* Already complete. */ } };
  signal?.addEventListener?.("abort", abort, { once: true });
  try {
    const store = transaction.objectStore(SCENE_STORE);
    const current = await requestResult(store.get(id), "无法读取导演场景版本。");
    const currentRevision = current ? normalizeRecord(current, id).revision : 0;
    if (currentRevision !== expectedRevision) {
      abort();
      throw new DirectorSceneConflictError();
    }
    const revision = currentRevision + 1;
    store.put({
      id, schemaVersion: 1, revision, writerEpoch: String(writerEpoch || "").slice(0, 256),
      updatedAt: new Date().toISOString(), size: validated.size, snapshot: validated.snapshot,
    });
    await transactionComplete(transaction, "导演场景未能保存到当前设备。");
    return { size: validated.size, revision };
  } catch (error) {
    if (error?.name === "QuotaExceededError") throw new Error("本机存储空间不足，场景仍可继续编辑，但刷新后可能丢失。");
    throw error;
  } finally {
    signal?.removeEventListener?.("abort", abort);
  }
}

function defaultPersistenceAdapter() {
  return { load: loadDirectorScene, save: saveDirectorScene, recover: recoverDirectorSceneFromQuarantine };
}

function coordinatorFor(id) {
  let coordinator = coordinators.get(id);
  if (!coordinator) {
    coordinator = { active: null, drain: Promise.resolve() };
    coordinators.set(id, coordinator);
  }
  return coordinator;
}

export function createDirectorScenePersistenceSession(instanceId, { adapter = defaultPersistenceAdapter() } = {}) {
  const id = requiredSceneId(instanceId);
  const coordinator = coordinatorFor(id);
  coordinator.active?.close?.("导演场景已在新的编辑器会话中打开。");
  const predecessorDrain = coordinator.drain;
  const writerEpoch = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const abortController = new AbortController();
  let closed = false;
  let revision = 0;
  let pending = null;
  let running = null;
  const session = {
    writerEpoch,
    async load() {
      await predecessorDrain.catch(() => undefined);
      if (closed) throw new DOMException("导演场景会话已关闭。", "AbortError");
      const loaded = await adapter.load(id);
      revision = loaded.revision;
      return loaded;
    },
    save(snapshot) {
      if (closed) return Promise.reject(new DOMException("导演场景会话已关闭。", "AbortError"));
      const validated = validateDirectorSceneSnapshot(snapshot);
      return new Promise((resolve, reject) => {
        if (pending) pending.resolve({ superseded: true, revision });
        pending = { snapshot: validated.snapshot, resolve, reject };
        session.flush();
      });
    },
    async recover(recoveryToken) {
      await predecessorDrain.catch(() => undefined);
      if (closed) throw new DOMException("导演场景会话已关闭。", "AbortError");
      if (running || pending || typeof adapter.recover !== "function") {
        throw new DirectorSceneConflictError("导演场景仍有写入任务，未执行重建。");
      }
      const result = await adapter.recover(id, recoveryToken, { writerEpoch, signal: abortController.signal });
      revision = 0;
      return result;
    },
    flush() {
      if (closed || running || !pending) return running || Promise.resolve();
      const next = pending;
      pending = null;
      running = Promise.resolve()
        .then(() => adapter.save(id, next.snapshot, { expectedRevision: revision, writerEpoch, signal: abortController.signal }))
        .then((result) => {
          revision = result.revision;
          next.resolve({ ...result, superseded: false });
          return result;
        }, (error) => { next.reject(error); throw error; })
        .finally(() => {
          running = null;
          if (!closed && pending) session.flush();
        });
      coordinator.drain = running.catch(() => undefined);
      return running;
    },
    close(reason = "导演场景编辑器已关闭。") {
      if (closed) return coordinator.drain;
      closed = true;
      if (pending) {
        pending.reject(new DOMException(reason, "AbortError"));
        pending = null;
      }
      abortController.abort();
      if (coordinator.active === session) coordinator.active = null;
      return coordinator.drain;
    },
    getRevision() { return revision; },
  };
  coordinator.active = session;
  return session;
}
