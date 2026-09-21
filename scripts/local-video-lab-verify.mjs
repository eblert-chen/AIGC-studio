import { createHash } from "node:crypto";
import { lstat, readFile, realpath } from "node:fs/promises";
import { extname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { labCookieName } from "./local-video-lab-gateway.mjs";

const KIND = "ai-video-local-video-lab";
const TARGETS = Object.freeze({
  gateway_base: "http://127.0.0.1:18480",
  platform_base: "http://127.0.0.1:18420",
  relay_base: "http://127.0.0.1:18430",
  frontend_base: "http://127.0.0.1:14178",
});
const SESSION = "__Host-ai_video_session";
const CSRF = "__Host-ai_video_csrf";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const REVISION = /^sha256:[0-9a-f]{64}$/;
const MAX_MEDIA_BYTES = 64 * 1024 * 1024;
const MAX_POLL_MS = 180_000;
const ACTIVE_STATES = new Set(["draft", "queued", "processing"]);
const TERMINAL_STATES = new Set(["succeeded", "failed", "cancelled"]);
const WORKSPACE = fileURLToPath(new URL("../", import.meta.url));
const REFERENCE_MODES = ["image_to_video", "video_to_video"];
const MODE_KEYS = { image_to_video: "i2v", video_to_video: "v2v" };
const SMOKE_REFERENCE_SLUGS = new Set(["seedance-2.5", "minimax-h3"]);
const NATIVE_BILLING_NUMERIC_FIELDS = ["user_quota", "user_used_quota", "user_request_count",
  "token_remain_quota", "token_used_quota", "native_consume_logs", "native_refund_logs"];

class VerificationError extends Error {
  constructor(code, stage, status = null) {
    super(code);
    this.code = code;
    this.stage = stage;
    this.status = status;
  }
}

function requireThat(condition, code, stage) {
  if (!condition) throw new VerificationError(code, stage);
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => JSON.stringify(key) + ":" + canonical(value[key])).join(",")}}`;
  }
  return JSON.stringify(value);
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function exactKeys(value, expected) {
  return value && typeof value === "object" && !Array.isArray(value)
    && canonical(Object.keys(value).sort()) === canonical([...expected].sort());
}

function validateBinding(manifest, receipt, bootstrapToken) {
  const stage = "binding";
  requireThat(exactKeys(manifest, ["schema_version", "kind", "lab_id", "instance_nonce", "provider_mode",
    "created_at_utc", "targets", "isolation", "budget", "paid_probe_approval"]), "INVALID_MANIFEST", stage);
  requireThat(manifest.schema_version === 1 && manifest.kind === KIND
    && manifest.provider_mode === "mock" && /^mock-[0-9a-f]{12}$/.test(manifest.lab_id)
    && /^[0-9a-f]{32}$/.test(manifest.instance_nonce) && manifest.paid_probe_approval === null,
  "MOCK_LAB_REQUIRED", stage);
  requireThat(canonical(manifest.targets) === canonical(TARGETS), "ISOLATED_TARGETS_REQUIRED", stage);
  const suffix = manifest.lab_id.slice(5);
  requireThat(canonical(manifest.isolation) === canonical({
    compose_project: `ai-video-lab-${manifest.lab_id}`,
    platform_database: { host: "platform-db-lab", port: 5432, database: `video_lab_mock_${suffix}` },
    relay_service_base: "http://relay-lab:3000",
  }), "ISOLATED_DATABASE_REQUIRED", stage);
  requireThat(typeof manifest.created_at_utc === "string" && Number.isFinite(Date.parse(manifest.created_at_utc))
    && manifest.created_at_utc.endsWith("Z") && Date.parse(manifest.created_at_utc) <= Date.now() + 300_000,
  "INVALID_MANIFEST_TIME", stage);
  requireThat(exactKeys(manifest.budget, ["test_points", "unit_price_points_per_second", "call_quota", "concurrency_limit"])
    && Object.entries({ test_points: 100_000, unit_price_points_per_second: 100, call_quota: 100, concurrency_limit: 2 })
      .every(([key, limit]) => Number.isSafeInteger(manifest.budget[key]) && manifest.budget[key] > 0 && manifest.budget[key] <= limit),
  "INVALID_TEST_BUDGET", stage);
  const digest = "sha256:" + sha256(canonical(manifest));
  requireThat(receipt?.schema_version === 1 && receipt.kind === KIND && receipt.lab_id === manifest.lab_id
    && receipt.provider_mode === "mock" && receipt.test_data_only === true && receipt.manifest_sha256 === digest,
  "RECEIPT_BINDING_MISMATCH", stage);
  requireThat(UUID.test(receipt.company_id || "") && UUID.test(receipt.user_id || ""), "INVALID_TEST_IDENTITY", stage);
  requireThat(typeof bootstrapToken === "string" && bootstrapToken.length >= 32
    && bootstrapToken.length <= 4096 && !/\s/.test(bootstrapToken), "MISSING_LAB_CREDENTIAL", stage);
  return digest;
}

async function eligibleModelIds() {
  const files = ["seedance_models.v1.json", "minimax_h3_models.v1.json"];
  const ids = [];
  for (const file of files) {
    let document;
    try {
      document = JSON.parse(await readFile(new URL(`../backend/new-api-relay/generationprofile/${file}`, import.meta.url), "utf8"));
    } catch {
      throw new VerificationError("MODEL_CONTRACT_UNAVAILABLE", "binding");
    }
    requireThat(document.schema_version === 1 && Array.isArray(document.models), "INVALID_MODEL_CONTRACT", "binding");
    for (const item of document.models) {
      if (item.new_routes_allowed === true && item.lifecycle === "acceptance_candidate"
          && (!item.eos_at || Date.parse(item.eos_at) > Date.now())) ids.push(item.public_model_id);
    }
  }
  requireThat(ids.length === 8 && new Set(ids).size === 8, "ELIGIBLE_MODEL_SET_CHANGED", "binding");
  return ids.sort();
}

function validateReceiptModels(receipt, eligible) {
  requireThat(Array.isArray(receipt.models) && receipt.models.length === eligible.length
    && canonical(receipt.models.map((model) => model.slug).sort()) === canonical(eligible), "RECEIPT_MODEL_SET_MISMATCH", "binding");
  requireThat(new Set(receipt.models.map((model) => model.id)).size === eligible.length, "DUPLICATE_MODEL_ID", "binding");
  for (const model of receipt.models) {
    requireThat(UUID.test(model.id || "") && model.status === "ready"
      && Number.isSafeInteger(model.expected_capability_version) && model.expected_capability_version > 0
      && REVISION.test(model.expected_quote_revision || ""), "INVALID_MODEL_RECEIPT", "binding");
    const payload = model.request_payload;
    requireThat(exactKeys(payload, ["mode", "prompt", "duration_seconds", "resolution", "aspect_ratio", "output_count", "face_enabled"])
      && payload.mode === "text_to_video" && payload.output_count === 1 && payload.face_enabled === false
      && typeof payload.prompt === "string" && payload.prompt.trim().length > 0,
    "TEXT_ONLY_RECEIPT_REQUIRED", "binding");
    const limits = model.effective_capabilities?.modes?.text_to_video?.limits;
    requireThat(limits && ["duration_seconds", "resolutions", "aspect_ratios"].every((key) => Array.isArray(limits[key]))
      && Array.from(payload.prompt).length <= limits.max_prompt_length
      && limits.duration_seconds.includes(payload.duration_seconds) && limits.resolutions.includes(payload.resolution)
      && limits.aspect_ratios.includes(payload.aspect_ratio), "RECEIPT_PAYLOAD_OUTSIDE_CAPABILITY", "binding");
  }
}

function mp4Boxes(bytes) {
  const boxes = [];
  let offset = 0;
  while (offset < bytes.length) {
    requireThat(bytes.length - offset >= 8 && boxes.length < 10_000, "REFERENCE_MP4_INVALID", "input_fixture");
    let size = bytes.readUInt32BE(offset);
    let header = 8;
    if (size === 1) {
      requireThat(bytes.length - offset >= 16, "REFERENCE_MP4_INVALID", "input_fixture");
      size = Number(bytes.readBigUInt64BE(offset + 8));
      header = 16;
    }
    if (size === 0) size = bytes.length - offset;
    requireThat(Number.isSafeInteger(size) && size >= header && size <= bytes.length - offset, "REFERENCE_MP4_INVALID", "input_fixture");
    boxes.push({ kind: bytes.toString("ascii", offset + 4, offset + 8), data: bytes.subarray(offset + header, offset + size) });
    offset += size;
  }
  return boxes;
}

function mp4Timing(bytes) {
  requireThat(bytes?.length >= 20 && [0, 1].includes(bytes[0]) && (bytes[0] === 0 || bytes.length >= 32),
    "REFERENCE_MP4_INVALID", "input_fixture");
  const scale = bytes.readUInt32BE(bytes[0] === 0 ? 12 : 20);
  const ticks = bytes[0] === 0 ? bytes.readUInt32BE(16) : Number(bytes.readBigUInt64BE(24));
  requireThat(scale > 0 && Number.isSafeInteger(ticks) && ticks > 0, "REFERENCE_MP4_INVALID", "input_fixture");
  return { scale, seconds: ticks / scale };
}

function inspectReferenceVideo(bytes) {
  const boxes = mp4Boxes(bytes);
  requireThat(boxes[0]?.kind === "ftyp" && boxes[0].data.length >= 8
    && boxes.some((box) => box.kind === "mdat" && box.data.length > 16)
    && boxes.filter((box) => box.kind === "moov").length === 1, "REFERENCE_MP4_INVALID", "input_fixture");
  const movie = mp4Boxes(boxes.find((box) => box.kind === "moov").data);
  const duration = mp4Timing(movie.find((box) => box.kind === "mvhd")?.data).seconds;
  const tracks = [];
  for (const box of movie.filter((item) => item.kind === "trak")) {
    const track = mp4Boxes(box.data);
    const media = mp4Boxes(track.find((item) => item.kind === "mdia")?.data || Buffer.alloc(0));
    const handler = media.find((item) => item.kind === "hdlr")?.data;
    if (!handler || handler.length < 12 || handler.toString("ascii", 8, 12) !== "vide") continue;
    const header = track.find((item) => item.kind === "tkhd")?.data;
    requireThat(header?.length >= 84, "REFERENCE_MP4_INVALID", "input_fixture");
    const width = header.readUInt32BE(header.length - 8) / 65536;
    const height = header.readUInt32BE(header.length - 4) / 65536;
    const mediaInfo = mp4Boxes(media.find((item) => item.kind === "minf")?.data || Buffer.alloc(0));
    const samples = mp4Boxes(mediaInfo.find((item) => item.kind === "stbl")?.data || Buffer.alloc(0));
    const description = samples.find((item) => item.kind === "stsd")?.data;
    const timing = samples.find((item) => item.kind === "stts")?.data;
    requireThat(description?.length >= 16 && description.readUInt32BE(4) === 1 && timing?.length >= 16,
      "REFERENCE_MP4_INVALID", "input_fixture");
    const codec = mp4Boxes(description.subarray(8))[0]?.kind;
    requireThat(["avc1", "avc3", "hvc1", "hev1"].includes(codec), "REFERENCE_VIDEO_CODEC_UNSUPPORTED", "input_fixture");
    const entries = timing.readUInt32BE(4);
    requireThat(entries > 0 && entries <= 10_000 && timing.length === 8 + entries * 8, "REFERENCE_MP4_INVALID", "input_fixture");
    let frames = 0;
    let ticks = 0;
    for (let index = 0; index < entries; index += 1) {
      const count = timing.readUInt32BE(8 + index * 8);
      const delta = timing.readUInt32BE(12 + index * 8);
      frames += count;
      ticks += count * delta;
    }
    const clock = mp4Timing(media.find((item) => item.kind === "mdhd")?.data);
    const frameRate = frames * clock.scale / ticks;
    requireThat(frames > 0 && Number.isSafeInteger(ticks) && ticks > 0 && frameRate >= 23.975 && frameRate <= 60.001,
      "REFERENCE_VIDEO_FRAME_RATE_UNSUPPORTED", "input_fixture");
    tracks.push({ width, height, duration_seconds: duration, frame_rate: frameRate, codec });
  }
  requireThat(tracks.length === 1 && duration >= 2 && duration <= 15, "REFERENCE_VIDEO_DURATION_UNSUPPORTED", "input_fixture");
  return tracks[0];
}

function inspectReferenceImage(bytes, extension) {
  if (extension === ".png") {
    requireThat(bytes.length >= 33 && bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
      && bytes.readUInt32BE(8) === 13 && bytes.toString("ascii", 12, 16) === "IHDR", "REFERENCE_IMAGE_INVALID", "input_fixture");
    return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), content_type: "image/png" };
  }
  requireThat([".jpg", ".jpeg"].includes(extension) && bytes.length >= 4 && bytes[0] === 255 && bytes[1] === 216,
    "REFERENCE_IMAGE_INVALID", "input_fixture");
  let offset = 2;
  while (offset < bytes.length) {
    requireThat(bytes[offset] === 255, "REFERENCE_IMAGE_INVALID", "input_fixture");
    while (bytes[offset] === 255) offset += 1;
    const marker = bytes[offset++];
    if (marker === 217 || marker === 218) break;
    if (marker === 1 || (marker >= 208 && marker <= 215)) continue;
    requireThat(offset + 2 <= bytes.length, "REFERENCE_IMAGE_INVALID", "input_fixture");
    const length = bytes.readUInt16BE(offset);
    requireThat(length >= 2 && offset + length <= bytes.length, "REFERENCE_IMAGE_INVALID", "input_fixture");
    if ([192, 193, 194].includes(marker)) {
      requireThat(length >= 8, "REFERENCE_IMAGE_INVALID", "input_fixture");
      return { width: bytes.readUInt16BE(offset + 5), height: bytes.readUInt16BE(offset + 3), content_type: "image/jpeg" };
    }
    offset += length;
  }
  throw new VerificationError("REFERENCE_IMAGE_INVALID", "input_fixture");
}

async function loadReferenceFixtures(fixtures, coverage) {
  requireThat(exactKeys(fixtures, ["imagePath", "videoPath"]) && ["smoke", "all-modes"].includes(coverage),
    "REFERENCE_FIXTURES_INVALID", "binding");
  const result = {};
  for (const mediaType of ["image", "video"]) {
    const path = fixtures[mediaType + "Path"];
    requireThat(typeof path === "string" && isAbsolute(path), "REFERENCE_PATH_REFUSED", "input_fixture");
    const lexical = relative(WORKSPACE, resolve(path));
    requireThat(lexical && lexical !== ".." && !lexical.startsWith(".." + sep) && !isAbsolute(lexical),
      "REFERENCE_PATH_REFUSED", "input_fixture"); // Reject UNC/out-of-workspace paths before filesystem access.
    let bytes;
    const extension = extname(path).toLowerCase();
    try {
      const root = await realpath(WORKSPACE);
      const target = await realpath(path);
      const within = relative(root, target);
      const info = await lstat(path);
      requireThat(within && within !== ".." && !within.startsWith(".." + sep) && !isAbsolute(within)
        && !info.isSymbolicLink() && info.isFile(), "REFERENCE_PATH_REFUSED", "input_fixture");
      requireThat(info.size > 0 && info.size <= (mediaType === "image" ? 30_000_000 : 50_000_000),
        "REFERENCE_SIZE_UNSUPPORTED", "input_fixture");
      bytes = await readFile(target);
      requireThat(bytes.length === info.size, "REFERENCE_FILE_CHANGED", "input_fixture");
    } catch (error) {
      if (error instanceof VerificationError) throw error;
      throw new VerificationError("REFERENCE_FILE_UNAVAILABLE", "input_fixture");
    }
    requireThat(mediaType !== "video" || extension === ".mp4", "REFERENCE_VIDEO_TYPE_UNSUPPORTED", "input_fixture");
    const metadata = mediaType === "image" ? inspectReferenceImage(bytes, extension) : inspectReferenceVideo(bytes);
    requireThat(Number.isInteger(metadata.width) && Number.isInteger(metadata.height)
      && metadata.width >= 256 && metadata.width <= 5760 && metadata.height >= 256 && metadata.height <= 5760
      && metadata.width / metadata.height >= 0.4 && metadata.width / metadata.height <= 2.5,
    "REFERENCE_DIMENSIONS_UNSUPPORTED", "input_fixture");
    result[mediaType] = { bytes, media_type: mediaType, content_type: metadata.content_type || "video/mp4",
      filename: `local-lab-reference-${mediaType}${extension === ".jpeg" ? ".jpg" : extension}`,
      size_bytes: bytes.length, sha256: sha256(bytes), metadata };
  }
  return result;
}

function referenceCases(receipt, assets, coverage) {
  const result = [];
  for (const model of receipt.models) {
    if (coverage === "smoke" && !SMOKE_REFERENCE_SLUGS.has(model.slug)) continue;
    for (const mode of REFERENCE_MODES) {
      const capability = model.effective_capabilities.modes[mode];
      if (!capability) continue;
      const mediaType = mode === "image_to_video" ? "image" : "video";
      const limits = capability.limits;
      requireThat(capability.input_media_types.includes(mediaType) && limits[mediaType === "image" ? "max_images" : "max_videos"] >= 1,
        "REFERENCE_MODE_CONTRACT_INVALID", "binding");
      const payload = { ...model.request_payload, mode, assets: [{ asset_id: assets[mediaType].id, media_type: mediaType }] };
      for (const [key, allowed] of [["resolution", limits.resolutions], ["aspect_ratio", limits.aspect_ratios], ["duration_seconds", limits.duration_seconds]]) {
        if (!allowed.includes(payload[key])) payload[key] = key === "duration_seconds" ? Math.min(...allowed) : allowed[0];
      }
      result.push({ ...model, request_payload: payload });
    }
  }
  requireThat(result.length === (coverage === "smoke" ? 4 : 12), "REFERENCE_MODEL_SET_CHANGED", "binding");
  return result;
}

function inputPreviewUrl(value, assetId) {
  let url;
  try { url = new URL(value); } catch { throw new VerificationError("INPUT_PREVIEW_URL_INVALID", "input_preview"); }
  requireThat(url.origin === TARGETS.frontend_base && !url.username && !url.password && !url.hash
    && url.pathname === `/api/v1/input-assets/${assetId}/content`
    && canonical([...url.searchParams.keys()].sort()) === canonical(["disposition", "expires", "signature"])
    && url.searchParams.get("disposition") === "inline" && /^[0-9a-f]{64}$/.test(url.searchParams.get("signature") || "")
    && /^\d{10}$/.test(url.searchParams.get("expires") || "")
    && Number(url.searchParams.get("expires")) > Date.now() / 1000
    && Number(url.searchParams.get("expires")) <= Date.now() / 1000 + 3600,
  "INPUT_PREVIEW_URL_OUTSIDE_LAB", "input_preview");
  return url.href;
}

async function boundedBody(response, limit, stage) {
  const declared = response.headers.get("content-length");
  requireThat(!declared || (/^\d+$/.test(declared) && Number(declared) <= limit), "RESPONSE_TOO_LARGE", stage);
  requireThat(response.body?.getReader, "RESPONSE_BODY_MISSING", stage);
  const reader = response.body.getReader();
  const parts = [];
  let total = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      total += chunk.value.byteLength;
      requireThat(total <= limit, "RESPONSE_TOO_LARGE", stage);
      parts.push(Buffer.from(chunk.value));
    }
  } catch (error) {
    await reader.cancel().catch(() => {});
    if (error instanceof VerificationError) throw error;
    throw new VerificationError("RESPONSE_BODY_INTERRUPTED", stage);
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(parts, total);
}

async function checkedFetch(url, init, stage, allowedStatuses = [200]) {
  requireThat(typeof url === "string" && [TARGETS.platform_base, TARGETS.frontend_base].includes(new URL(url).origin),
    "FETCH_TARGET_REFUSED", stage);
  let response;
  try {
    response = await fetch(url, { ...init, redirect: "manual", signal: AbortSignal.timeout(30_000) });
  } catch {
    throw new VerificationError("HTTP_RESULT_UNKNOWN", stage);
  }
  if (!allowedStatuses.includes(response.status)) {
    // Neither error bodies nor Location headers belong in diagnostics: both can
    // contain credentials, signed object URLs, or upstream provider messages.
    await response.body?.cancel().catch(() => {});
    throw new VerificationError(response.status >= 300 && response.status < 400 ? "REDIRECT_REFUSED" : "HTTP_STATUS_UNEXPECTED", stage, response.status);
  }
  return response;
}

async function jsonBody(response, stage) {
  try {
    return JSON.parse((await boundedBody(response, 4 * 1024 * 1024, stage)).toString("utf8"));
  } catch (error) {
    if (error instanceof VerificationError) throw error;
    throw new VerificationError("INVALID_JSON_RESPONSE", stage);
  }
}

function cookiePairs(headers, labId, { login = false } = {}) {
  requireThat(typeof headers.getSetCookie === "function", "COOKIE_HEADERS_UNAVAILABLE", "login");
  const pairs = [];
  const allowed = [SESSION, CSRF];
  for (const value of headers.getSetCookie()) {
    const separator = value.indexOf("=");
    const name = value.slice(0, separator);
    const canonicalName = login ? name : allowed.find((candidate) => labCookieName(candidate, labId) === name);
    if (!allowed.includes(canonicalName)) continue;
    requireThat(separator > 0 && /;\s*secure(?:;|$)/i.test(value) && /;\s*path=\/(?:;|$)/i.test(value)
      && /;\s*samesite=lax(?:;|$)/i.test(value) && !/;\s*domain=/i.test(value)
      && (canonicalName !== SESSION || /;\s*httponly(?:;|$)/i.test(value)), "SESSION_COOKIE_FLAGS_INVALID", "login");
    const secret = value.slice(separator + 1).split(";", 1)[0];
    requireThat(secret.length > 0 && secret.length <= 512 && !/[\r\n\s]/.test(secret), "SESSION_COOKIE_INVALID", "login");
    pairs.push([labCookieName(canonicalName, labId), secret]);
  }
  return pairs;
}

function safeEvidence(value) {
  requireThat(value?.mode === "mock" && value.production_acceptance === false
    && typeof value.state_id === "string" && /^[a-zA-Z0-9._:-]{1,128}$/.test(value.state_id), "RELAY_EVIDENCE_BINDING_INVALID", "provider_evidence");
  // The authenticated evidence endpoint binds these readings to its outer
  // state_id and exact lab-native user/token, returning 503 on binding failure.
  // Its real schema has no nested identity flag; never manufacture one here.
  const native = value.native_billing;
  requireThat(exactKeys(native, [...NATIVE_BILLING_NUMERIC_FIELDS, "token_unlimited_quota"]),
    "NATIVE_BILLING_EVIDENCE_INCOMPLETE", "provider_evidence");
  requireThat(NATIVE_BILLING_NUMERIC_FIELDS.every((key) => Number.isSafeInteger(native[key]))
    && native.native_consume_logs >= 0 && native.native_refund_logs >= 0
    && typeof native.token_unlimited_quota === "boolean", "NATIVE_BILLING_EVIDENCE_INVALID", "provider_evidence");
  const result = { mode: "mock", state_id: value.state_id,
    native_billing: Object.fromEntries([...NATIVE_BILLING_NUMERIC_FIELDS, "token_unlimited_quota"].map((key) => [key, native[key]])) };
  for (const key of ["provider_posts_this_process", "provider_polls_this_process", "artifact_fetches_this_process",
    "input_fetches_this_process", "input_bytes_this_process", "jobs", "native_tasks", "callback_deliveries", "provider_terminal_outcomes", "provider_cost_events"]) {
    if (key === "provider_posts_this_process" || value[key] !== undefined) {
      requireThat(Number.isSafeInteger(value[key]) && value[key] >= 0, "RELAY_COUNTER_INVALID", "provider_evidence");
      result[key] = value[key];
    }
  }
  return result;
}

function walletState(value) {
  requireThat(value?.billing_unit === "POINT" && value.billing_version === 2
    && Number.isSafeInteger(value.available_points) && value.available_points >= 0
    && Number.isSafeInteger(value.reserved_points) && value.reserved_points >= 0, "TEST_WALLET_INVALID", "accounting");
  return { available_points: value.available_points, reserved_points: value.reserved_points };
}

function taskState(task, model, receipt, unitPrice) {
  requireThat(task && UUID.test(task.id || "") && task.company_id === receipt.company_id
    && task.user_id === receipt.user_id && task.model_id === model.id
    && (ACTIVE_STATES.has(task.status) || TERMINAL_STATES.has(task.status)), "TASK_IDENTITY_INVALID", "task");
  requireThat(canonical(task.request_payload) === canonical({ ...model.request_payload, assets: model.request_payload.assets || [] }), "TASK_PAYLOAD_DRIFT", "task");
  const quote = unitPrice * model.request_payload.duration_seconds;
  requireThat(task.billing_unit === "POINT" && task.billing_version === 2 && task.quote_points === quote
    && task.quote_cents === null && task.pricing_snapshot?.quote_revision === model.expected_quote_revision
    && task.pricing_snapshot.unit_price_points === unitPrice, "TASK_QUOTE_DRIFT", "task");
  requireThat(task.capability_snapshot?.capability_version === model.expected_capability_version
    && task.capability_snapshot.model_id === model.id && task.capability_snapshot.model_slug === model.slug
    && canonical(task.capability_snapshot.effective_capabilities) === canonical(model.effective_capabilities),
  "TASK_CAPABILITY_DRIFT", "task");
  return { id: task.id, slug: model.slug, mode: model.request_payload.mode,
    input_asset_ids: (model.request_payload.assets || []).map((asset) => asset.asset_id),
    quote_points: quote, quote_revision: model.expected_quote_revision,
    capability_version: model.expected_capability_version, status: task.status, artifacts: [] };
}

function previewUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new VerificationError("PREVIEW_URL_INVALID", "preview"); }
  let path;
  try { path = decodeURIComponent(url.pathname); } catch { throw new VerificationError("PREVIEW_URL_INVALID", "preview"); }
  requireThat(url.origin === TARGETS.frontend_base && !url.username && !url.password && !url.hash
    && path.startsWith("/__local-video-lab__/objects/") && !path.includes("\\")
    && !path.split("/").includes("..") && !path.includes("//"), "PREVIEW_URL_OUTSIDE_LAB", "preview");
  return url.href;
}

/** Mock-only real-service exercise. No service startup, paid probe or fixture fallback. */
export async function verifyLocalVideoLab({ manifest, receipt, bootstrapToken, readRelayEvidence,
  referenceFixtures, referenceCoverage = "smoke" } = {}) {
  const report = {
    schema_version: 1, kind: "ai-video-local-video-lab-verification", status: "blocked", lab_id: null,
    provider_mode: "mock", test_data_only: true, real_provider_acceptance: false,
    tasks: [], input_assets: [], input_checks: [], negative_checks: [], provider_evidence: { observed: false },
  };
  let evidenceBefore = null;
  let evidenceAfterPositive = null;
  let readEvidence = null;
  try {
    report.manifest_sha256 = validateBinding(manifest, receipt, bootstrapToken);
    report.lab_id = manifest.lab_id;
    validateReceiptModels(receipt, await eligibleModelIds());
    requireThat(referenceFixtures !== undefined || referenceCoverage === "smoke", "REFERENCE_FIXTURES_REQUIRED", "binding");
    const referenceFiles = referenceFixtures === undefined ? null : await loadReferenceFixtures(referenceFixtures, referenceCoverage);
    report.reference_coverage = referenceFiles ? referenceCoverage : "not_requested";
    requireThat(readRelayEvidence === undefined || typeof readRelayEvidence === "function", "INVALID_EVIDENCE_READER", "binding");
    if (readRelayEvidence) {
      readEvidence = async () => {
        let value;
        try { value = await readRelayEvidence(); } catch { throw new VerificationError("RELAY_EVIDENCE_UNAVAILABLE", "provider_evidence"); }
        return safeEvidence(value);
      };
    }
    const privateHeaders = { "X-Bootstrap-Token": bootstrapToken, "X-Local-Video-Lab-ID": manifest.lab_id,
      "X-Local-Video-Lab-Nonce": manifest.instance_nonce, Accept: "application/json" };
    const identity = await jsonBody(await checkedFetch(TARGETS.platform_base + "/internal/local-video-lab/identity",
      { headers: privateHeaders }, "identity"), "identity");
    requireThat(identity.kind === KIND && identity.lab_id === manifest.lab_id && identity.provider_mode === "mock"
      && identity.instance_nonce === manifest.instance_nonce && identity.manifest_sha256 === report.manifest_sha256
      && identity.storage_bound === true && identity.test_data_only === true
      && identity.company_id === receipt.company_id && identity.user_id === receipt.user_id,
    "SERVER_IDENTITY_MISMATCH", "identity");
    const gateway = await jsonBody(await checkedFetch(TARGETS.frontend_base + "/__local-video-lab__/identity", {}, "gateway"), "gateway");
    requireThat(gateway.kind === KIND && gateway.lab_id === manifest.lab_id && gateway.provider_mode === "mock"
      && gateway.frontend_base === TARGETS.frontend_base, "GATEWAY_IDENTITY_MISMATCH", "gateway");
    const login = await checkedFetch(TARGETS.platform_base + "/internal/local-video-lab/login", {
      method: "POST", headers: { ...privateHeaders, "Content-Type": "application/json" }, body: "{}",
    }, "login");
    const loginIdentity = await jsonBody(login, "login");
    requireThat(loginIdentity.lab_id === manifest.lab_id && loginIdentity.company_id === receipt.company_id
      && loginIdentity.user_id === receipt.user_id && loginIdentity.identity_provenance === "local_lab_bootstrap",
    "LOGIN_IDENTITY_MISMATCH", "login");
    const jar = new Map(cookiePairs(login.headers, manifest.lab_id, { login: true }));
    requireThat([SESSION, CSRF].every((name) => jar.has(labCookieName(name, manifest.lab_id))), "SESSION_COOKIES_MISSING", "login");
    let csrf;
    async function api(path, { method = "GET", body, statuses = [200], key, stage = "api" } = {}) {
      requireThat(path.startsWith("/api/v1/") && !/[\r\n\\]/.test(path), "API_PATH_REFUSED", stage);
      const headers = { Accept: "application/json", Cookie: [...jar].map(([name, value]) => `${name}=${value}`).join("; "),
        "X-Company-ID": receipt.company_id };
      if (!["GET", "HEAD"].includes(method)) {
        headers.Origin = TARGETS.frontend_base;
        headers["X-CSRF-Token"] = csrf;
      }
      const multipart = body instanceof FormData;
      if (body !== undefined && !multipart) headers["Content-Type"] = "application/json";
      if (key) headers["Idempotency-Key"] = key;
      const response = await checkedFetch(TARGETS.frontend_base + path, {
        method, headers, ...(body !== undefined ? { body: multipart ? body : JSON.stringify(body) } : {}),
      }, stage, statuses);
      for (const [name, value] of cookiePairs(response.headers, manifest.lab_id)) jar.set(name, value);
      const value = response.status === 204 ? null : await jsonBody(response, stage);
      return { status: response.status, value };
    }
    const session = (await api("/api/v1/auth/session", { stage: "session" })).value;
    requireThat(session.authenticated === true && session.active_product_context === "company" && session.platform_admin === false
      && session.user?.id === receipt.user_id && session.companies?.some((company) => company.company_id === receipt.company_id)
      && typeof session.csrf_token === "string" && session.csrf_token.length > 0 && session.csrf_token.length <= 512,
    "REAL_COMPANY_SESSION_REQUIRED", "session");
    csrf = session.csrf_token;
    const companyPath = `/api/v1/companies/${receipt.company_id}`;
    const discovery = (await api(companyPath + "/models", { stage: "discovery" })).value;
    requireThat(Array.isArray(discovery) && discovery.length === receipt.models.length, "DISCOVERY_MODEL_SET_MISMATCH", "discovery");
    for (const model of receipt.models) {
      const current = discovery.find((item) => item.id === model.id && item.slug === model.slug);
      requireThat(current?.capability_version === model.expected_capability_version && current.quote_revision === model.expected_quote_revision
        && canonical(current.effective_capabilities) === canonical(model.effective_capabilities)
        && current.mode_readiness?.text_to_video?.default?.ready === true
        && current.billing_unit === "POINT" && current.billing_version === 2
        && current.unit_price_points === manifest.budget.unit_price_points_per_second,
      "DISCOVERY_RECEIPT_DRIFT", "discovery");
    }
    async function taskInventory() {
      const tasks = (await api(companyPath + "/tasks?limit=500", { stage: "task_inventory" })).value;
      requireThat(Array.isArray(tasks) && tasks.length < 500 && tasks.every((item) => UUID.test(item.id || "")), "TASK_INVENTORY_INCOMPLETE", "task_inventory");
      return tasks;
    }
    async function taskIds() { return (await taskInventory()).map((task) => task.id).sort(); }
    const beforeTasks = await taskInventory();
    const beforeIds = new Set(beforeTasks.map((task) => task.id));
    const beforeWallet = walletState((await api(companyPath + "/wallet", { stage: "accounting" })).value);
    requireThat(beforeWallet.reserved_points === 0, "OTHER_TASKS_ARE_IN_FLIGHT", "accounting");
    if (readEvidence) {
      evidenceBefore = await readEvidence();
      report.provider_evidence = { observed: true, before: evidenceBefore };
      if (referenceFiles) requireThat(Number.isSafeInteger(evidenceBefore.input_fetches_this_process), "REFERENCE_FETCH_COUNTER_MISSING", "provider_evidence");
    }
    async function uploadReference(fixture, { disabledProbe = false } = {}) {
      const filename = disabledProbe ? "disabled-probe-" + fixture.filename : fixture.filename;
      const key = `lab-${disabledProbe ? "disabled" : "input"}:${manifest.lab_id}:${fixture.media_type}:${fixture.sha256}`;
      async function submitUpload() {
        const form = new FormData();
        form.append("file", new Blob([fixture.bytes], { type: fixture.content_type }), filename);
        form.append("media_type", fixture.media_type);
        const asset = (await api(companyPath + "/assets", { method: "POST", body: form, key, statuses: [201], stage: "input_upload" })).value;
        requireThat(UUID.test(asset.id || "") && asset.company_id === receipt.company_id && asset.uploaded_by_user_id === receipt.user_id
          && asset.original_filename === filename && asset.media_type === fixture.media_type && asset.content_type === fixture.content_type
          && asset.size_bytes === fixture.size_bytes && asset.sha256 === fixture.sha256
          && (asset.status === "active" || (disabledProbe && asset.status === "disabled")), "INPUT_UPLOAD_PROOF_MISMATCH", "input_upload");
        return asset;
      }
      const asset = await submitUpload();
      const replay = await submitUpload();
      requireThat(replay.id === asset.id && replay.status === asset.status, "INPUT_UPLOAD_IDEMPOTENCY_FAILED", "input_upload");
      const proof = { id: asset.id, media_type: fixture.media_type, content_type: fixture.content_type,
        size_bytes: fixture.size_bytes, sha256: fixture.sha256, purpose: disabledProbe ? "disabled_admission_probe" : "reference",
        status: asset.status, idempotency_replayed: true, signed_bytes_verified: false, metadata: fixture.metadata };
      report.input_assets.push(proof);
      let url = null;
      if (asset.status === "active") {
        const preview = (await api(`${companyPath}/assets/${asset.id}/preview`, { stage: "input_preview" })).value;
        requireThat(Number.isSafeInteger(preview.expires_seconds) && preview.expires_seconds >= 1 && preview.expires_seconds <= 3600,
          "INPUT_PREVIEW_PROOF_MISSING", "input_preview");
        url = inputPreviewUrl(preview.url, asset.id);
        const download = await checkedFetch(url, { headers: { Accept: fixture.content_type } }, "input_bytes");
        requireThat(download.headers.get("content-type")?.split(";", 1)[0] === fixture.content_type,
          "INPUT_CONTENT_TYPE_MISMATCH", "input_bytes");
        const bytes = await boundedBody(download, fixture.size_bytes, "input_bytes");
        requireThat(bytes.length === fixture.size_bytes && sha256(bytes) === fixture.sha256, "INPUT_BYTES_MISMATCH", "input_bytes");
        proof.signed_bytes_verified = true;
      }
      return { ...asset, proof, signedUrl: url };
    }
    const inputAssets = {};
    const cases = [...receipt.models];
    if (referenceFiles) {
      // Confirm reference discovery before the first multipart upload. No grant,
      // MIME, parameter or readiness override is performed by this verifier.
      const planned = referenceCases(receipt, { image: { id: receipt.user_id }, video: { id: receipt.user_id } }, referenceCoverage);
      for (const item of planned) requireThat(discovery.find((model) => model.id === item.id)
        ?.mode_readiness?.[item.request_payload.mode]?.default?.ready === true, "REFERENCE_MODE_NOT_READY", "discovery");
      for (const mediaType of ["image", "video"]) inputAssets[mediaType] = await uploadReference(referenceFiles[mediaType]);
      cases.push(...referenceCases(receipt, inputAssets, referenceCoverage));
      const allCases = [...receipt.models, ...referenceCases(receipt, inputAssets, "all-modes")];
      const existingCaseKeys = new Set();
      for (const previous of beforeTasks) {
        const matching = allCases.find((item) => previous.model_id === item.id && previous.request_payload?.mode === item.request_payload.mode);
        const caseKey = `${previous.model_id}/${previous.request_payload?.mode}`;
        requireThat(matching && !existingCaseKeys.has(caseKey), "UNEXPECTED_LAB_TASK_INVENTORY", "accounting");
        taskState(previous, matching, receipt, manifest.budget.unit_price_points_per_second);
        existingCaseKeys.add(caseKey);
      }
      const missing = cases.filter((item) => !existingCaseKeys.has(`${item.id}/${item.request_payload.mode}`));
      requireThat(beforeTasks.length + missing.length <= Math.min(20, manifest.budget.call_quota), "REFERENCE_TASK_BUDGET_EXCEEDED", "accounting");
      const requiredPoints = missing.reduce((sum, item) => sum + item.request_payload.duration_seconds * manifest.budget.unit_price_points_per_second, 0);
      requireThat(requiredPoints <= beforeWallet.available_points, "REFERENCE_POINT_BUDGET_EXCEEDED", "accounting");
    }
    for (const model of cases) {
      const reference = model.request_payload.mode !== "text_to_video";
      const key = reference ? `lab-ref:${manifest.lab_id}:${model.slug}:${MODE_KEYS[model.request_payload.mode]}` : `lab-e2e:${manifest.lab_id}:${model.slug}`;
      const body = { model_id: model.id, idempotency_key: key, request_payload: model.request_payload,
        expected_capability_version: model.expected_capability_version, expected_quote_revision: model.expected_quote_revision };
      const created = (await api(companyPath + "/tasks", { method: "POST", body, key, statuses: [201], stage: "submit" })).value;
      const record = taskState(created, model, receipt, manifest.budget.unit_price_points_per_second);
      record.created_in_this_verification = !beforeIds.has(created.id);
      report.tasks.push(record);
      const replay = (await api(companyPath + "/tasks", { method: "POST", body, key, statuses: [201], stage: "idempotent_replay" })).value;
      taskState(replay, model, receipt, manifest.budget.unit_price_points_per_second);
      requireThat(replay.id === created.id, "IDEMPOTENCY_CREATED_ANOTHER_TASK", "idempotent_replay");
      record.idempotency_replayed = true;
      // Always observe the original task GET, including an already-terminal
      // idempotent replay; a create response alone is not a polling proof.
      let terminal = (await api(`${companyPath}/tasks/${created.id}`, { stage: "poll" })).value;
      taskState(terminal, model, receipt, manifest.budget.unit_price_points_per_second);
      requireThat(terminal.id === created.id, "TASK_POLL_IDENTITY_DRIFT", "poll");
      const deadline = Date.now() + MAX_POLL_MS;
      while (!TERMINAL_STATES.has(terminal.status)) {
        requireThat(Date.now() < deadline, "TASK_TERMINAL_TIMEOUT", "poll");
        await new Promise((resolveWait) => setTimeout(resolveWait, 750));
        terminal = (await api(`${companyPath}/tasks/${created.id}`, { stage: "poll" })).value;
        taskState(terminal, model, receipt, manifest.budget.unit_price_points_per_second);
        requireThat(terminal.id === created.id, "TASK_POLL_IDENTITY_DRIFT", "poll");
      }
      record.status = terminal.status;
      requireThat(terminal.status === "succeeded", "TASK_DID_NOT_SUCCEED", "poll");
      requireThat(terminal.actual_cost_points === record.quote_points && terminal.actual_cost_cents === null
        && terminal.reserved_points === 0 && terminal.reserved_cents === 0 && typeof terminal.relay_job_id === "string"
        && terminal.relay_job_id.length > 0, "TERMINAL_ACCOUNTING_OR_RELAY_PROOF_MISSING", "artifact");
      requireThat(Array.isArray(terminal.output_artifacts) && terminal.output_artifacts.length === 1, "STORED_ARTIFACT_MISSING", "artifact");
      for (const artifact of terminal.output_artifacts) {
        requireThat(/^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,159}$/.test(artifact.asset_id || "")
          && artifact.media_type === "video" && artifact.content_type === "video/mp4"
          && Number.isSafeInteger(artifact.size_bytes) && artifact.size_bytes >= 24 && artifact.size_bytes <= MAX_MEDIA_BYTES
          && /^[0-9a-f]{64}$/.test(artifact.sha256 || ""), "ARTIFACT_STORAGE_METADATA_INVALID", "artifact");
        const proof = { asset_id: artifact.asset_id, content_type: artifact.content_type,
          size_bytes: artifact.size_bytes, sha256: artifact.sha256, stored_index_observed: true,
          bound_preview_verified: false, bytes_verified: false, range_verified: false };
        record.artifacts.push(proof);
        const preview = (await api(`${companyPath}/tasks/${created.id}/artifacts/${encodeURIComponent(artifact.asset_id)}/preview`, { stage: "preview_storage_proof" })).value;
        requireThat(preview.preview_status === "issued" && preview.media_type === "video" && preview.content_type === "video/mp4"
          && Number.isSafeInteger(preview.expires_seconds) && preview.expires_seconds >= 1 && preview.expires_seconds <= 3600,
        "PREVIEW_STORAGE_PROOF_MISSING", "preview");
        const url = previewUrl(preview.url);
        proof.bound_preview_verified = true;
        const mediaHeaders = { Accept: "video/mp4", Cookie: [...jar].map(([name, value]) => `${name}=${value}`).join("; ") };
        const media = await checkedFetch(url, { headers: mediaHeaders }, "media_bytes");
        requireThat(media.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() === "video/mp4", "MEDIA_CONTENT_TYPE_INVALID", "media_bytes");
        const bytes = await boundedBody(media, Math.min(MAX_MEDIA_BYTES, artifact.size_bytes), "media_bytes");
        requireThat(bytes.length === artifact.size_bytes && sha256(bytes) === artifact.sha256
          && bytes.subarray(4, 8).toString("ascii") === "ftyp", "MEDIA_BYTES_DO_NOT_MATCH_STORED_ARTIFACT", "media_bytes");
        proof.bytes_verified = true;
        const end = Math.min(63, bytes.length - 1);
        const range = await checkedFetch(url, { headers: { ...mediaHeaders, Range: `bytes=0-${end}` } }, "media_range", [206]);
        requireThat(range.headers.get("content-range") === `bytes 0-${end}/${bytes.length}`, "MEDIA_CONTENT_RANGE_INVALID", "media_range");
        const slice = await boundedBody(range, end + 1, "media_range");
        requireThat(slice.equals(bytes.subarray(0, end + 1)), "MEDIA_RANGE_BYTES_INVALID", "media_range");
        proof.range_verified = true;
      }
    }
    report.created_task_count = report.tasks.filter((task) => task.created_in_this_verification).length;
    if (readEvidence) {
      evidenceAfterPositive = await readEvidence();
      requireThat(evidenceAfterPositive.state_id === evidenceBefore.state_id, "RELAY_STATE_CHANGED", "provider_evidence");
      report.provider_evidence.after_positive = evidenceAfterPositive;
      requireThat(canonical(evidenceAfterPositive.native_billing) === canonical(evidenceBefore.native_billing),
        "NATIVE_BILLING_CHANGED_DURING_POSITIVE", "native_billing");
      report.provider_evidence.native_billing_unchanged_after_positive = true;
      report.provider_evidence.positive_provider_posts = evidenceAfterPositive.provider_posts_this_process - evidenceBefore.provider_posts_this_process;
      requireThat(report.provider_evidence.positive_provider_posts === report.created_task_count, "PROVIDER_POST_COUNT_MISMATCH", "provider_evidence");
      if (referenceFiles) {
        const expectedFetches = report.tasks.filter((task) => task.created_in_this_verification).reduce((sum, task) => sum + task.input_asset_ids.length, 0);
        requireThat(Number.isSafeInteger(evidenceAfterPositive.input_fetches_this_process), "REFERENCE_FETCH_COUNTER_MISSING", "provider_evidence");
        report.provider_evidence.positive_input_fetches = evidenceAfterPositive.input_fetches_this_process - evidenceBefore.input_fetches_this_process;
        requireThat(report.provider_evidence.positive_input_fetches === expectedFetches, "REFERENCE_FETCH_COUNT_MISMATCH", "provider_evidence");
        if (evidenceBefore.input_bytes_this_process !== undefined && evidenceAfterPositive.input_bytes_this_process !== undefined) {
          const expectedBytes = report.tasks.filter((task) => task.created_in_this_verification)
            .flatMap((task) => task.input_asset_ids).reduce((sum, id) => sum + report.input_assets.find((asset) => asset.id === id).size_bytes, 0);
          report.provider_evidence.positive_input_bytes = evidenceAfterPositive.input_bytes_this_process - evidenceBefore.input_bytes_this_process;
          requireThat(report.provider_evidence.positive_input_bytes === expectedBytes, "REFERENCE_FETCH_BYTE_COUNT_MISMATCH", "provider_evidence");
        }
      }
    }
    const beforeNegativeIds = await taskIds();
    const beforeNegativeWallet = walletState((await api(companyPath + "/wallet", { stage: "accounting" })).value);
    requireThat(beforeNegativeWallet.reserved_points === 0, "SUCCESS_LEFT_RESERVED_POINTS", "accounting");
    const spent = report.tasks.filter((task) => task.created_in_this_verification).reduce((sum, task) => sum + task.quote_points, 0);
    requireThat(beforeWallet.available_points - beforeNegativeWallet.available_points === spent, "WALLET_SETTLEMENT_MISMATCH", "accounting");
    for (const model of receipt.models) {
      for (const name of ["stale_quote", "unsupported_resolution"]) {
        const key = `lab-neg:${manifest.lab_id}:${model.slug}:${name}`;
        const wrongRevision = "sha256:" + (model.expected_quote_revision[7] === "0" ? "1" : "0") + model.expected_quote_revision.slice(8);
        const body = { model_id: model.id, idempotency_key: key,
          expected_capability_version: model.expected_capability_version,
          expected_quote_revision: name === "stale_quote" ? wrongRevision : model.expected_quote_revision,
          request_payload: { ...model.request_payload, ...(name === "unsupported_resolution" ? { resolution: "lab-unsupported-resolution" } : {}) } };
        const rejected = await api(companyPath + "/tasks", { method: "POST", body, key, statuses: [409], stage: "negative_admission" });
        requireThat(!rejected.value?.id, "NEGATIVE_REQUEST_CREATED_A_TASK", "negative_admission");
        report.negative_checks.push({ slug: model.slug, case: name, status: "rejected", http_status: rejected.status });
      }
    }
    if (referenceFiles) {
      for (const mediaType of ["image", "video"]) {
        for (const check of ["tampered_signature", "expired_signature"]) {
          const url = new URL(inputAssets[mediaType].signedUrl);
          if (check === "expired_signature") url.searchParams.set("expires", String(Math.floor(Date.now() / 1000) - 1));
          else {
            const signature = url.searchParams.get("signature");
            url.searchParams.set("signature", (signature[0] === "0" ? "1" : "0") + signature.slice(1));
          }
          const denied = await checkedFetch(url.href, { headers: { Accept: referenceFiles[mediaType].content_type } }, "input_signature_negative", [404]);
          await denied.body?.cancel().catch(() => {});
          report.input_checks.push({ media_type: mediaType, case: check, http_status: 404, status: "rejected" });
        }
      }
      // Never disable the reusable positive inputs. This separately named copy
      // exists only to prove the original ACTIVE/signed-access admission guard.
      const disabled = await uploadReference(referenceFiles.image, { disabledProbe: true });
      await api(`${companyPath}/assets/${disabled.id}`, { method: "DELETE", statuses: [204], stage: "input_disable" });
      disabled.proof.status = "disabled";
      await api(`${companyPath}/assets/${disabled.id}/preview`, { statuses: [404], stage: "disabled_input_preview" });
      if (disabled.signedUrl) {
        const denied = await checkedFetch(disabled.signedUrl, {}, "disabled_input_signed_access", [404]);
        await denied.body?.cancel().catch(() => {});
      }
      const imageCase = cases.find((model) => model.slug === "minimax-h3" && model.request_payload.mode === "image_to_video");
      const key = `lab-disabled:${manifest.lab_id}:minimax-h3:i2v`;
      const rejected = await api(companyPath + "/tasks", { method: "POST", key, statuses: [404], stage: "disabled_input_admission",
        body: { model_id: imageCase.id, idempotency_key: key, expected_capability_version: imageCase.expected_capability_version,
          expected_quote_revision: imageCase.expected_quote_revision,
          request_payload: { ...imageCase.request_payload, assets: [{ asset_id: disabled.id, media_type: "image" }] } } });
      requireThat(!rejected.value?.id, "DISABLED_INPUT_CREATED_A_TASK", "disabled_input_admission");
      report.input_checks.push({ media_type: "image", case: "disabled_input", http_status: 404, status: "rejected", asset_id: disabled.id });
    }
    requireThat(canonical(await taskIds()) === canonical(beforeNegativeIds), "NEGATIVE_REQUESTS_CHANGED_TASK_INVENTORY", "negative_admission");
    const afterNegativeWallet = walletState((await api(companyPath + "/wallet", { stage: "accounting" })).value);
    requireThat(canonical(afterNegativeWallet) === canonical(beforeNegativeWallet), "NEGATIVE_REQUESTS_CHANGED_WALLET", "negative_admission");
    report.negative_admission_uncharged = true;
    if (readEvidence) {
      const after = await readEvidence();
      report.provider_evidence.after_negative = after;
      requireThat(after.state_id === evidenceBefore.state_id, "RELAY_STATE_CHANGED", "provider_evidence");
      requireThat(canonical(after.native_billing) === canonical(evidenceAfterPositive.native_billing),
        "NATIVE_BILLING_CHANGED_DURING_NEGATIVE", "native_billing");
      report.provider_evidence.native_billing_unchanged_after_negative = true;
      report.provider_evidence.negative_provider_posts = after.provider_posts_this_process - evidenceAfterPositive.provider_posts_this_process;
      requireThat(report.provider_evidence.negative_provider_posts === 0, "NEGATIVE_REQUESTS_CREATED_PROVIDER_POSTS", "provider_evidence");
      if (referenceFiles) {
        requireThat(Number.isSafeInteger(after.input_fetches_this_process), "REFERENCE_FETCH_COUNTER_MISSING", "provider_evidence");
        report.provider_evidence.negative_input_fetches = after.input_fetches_this_process - evidenceAfterPositive.input_fetches_this_process;
        requireThat(report.provider_evidence.negative_input_fetches === 0, "NEGATIVE_REQUESTS_FETCHED_INPUTS", "provider_evidence");
      }
    }
    report.status = "passed";
  } catch (error) {
    report.status = "failed";
    report.failure = error instanceof VerificationError
      ? { code: error.code, stage: error.stage, ...(error.status !== null ? { http_status: error.status } : {}) }
      : { code: "VERIFIER_INTERNAL_ERROR", stage: "verification" };
    if (readEvidence && evidenceBefore) {
      try { report.provider_evidence.after_failure = await readEvidence(); } catch { /* no inferred counter or secret-bearing error */ }
    }
  }
  return report;
}
