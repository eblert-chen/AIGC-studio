import { getDirectorHostSnapshot, openDirectorHostSnapshot, useDirectorStore } from "../store/directorStore";
import {
  createDirectorChildEnvelope,
  DIRECTOR_HOST_MESSAGES,
  DIRECTOR_HOST_PROTOCOL,
  parseDirectorConnectEnvelope,
  parseDirectorHostEnvelope,
} from "./hostProtocol";

interface DirectorBinding {
  instanceId: string;
  sessionNonce: string;
  epoch: number;
}

interface HostPanoramaPayload {
  edgeId?: unknown;
  sourceNodeId?: unknown;
  imageUrl?: unknown;
  fileName?: unknown;
}

interface HostSessionPayload {
  theme?: unknown;
  schemaVersion?: unknown;
  revision?: unknown;
  snapshot?: unknown;
}

export interface HostCaptureItemPayload { dataUrl?: unknown; fileName?: unknown; }

interface HostConnectedPanorama { edgeId: string; sourceNodeId: string; }

interface CaptureCommitResult { ok: boolean; addedCount: number; message: string; }

let initialized = false;
let hostConnectedPanorama: HostConnectedPanorama | null = null;
let removeUnsubscribe: (() => void) | null = null;
let removePersistenceUnsubscribe: (() => void) | null = null;
let persistenceTimer: number | null = null;
let activeHostInstanceId = "";
let activeBinding: DirectorBinding | null = null;
let hostPort: MessagePort | null = null;
let inboundSequence = 0;
let outboundSequence = 0;
let sceneSaveInFlight = 0;
let sceneDirty = false;
let suppressNextPanoramaRemovalNotice = false;
const pendingCaptures = new Map<string, {
  resolve: (value: CaptureCommitResult) => void;
  reject: (reason: Error) => void;
  timeout: number;
}>();

function normalizeString(value: unknown) { return typeof value === "string" ? value.trim() : ""; }

function getExpectedInstanceId() {
  try { return normalizeString(new URLSearchParams(window.location.search).get("instanceId")); }
  catch { return ""; }
}

function getHostOrigin() {
  try {
    const configured = new URLSearchParams(window.location.search).get("parentOrigin");
    if (configured) {
      const parsed = new URL(configured);
      if ((parsed.protocol === "https:" || parsed.protocol === "http:") && parsed.origin === configured) return parsed.origin;
    }
  } catch { /* Fall through for standalone development. */ }
  return window.location.origin;
}

function postWindowHost(message: unknown) { window.parent?.postMessage(message, getHostOrigin()); }

function postPort(type: string, payload: unknown = null) {
  if (!activeBinding || !hostPort) return 0;
  outboundSequence += 1;
  hostPort.postMessage(createDirectorChildEnvelope(activeBinding, type, outboundSequence, payload));
  return outboundSequence;
}

function normalizeTheme(value: unknown): "dark" | "light" | null { return value === "light" || value === "dark" ? value : null; }

function applyDirectorDeskTheme(theme: "dark" | "light") {
  document.documentElement.dataset.theme = theme;
  document.documentElement.classList.toggle("dark", theme === "dark");
}

function getInitialHostTheme() {
  try { return normalizeTheme(new URLSearchParams(window.location.search).get("theme")); }
  catch { return null; }
}

function safeEmbeddedPanorama(value: unknown) {
  const url = normalizeString(value);
  if (!/^data:image\/(?:png|jpeg|webp);base64,[a-z0-9+/=]+$/i.test(url)) return "";
  return url.length <= 8 * 1024 * 1024 ? url : "";
}

function notifyPanoramaRemoved() {
  if (!hostConnectedPanorama) return;
  postPort(DIRECTOR_HOST_MESSAGES.panoramaRemoved, hostConnectedPanorama);
  hostConnectedPanorama = null;
}

function subscribeToPanoramaRemoval() {
  if (removeUnsubscribe) return;
  let previousPanoramaAssetId = useDirectorStore.getState().project.panoramaAssetId;
  removeUnsubscribe = useDirectorStore.subscribe((state) => {
    const nextPanoramaAssetId = state.project.panoramaAssetId;
    if (previousPanoramaAssetId && !nextPanoramaAssetId) {
      if (suppressNextPanoramaRemovalNotice) {
        suppressNextPanoramaRemovalNotice = false;
        hostConnectedPanorama = null;
      } else notifyPanoramaRemoved();
    }
    previousPanoramaAssetId = nextPanoramaAssetId;
  });
}

function postSceneSnapshot() {
  persistenceTimer = null;
  if (!activeHostInstanceId || !activeBinding || !hostPort) return;
  if (sceneSaveInFlight) {
    sceneDirty = true;
    return;
  }
  sceneDirty = false;
  sceneSaveInFlight = postPort(DIRECTOR_HOST_MESSAGES.sceneSave, { snapshot: getDirectorHostSnapshot() });
}

function scheduleSceneSnapshot() {
  if (!activeHostInstanceId) return;
  sceneDirty = true;
  if (persistenceTimer !== null) window.clearTimeout(persistenceTimer);
  persistenceTimer = window.setTimeout(postSceneSnapshot, 900);
}

function subscribeToScenePersistence() {
  if (removePersistenceUnsubscribe) return;
  removePersistenceUnsubscribe = useDirectorStore.subscribe(scheduleSceneSnapshot);
}

function importHostPanorama(payload: HostPanoramaPayload) {
  const imageUrl = safeEmbeddedPanorama(payload.imageUrl);
  if (!imageUrl) return;
  const fileName = normalizeString(payload.fileName) || "画布全景图.png";
  const edgeId = normalizeString(payload.edgeId);
  const sourceNodeId = normalizeString(payload.sourceNodeId);
  hostConnectedPanorama = edgeId && sourceNodeId ? { edgeId, sourceNodeId } : null;
  useDirectorStore.getState().addImportedAsset({ kind: "panorama", name: fileName, fileName, url: imageUrl, projectionMode: "backdrop" });
}

function openHostSession(payload: HostSessionPayload) {
  const theme = normalizeTheme(payload.theme);
  if (theme) applyDirectorDeskTheme(theme);
  if (!activeBinding || payload.schemaVersion !== 1 || !Number.isSafeInteger(payload.revision) || Number(payload.revision) < 0) {
    postPort(DIRECTOR_HOST_MESSAGES.sessionAck, { accepted: false, schemaVersion: 1, snapshotRestored: false });
    return;
  }
  activeHostInstanceId = "";
  suppressNextPanoramaRemovalNotice = Boolean(useDirectorStore.getState().project.panoramaAssetId);
  const snapshotProvided = payload.snapshot !== null && payload.snapshot !== undefined;
  const restored = openDirectorHostSnapshot(activeBinding.instanceId, payload.snapshot);
  suppressNextPanoramaRemovalNotice = false;
  hostConnectedPanorama = null;
  if (snapshotProvided && !restored) {
    postPort(DIRECTOR_HOST_MESSAGES.sessionAck, { accepted: false, schemaVersion: 1, snapshotRestored: false });
    return;
  }
  activeHostInstanceId = activeBinding.instanceId;
  postPort(DIRECTOR_HOST_MESSAGES.sessionAck, {
    accepted: true,
    schemaVersion: 1,
    revision: payload.revision,
    snapshotRestored: restored,
  });
}

function finishCapture(payload: unknown) {
  const result = payload as Partial<CaptureCommitResult> & { requestId?: unknown };
  const requestId = normalizeString(result.requestId);
  const pending = pendingCaptures.get(requestId);
  if (!pending || typeof result.ok !== "boolean" || !Number.isInteger(result.addedCount)) return;
  window.clearTimeout(pending.timeout);
  pendingCaptures.delete(requestId);
  const message = normalizeString(result.message) || (result.ok ? "取景图已加入当前镜头。" : "取景图未能加入当前镜头。");
  if (result.ok && Number(result.addedCount) > 0) pending.resolve({ ok: true, addedCount: Number(result.addedCount), message });
  else pending.reject(new Error(message));
}

function handleSceneSaved(payload: unknown) {
  const result = payload as { replyToSequence?: unknown; ok?: unknown };
  if (result.replyToSequence !== sceneSaveInFlight || typeof result.ok !== "boolean") return;
  sceneSaveInFlight = 0;
  if (result.ok && sceneDirty) scheduleSceneSnapshot();
  else if (!result.ok) sceneDirty = false;
}

function handleHostPortMessage(event: MessageEvent) {
  const message = parseDirectorHostEnvelope(event.data, { binding: activeBinding, lastSequence: inboundSequence });
  if (!message) return;
  inboundSequence = message.sequence;
  if (message.type === DIRECTOR_HOST_MESSAGES.session) openHostSession(message.payload || {});
  else if (message.type === DIRECTOR_HOST_MESSAGES.captureResult) finishCapture(message.payload);
  else if (message.type === DIRECTOR_HOST_MESSAGES.sceneSaved) handleSceneSaved(message.payload);
  else if (message.type === DIRECTOR_HOST_MESSAGES.panorama) importHostPanorama(message.payload || {});
}

function rejectPendingCaptures(message: string) {
  for (const pending of pendingCaptures.values()) {
    window.clearTimeout(pending.timeout);
    pending.reject(new Error(message));
  }
  pendingCaptures.clear();
}

function handleHostMessage(event: MessageEvent) {
  if (event.source !== window.parent || event.origin !== getHostOrigin()) return;
  const binding = parseDirectorConnectEnvelope(event.data, getExpectedInstanceId());
  const port = event.ports?.[0];
  if (!binding || !port) return;
  rejectPendingCaptures("导演台与宿主重新连接，请重新发送取景图。");
  hostPort?.close();
  activeHostInstanceId = "";
  activeBinding = binding;
  hostPort = port;
  inboundSequence = 0;
  outboundSequence = 0;
  sceneSaveInFlight = 0;
  sceneDirty = false;
  hostPort.onmessage = handleHostPortMessage;
  hostPort.start();
}

export function postDirectorDeskCapturesToHost(captures: Array<{ dataUrl: string; fileName?: string }>) {
  const normalizedCaptures = captures.map((capture, index) => {
    const dataUrl = normalizeString(capture.dataUrl);
    return dataUrl ? { dataUrl, fileName: normalizeString(capture.fileName) || `director-desk-capture-${index + 1}.png` } : null;
  }).filter((capture): capture is { dataUrl: string; fileName: string } => Boolean(capture));
  if (!activeHostInstanceId || !activeBinding || !hostPort) return Promise.reject(new Error("导演台尚未连接当前镜头。"));
  if (!normalizedCaptures.length) return Promise.reject(new Error("没有可发送的截图。"));
  const requestId = crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return new Promise<CaptureCommitResult>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      pendingCaptures.delete(requestId);
      reject(new Error("宿主没有确认取景图，请检查当前镜头后重试。"));
    }, 30_000);
    pendingCaptures.set(requestId, { resolve, reject, timeout });
    postPort(DIRECTOR_HOST_MESSAGES.captures, { requestId, captures: normalizedCaptures });
  });
}

export function initDirectorDeskHostBridge() {
  if (initialized) return;
  initialized = true;
  applyDirectorDeskTheme(getInitialHostTheme() ?? "dark");
  window.addEventListener("message", handleHostMessage);
  subscribeToPanoramaRemoval();
  subscribeToScenePersistence();
}

export function postDirectorDeskReadyToHost() {
  postWindowHost({
    type: DIRECTOR_HOST_MESSAGES.ready,
    protocolVersion: DIRECTOR_HOST_PROTOCOL,
    instanceId: getExpectedInstanceId(),
  });
}

export function postDirectorDeskCloseToHost() { postPort(DIRECTOR_HOST_MESSAGES.close); }

export function clearDirectorDeskHostBridge() {
  if (!initialized) return;
  initialized = false;
  activeHostInstanceId = "";
  activeBinding = null;
  hostPort?.close();
  hostPort = null;
  inboundSequence = 0;
  outboundSequence = 0;
  sceneSaveInFlight = 0;
  sceneDirty = false;
  rejectPendingCaptures("导演台已关闭，取景图未发送。");
  hostConnectedPanorama = null;
  suppressNextPanoramaRemovalNotice = false;
  window.removeEventListener("message", handleHostMessage);
  removeUnsubscribe?.();
  removeUnsubscribe = null;
  removePersistenceUnsubscribe?.();
  removePersistenceUnsubscribe = null;
  if (persistenceTimer !== null) window.clearTimeout(persistenceTimer);
  persistenceTimer = null;
}
