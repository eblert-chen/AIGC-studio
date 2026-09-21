import { generationPromptLength } from "../modelCapabilities.js";

const PENDING_CREATE_STORAGE_PREFIX = "ai-video.pending-create";

function pendingCreateStorageKey(workspaceKey) {
  return `${PENDING_CREATE_STORAGE_PREFIX}:${encodeURIComponent(workspaceKey)}`;
}

export function makeIdempotencyKey() {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `task-${Date.now()}-${Math.random().toString(16).slice(2)}`
  );
}

export function taskRequestFingerprint(value) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `${value.length}:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export function filesFromPendingRequest(requestPayload) {
  const grouped = { image: [], video: [], audio: [] };
  for (const reference of requestPayload?.assets ?? []) {
    const kind = String(reference?.media_type ?? "").trim();
    if (!grouped[kind]) continue;
    const assetId = String(reference?.asset_id ?? reference?.id ?? "").trim();
    grouped[kind].push({
      id: assetId,
      asset_id: assetId,
      media_type: kind,
      original_filename: `已上传${kind === "image" ? "图片" : kind === "video" ? "视频" : "音频"}`,
      status: "active",
    });
  }
  return grouped;
}

export function readPendingCreate(workspaceKey, storage) {
  if (!workspaceKey) return null;
  try {
    if (storage === undefined) storage = globalThis.sessionStorage;
    const value = storage?.getItem(pendingCreateStorageKey(workspaceKey));
    if (!value) return null;
    const parsed = JSON.parse(value);
    if (
      !parsed ||
      ![1, 2, 3, 4, 5, 6].includes(parsed.version) ||
      String(parsed.workspaceKey || parsed.companyId || "") !== workspaceKey ||
      typeof parsed.fingerprint !== "string" ||
      typeof parsed.idempotencyKey !== "string" ||
      parsed.idempotencyKey.length < 8 ||
      parsed.idempotencyKey.length > 120 ||
      typeof parsed.modelId !== "string" ||
      !parsed.modelId ||
      (parsed.version >= 4 &&
        (!Number.isInteger(parsed.capabilityVersion) || parsed.capabilityVersion < 1)) ||
      (parsed.version >= 5 &&
        (typeof parsed.quoteRevision !== "string" ||
          !/^sha256:[0-9a-f]{64}$/.test(parsed.quoteRevision))) ||
      !parsed.requestPayload ||
      typeof parsed.requestPayload !== "object" ||
      Array.isArray(parsed.requestPayload)
    ) {
      return null;
    }
    if (parsed.version >= 6) {
      const context = parsed.creationContext;
      if (!context || typeof context !== "object" || Array.isArray(context)
        || Object.keys(context).some((key) => !["scopeKey", "kind", "entryId", "draftRevision"].includes(key))
        || context.scopeKey !== workspaceKey
        || !["quick", "console", "notebook", "canvas"].includes(context.kind)
        || typeof context.entryId !== "string"
        || (context.kind === "quick" ? context.entryId !== "" : !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$/.test(context.entryId))
        || ["__proto__", "prototype", "constructor"].includes(context.entryId)
        || !Number.isSafeInteger(context.draftRevision) || context.draftRevision < 0) return null;
    }
    const requestPayload = parsed.requestPayload;
    const allowedPayloadKeys = new Set([
      "mode",
      "prompt",
      "duration_seconds",
      "aspect_ratio",
      "resolution",
      "output_count",
      "face_enabled",
      "assets",
    ]);
    if (Object.keys(requestPayload).some((key) => !allowedPayloadKeys.has(key))) {
      return null;
    }
    const rawAssets = requestPayload.assets;
    if (rawAssets !== undefined && !Array.isArray(rawAssets)) return null;
    const assets = rawAssets ?? [];
    const validAssets = assets.every((asset) => (
      asset &&
      typeof asset === "object" &&
      !Array.isArray(asset) &&
      typeof asset.asset_id === "string" &&
      Boolean(asset.asset_id.trim()) &&
      ["image", "video", "audio"].includes(asset.media_type)
    ));
    if (
      !validAssets ||
      assets.length > 15 ||
      !["text_to_video", "image_to_video", "video_to_video", "text_to_image"].includes(
        requestPayload.mode,
      ) ||
      typeof requestPayload.prompt !== "string" ||
      !requestPayload.prompt.trim() ||
      generationPromptLength(requestPayload.prompt) > 10_000 ||
      !Number.isInteger(requestPayload.duration_seconds) ||
      requestPayload.duration_seconds <= 0 ||
      requestPayload.duration_seconds > 3600 ||
      typeof requestPayload.aspect_ratio !== "string" ||
      !requestPayload.aspect_ratio ||
      (requestPayload.resolution !== undefined &&
        (typeof requestPayload.resolution !== "string" || !requestPayload.resolution)) ||
      (requestPayload.face_enabled !== undefined &&
        typeof requestPayload.face_enabled !== "boolean") ||
      !Number.isInteger(requestPayload.output_count) ||
      requestPayload.output_count < 1 ||
      requestPayload.output_count > 16 ||
      (requestPayload.mode === "image_to_video" &&
        !assets.some((asset) => asset.media_type === "image")) ||
      (requestPayload.mode === "video_to_video" &&
        !assets.some((asset) => asset.media_type === "video"))
    ) {
      return null;
    }
    const fingerprint = taskRequestFingerprint(JSON.stringify(
      parsed.version >= 5
        ? {
            modelId: parsed.modelId,
            capabilityVersion: parsed.capabilityVersion,
            quoteRevision: parsed.quoteRevision,
            requestPayload,
            ...(parsed.version >= 6 ? { creationContext: parsed.creationContext } : {}),
          }
        : parsed.version >= 4
          ? {
              modelId: parsed.modelId,
              capabilityVersion: parsed.capabilityVersion,
              requestPayload,
            }
          : { modelId: parsed.modelId, requestPayload },
    ));
    if (fingerprint !== parsed.fingerprint) return null;
    return { ...parsed, requestPayload };
  } catch {
    return null;
  }
}

export function rememberPendingCreate(
  workspaceKey,
  value,
  storage,
) {
  const failed = { ok: false, notice: "浏览器未能保留请求确认信息。请允许此站点使用本机存储后重试。" };
  if (!workspaceKey) return failed;
  try {
    if (storage === undefined) storage = globalThis.sessionStorage;
    if (!storage || typeof storage.getItem !== "function") return failed;
    const key = pendingCreateStorageKey(workspaceKey);
    if (value) {
      const bytes = JSON.stringify(value);
      if (typeof bytes !== "string" || typeof storage.setItem !== "function") return failed;
      storage.setItem(key, bytes);
      if (storage.getItem(key) !== bytes) return failed;
    } else {
      if (typeof storage.removeItem !== "function") return failed;
      storage.removeItem(key);
      if (storage.getItem(key) !== null) return failed;
    }
    return { ok: true, notice: "" };
  } catch {
    return failed;
  }
}
