import { validateDirectorSceneSnapshot } from "./directorPersistence.js";

export const DIRECTOR_DESK_PROTOCOL = 2;
export const DIRECTOR_DESK_SANDBOX_ORIGIN = "null";

export const DIRECTOR_DESK_MESSAGES = Object.freeze({
  connect: "storyai:director-desk-connect",
  ready: "storyai:director-desk-ready",
  close: "storyai:director-desk-close",
  captures: "storyai:director-desk-captures",
  captureResult: "storyai:director-desk-capture-result",
  sceneSave: "storyai:director-desk-scene-save",
  sceneSaved: "storyai:director-desk-scene-saved",
  session: "storyai:director-desk-session",
  sessionAck: "storyai:director-desk-session-ack",
  panorama: "storyai:director-desk-panorama",
  panoramaRemoved: "storyai:director-desk-panorama-removed",
});

const MAX_CAPTURE_COUNT = 12;
const MAX_CAPTURE_BYTES = 12 * 1024 * 1024;
const MAX_CAPTURE_BATCH_BYTES = 48 * 1024 * 1024;
const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];
const CHILD_PORT_TYPES = new Set([
  DIRECTOR_DESK_MESSAGES.sessionAck,
  DIRECTOR_DESK_MESSAGES.close,
  DIRECTOR_DESK_MESSAGES.captures,
  DIRECTOR_DESK_MESSAGES.sceneSave,
  DIRECTOR_DESK_MESSAGES.panoramaRemoved,
]);

function requiredText(value, label, maxLength = 2048) {
  if (typeof value !== "string" || !value.trim() || value.length > maxLength) throw new TypeError(`${label}无效。`);
  return value.trim();
}

function requiredSequence(value) {
  if (!Number.isSafeInteger(value) || value < 1) throw new TypeError("导演台消息序号无效。");
  return value;
}

function sameBinding(left, right) {
  return Boolean(
    left && right
      && left.instanceId === right.instanceId
      && left.sessionNonce === right.sessionNonce
      && left.epoch === right.epoch,
  );
}

export function createDirectorDeskSessionId(scopeKey, shotId) {
  const scope = requiredText(scopeKey, "导演场景工作区");
  const shot = requiredText(shotId, "导演场景镜头", 256);
  return ["xutian-director-v1", scope, shot].map((part) => encodeURIComponent(part)).join(":");
}

export function createDirectorDeskBinding(instanceId, epoch = 1) {
  if (!Number.isSafeInteger(epoch) || epoch < 1) throw new TypeError("导演台连接代次无效。");
  return Object.freeze({
    instanceId: requiredText(instanceId, "导演场景会话"),
    sessionNonce: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    epoch,
  });
}

export function supportsDirectorDeskWebGl(documentRef = globalThis.document) {
  try {
    const canvas = documentRef?.createElement?.("canvas");
    if (!canvas?.getContext) return false;
    const context = canvas.getContext("webgl2", { failIfMajorPerformanceCaveat: true })
      || canvas.getContext("webgl", { failIfMajorPerformanceCaveat: true })
      || canvas.getContext("experimental-webgl", { failIfMajorPerformanceCaveat: true });
    if (!context) return false;
    context.getExtension?.("WEBGL_lose_context")?.loseContext?.();
    return true;
  } catch { return false; }
}

function isEnvelope(data) {
  return Boolean(data && typeof data === "object" && !Array.isArray(data) && data.protocolVersion === DIRECTOR_DESK_PROTOCOL);
}

export function parseDirectorDeskReadyMessage(event, { source, origin, instanceId }) {
  if (!event || event.source !== source || event.origin !== origin || !isEnvelope(event.data)) return null;
  if (event.data.type !== DIRECTOR_DESK_MESSAGES.ready) return null;
  if (event.data.instanceId !== instanceId) return null;
  return { type: event.data.type, instanceId: event.data.instanceId };
}

export function parseDirectorDeskPortMessage(data, { binding, lastSequence = 0, allowedTypes = CHILD_PORT_TYPES } = {}) {
  if (!isEnvelope(data) || !allowedTypes.has(data.type) || !sameBinding(data, binding)) return null;
  if (!Number.isSafeInteger(lastSequence) || lastSequence < 0) return null;
  if (!Number.isSafeInteger(data.sequence) || data.sequence <= lastSequence) return null;
  if (data.type === DIRECTOR_DESK_MESSAGES.captures) {
    const captures = data.payload?.captures;
    if (!Array.isArray(captures) || captures.length < 1 || captures.length > MAX_CAPTURE_COUNT) return null;
    if (typeof data.payload?.requestId !== "string" || !data.payload.requestId.trim()) return null;
  }
  if (data.type === DIRECTOR_DESK_MESSAGES.sceneSave) {
    if (!data.payload?.snapshot || typeof data.payload.snapshot !== "object" || Array.isArray(data.payload.snapshot)) return null;
  }
  if (data.type === DIRECTOR_DESK_MESSAGES.sessionAck) {
    if (typeof data.payload?.accepted !== "boolean" || data.payload?.schemaVersion !== 1) return null;
  }
  return { type: data.type, payload: data.payload ?? null, sequence: data.sequence };
}

export function createDirectorDeskConnectMessage(binding) {
  return {
    type: DIRECTOR_DESK_MESSAGES.connect,
    protocolVersion: DIRECTOR_DESK_PROTOCOL,
    instanceId: requiredText(binding?.instanceId, "导演场景会话"),
    sessionNonce: requiredText(binding?.sessionNonce, "导演台连接随机数", 256),
    epoch: binding?.epoch,
  };
}

export function createDirectorDeskPortMessage(binding, type, sequence, payload = null) {
  if (!Object.values(DIRECTOR_DESK_MESSAGES).includes(type)) throw new TypeError("导演台消息类型无效。");
  return {
    type,
    protocolVersion: DIRECTOR_DESK_PROTOCOL,
    instanceId: requiredText(binding?.instanceId, "导演场景会话"),
    sessionNonce: requiredText(binding?.sessionNonce, "导演台连接随机数", 256),
    epoch: binding?.epoch,
    sequence: requiredSequence(sequence),
    payload,
  };
}

export function createDirectorDeskSessionMessage(binding, sequence, { snapshot = null, revision = 0 } = {}) {
  if (!Number.isSafeInteger(revision) || revision < 0) throw new TypeError("导演场景版本无效。");
  const safeSnapshot = snapshot === null ? null : validateDirectorSceneSnapshot(snapshot).snapshot;
  return createDirectorDeskPortMessage(binding, DIRECTOR_DESK_MESSAGES.session, sequence, {
    theme: "light",
    schemaVersion: 1,
    revision,
    snapshot: safeSnapshot,
  });
}

function safeCaptureName(value, index) {
  const base = typeof value === "string" ? value.trim() : "";
  const normalized = base.replace(/[\\/:*?"<>|\u0000-\u001f\u007f]+/g, "-").replace(/\s+/g, " ").slice(0, 116);
  const withExtension = normalized || `director-shot-${index + 1}.png`;
  return withExtension.toLowerCase().endsWith(".png") ? withExtension : `${withExtension}.png`;
}

function decodePngDataUrl(dataUrl) {
  if (typeof dataUrl !== "string" || !dataUrl.startsWith("data:image/png;base64,")) throw new TypeError("导演台只允许传回 PNG 截图。");
  const encoded = dataUrl.slice("data:image/png;base64,".length);
  if (!encoded || encoded.length > Math.ceil(MAX_CAPTURE_BYTES * 4 / 3) + 4) throw new RangeError("导演台截图过大，未加入当前镜头。");
  let binary;
  try { binary = globalThis.atob(encoded); } catch { throw new TypeError("导演台截图内容无效。"); }
  if (binary.length > MAX_CAPTURE_BYTES) throw new RangeError("导演台截图过大，未加入当前镜头。");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  if (PNG_SIGNATURE.some((value, index) => bytes[index] !== value)) throw new TypeError("导演台截图未通过 PNG 文件校验。");
  return bytes;
}

export function directorCapturePayloadToFiles(payload) {
  const captures = payload?.captures;
  if (!Array.isArray(captures) || captures.length < 1 || captures.length > MAX_CAPTURE_COUNT) throw new TypeError("导演台截图批次无效。");
  let totalBytes = 0;
  return captures.map((capture, index) => {
    if (!capture || typeof capture !== "object" || Array.isArray(capture)) throw new TypeError("导演台截图项目无效。");
    const bytes = decodePngDataUrl(capture.dataUrl);
    totalBytes += bytes.byteLength;
    if (totalBytes > MAX_CAPTURE_BATCH_BYTES) throw new RangeError("导演台截图批次过大，未加入当前镜头。");
    return new File([bytes], safeCaptureName(capture.fileName, index), { type: "image/png", lastModified: Date.now() });
  });
}

export function requireDirectorCaptureCommit(result) {
  if (!result || result.ok !== true || !Number.isInteger(result.addedCount) || result.addedCount < 1) {
    throw new Error(typeof result?.message === "string" && result.message.trim() ? result.message.trim() : "取景图未能加入当前镜头。");
  }
  return { addedCount: result.addedCount, message: typeof result.message === "string" ? result.message.trim() : "" };
}

// Kept as a narrow compatibility entry for tests and pre-connect ready events.
export function parseDirectorDeskMessage(event, options) {
  return parseDirectorDeskReadyMessage(event, options);
}
