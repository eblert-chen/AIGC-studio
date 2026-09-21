import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createPlatformClient } from "../src/api/platformClient.js";
import {
  filesFromPendingRequest,
  readPendingCreate,
  rememberPendingCreate,
  taskRequestFingerprint,
} from "../src/app/pendingGeneration.js";
import {
  buildCapabilityRequestPayload,
  capabilityControlVisibility,
  reconcileGenerationDraft,
  resolveEffectiveCapabilities,
} from "../src/modelCapabilities.js";

// Test-only discovery responses. The effective capabilities are copied from the
// reviewed Relay manifest; the synthetic grants, prices and readiness below are
// not provider authorization or release evidence.
const GOOGLE_VIDEO_CATALOG = JSON.parse(readFileSync(new URL(
  "../backend/new-api-relay/generationprofile/google_video_models.v1.json",
  import.meta.url,
), "utf8"));

const WORKSPACE_ID = "dddddddd-0000-4000-8000-000000000001";
const PUBLIC_REQUEST_KEYS = new Set([
  "mode",
  "prompt",
  "duration_seconds",
  "aspect_ratio",
  "resolution",
  "output_count",
  "face_enabled",
  "assets",
]);

function publicCatalogModels() {
  const byPublicId = new Map();
  for (const model of GOOGLE_VIDEO_CATALOG.models) {
    if (!byPublicId.has(model.public_model_id)) byPublicId.set(model.public_model_id, model);
  }
  return [...byPublicId.values()];
}

function discoveryResponses() {
  return publicCatalogModels().map((model, index) => ({
    id: `eeeeeeee-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    slug: model.public_model_id,
    display_name: model.display_name,
    pricing_mode: "per_generation",
    billing_unit: "POINT",
    billing_version: 2,
    billing_scope: "company",
    unit_price_points: 20 + index,
    capability_version: 1,
    quote_revision: `sha256:${createHash("sha256")
      .update(`google-video-test:${model.public_model_id}`)
      .digest("hex")}`,
    effective_capabilities: structuredClone(model.capability),
  }));
}

function discovery(publicModelId) {
  const model = discoveryResponses().find((candidate) => candidate.slug === publicModelId);
  if (!model) throw new Error(`Missing Google video test model: ${publicModelId}`);
  return model;
}

function media(kind, count) {
  return Array.from({ length: count }, (_, index) => ({
    asset_id: `${kind}-google-fixture-${index + 1}`,
    media_type: kind,
  }));
}

function draftFor(capability) {
  return {
    prompt: "让参考画面里的光线自然流动，生成一段连贯的视频。",
    duration: capability.limits.durations[0],
    aspectRatio: capability.limits.aspectRatios[0],
    resolution: capability.limits.resolutions[0],
    outputCount: capability.limits.outputCounts[0],
    faceEnabled: false,
    files: {
      image: media("image", capability.limits.maxImages),
      video: media("video", capability.limits.maxVideos),
      audio: media("audio", capability.limits.maxAudio),
    },
  };
}

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

function pendingRequest(model, requestPayload) {
  const identity = {
    modelId: model.id,
    capabilityVersion: model.capability_version,
    quoteRevision: model.quote_revision,
    requestPayload,
  };
  return {
    version: 5,
    workspaceKey: WORKSPACE_ID,
    companyId: WORKSPACE_ID,
    ...identity,
    fingerprint: taskRequestFingerprint(JSON.stringify(identity)),
    idempotencyKey: "google-video-original-request-0001",
    uncertain: true,
  };
}

test("Google video discovery exposes three public identities from the reviewed manifest only", () => {
  assert.equal(GOOGLE_VIDEO_CATALOG.schema_version, 1);
  assert.deepEqual(publicCatalogModels().map((model) => model.public_model_id).sort(), [
    "gemini-omni-1.1-flash",
    "veo-3.1",
    "veo-3.1-fast",
  ]);

  const recordsByPublicId = Map.groupBy(
    GOOGLE_VIDEO_CATALOG.models,
    (model) => model.public_model_id,
  );
  for (const publicModelId of ["veo-3.1", "veo-3.1-fast"]) {
    const records = recordsByPublicId.get(publicModelId);
    assert.equal(records.length, 1);
    assert.equal(records[0].access_surface, "gemini_api");
    assert.equal(records[0].native_channel_type, 24);
    assert.equal(records[0].provider_model_id.endsWith("-generate-preview"), true);
  }
  assert.equal(
    GOOGLE_VIDEO_CATALOG.models.some((model) => model.access_surface === "vertex_ai"),
    false,
  );

  for (const model of discoveryResponses()) {
    const source = publicCatalogModels().find((entry) => entry.public_model_id === model.slug);
    assert.deepEqual(model.effective_capabilities, source.capability);
    assert.notEqual(model.effective_capabilities, source.capability);
    for (const forbidden of [
      "provider_model_id",
      "adapter_profile_id",
      "access_surface",
      "native_channel_type",
      "endpoint",
      "api_key",
    ]) {
      assert.equal(Object.hasOwn(model, forbidden), false, forbidden);
    }
  }
});

test("Omni and Veo controls come only from effective capabilities", () => {
  const omni = resolveEffectiveCapabilities(discovery("gemini-omni-1.1-flash"));
  assert.deepEqual(Object.keys(omni.modes), ["text_to_video", "image_to_video"]);
  for (const capability of Object.values(omni.modes)) {
    assert.deepEqual(capability.limits.durations, [3, 4, 5, 6, 7, 8, 9, 10]);
    assert.deepEqual(capability.limits.resolutions, ["360p", "720p", "1080p", "4k"]);
    assert.equal(capability.supportsFace, false);
    assert.equal(capability.limits.maxVideos, 0);
    assert.equal(capability.limits.maxAudio, 0);
  }
  assert.deepEqual(capabilityControlVisibility(omni.modes.text_to_video), {
    image: false,
    video: false,
    audio: false,
    face: false,
  });
  assert.deepEqual(capabilityControlVisibility(omni.modes.image_to_video), {
    image: true,
    video: false,
    audio: false,
    face: false,
  });
  assert.equal(omni.modes.image_to_video.limits.maxImages, 2);

  for (const modelId of ["veo-3.1", "veo-3.1-fast"]) {
    const veo = resolveEffectiveCapabilities(discovery(modelId));
    assert.deepEqual(Object.keys(veo.modes), ["text_to_video", "image_to_video"]);
    for (const capability of Object.values(veo.modes)) {
      assert.deepEqual(capability.limits.durations, [8]);
      assert.deepEqual(capability.limits.resolutions, ["720p", "1080p", "4k"]);
      assert.equal(capability.supportsFace, false);
      assert.equal(capability.limits.maxVideos, 0);
      assert.equal(capability.limits.maxAudio, 0);
    }
    assert.equal(veo.modes.text_to_video.limits.maxImages, 0);
    assert.equal(veo.modes.image_to_video.limits.maxImages, 1);
  }
});

test("malformed Google input-media declarations invalidate the whole effective document", () => {
  const mutations = [
    (mode) => { mode.input_media_types = ["image", "document"]; },
    (mode) => { mode.input_media_types = ["image", "image"]; },
    (mode) => { mode.input_media_types = ["Image"]; },
    (mode) => { mode.input_media_types = ["image "]; },
    (mode) => { mode.limits.max_images = 0; },
  ];
  for (const mutate of mutations) {
    const model = structuredClone(discovery("veo-3.1"));
    mutate(model.effective_capabilities.modes.image_to_video);
    assert.deepEqual(
      resolveEffectiveCapabilities(model).modes,
      {},
      JSON.stringify(model.effective_capabilities.modes.image_to_video),
    );
  }

  const undeclaredPositiveMaximum = structuredClone(discovery("veo-3.1"));
  undeclaredPositiveMaximum.effective_capabilities.modes.text_to_video.limits.max_images = 1;
  assert.deepEqual(resolveEffectiveCapabilities(undeclaredPositiveMaximum).modes, {});
});

test("switching Omni to Veo clips media and replaces unsupported specifications synchronously", () => {
  const omni = resolveEffectiveCapabilities(discovery("gemini-omni-1.1-flash"));
  const veo = resolveEffectiveCapabilities(discovery("veo-3.1"));
  const omniDraft = {
    ...draftFor(omni.modes.image_to_video),
    faceEnabled: true,
    files: {
      image: media("image", 2),
      video: media("video", 1),
      audio: media("audio", 1),
    },
  };
  assert.equal(omniDraft.duration, 3);
  assert.equal(omniDraft.resolution, "360p");

  const changed = reconcileGenerationDraft(veo, "image_to_video", omniDraft);
  assert.equal(changed.ok, true);
  assert.equal(changed.mode, "image_to_video");
  assert.equal(changed.draft.files.image.length, 1);
  assert.equal(changed.draft.files.video.length, 0);
  assert.equal(changed.draft.files.audio.length, 0);
  assert.equal(changed.removedMediaCount, 3);
  assert.equal(changed.draft.duration, 8);
  assert.equal(changed.draft.resolution, "720p");
  assert.equal(changed.draft.faceEnabled, false);

  const textOnly = reconcileGenerationDraft(veo, "text_to_video", changed.draft);
  assert.deepEqual(textOnly.draft.files, { image: [], video: [], audio: [] });
  assert.equal(textOnly.removedMediaCount, 1);
  assert.deepEqual(capabilityControlVisibility(textOnly.capability), {
    image: false,
    video: false,
    audio: false,
    face: false,
  });
});

test("Google video requests use the shared public payload and drop provider passthrough", () => {
  for (const publicModelId of ["gemini-omni-1.1-flash", "veo-3.1", "veo-3.1-fast"]) {
    const capabilities = resolveEffectiveCapabilities(discovery(publicModelId));
    for (const [mode, capability] of Object.entries(capabilities.modes)) {
      const built = buildCapabilityRequestPayload(capabilities, mode, {
        ...draftFor(capability),
        provider_model_id: "forbidden-provider-model",
        access_surface: "forbidden-access-surface",
        endpoint: "https://provider.invalid/forbidden",
        api_key: "forbidden-key",
        generate_audio: true,
        previous_interaction_id: "forbidden-lineage",
        seed: 42,
      });
      assert.equal(built.ok, true, `${publicModelId}/${mode}: ${built.error}`);
      assert.ok(Object.keys(built.payload).every((key) => PUBLIC_REQUEST_KEYS.has(key)));
      assert.equal(Object.hasOwn(built.payload, "face_enabled"), false);
      assert.equal(built.payload.assets?.length ?? 0, capability.limits.maxImages);
      assert.deepEqual(
        (built.payload.assets ?? []).map((asset) => asset.media_type),
        Array(capability.limits.maxImages).fill("image"),
      );
    }
  }
});

test("an uncertain Google video submission replays its exact original request", async () => {
  const originalModel = discovery("gemini-omni-1.1-flash");
  const originalCapabilities = resolveEffectiveCapabilities(originalModel);
  const built = buildCapabilityRequestPayload(
    originalCapabilities,
    "image_to_video",
    draftFor(originalCapabilities.modes.image_to_video),
  );
  assert.equal(built.ok, true);

  const cache = memoryStorage();
  const original = pendingRequest(originalModel, built.payload);
  assert.equal(rememberPendingCreate(WORKSPACE_ID, original, cache).ok, true);

  // A later discovery/price revision must not rewrite an uncertain request.
  const newerDiscovery = discovery("gemini-omni-1.1-flash");
  newerDiscovery.capability_version += 1;
  newerDiscovery.quote_revision = `sha256:${"f".repeat(64)}`;
  newerDiscovery.unit_price_points = 999;

  const restored = readPendingCreate(WORKSPACE_ID, cache);
  assert.deepEqual(restored, original);
  assert.deepEqual(
    filesFromPendingRequest(restored.requestPayload).image.map((file) => file.asset_id),
    original.requestPayload.assets.map((asset) => asset.asset_id),
  );

  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example.test",
    companyId: WORKSPACE_ID,
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify({ id: "google-video-fixture-task", status: "accepted" }), {
        headers: { "content-type": "application/json" },
      });
    },
  });
  await client.createTask({
    modelId: restored.modelId,
    requestPayload: restored.requestPayload,
    expectedCapabilityVersion: restored.capabilityVersion,
    expectedQuoteRevision: restored.quoteRevision,
  }, { idempotencyKey: restored.idempotencyKey });

  assert.equal(captured.length, 1);
  assert.deepEqual(JSON.parse(captured[0].options.body), {
    model_id: original.modelId,
    request_payload: original.requestPayload,
    idempotency_key: original.idempotencyKey,
    expected_capability_version: original.capabilityVersion,
    expected_quote_revision: original.quoteRevision,
  });
  assert.equal(captured[0].options.headers.Authorization, undefined);
});
