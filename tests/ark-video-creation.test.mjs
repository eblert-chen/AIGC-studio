import assert from "node:assert/strict";
import test from "node:test";
import {
  buildCapabilityRequestPayload,
  capabilityControlVisibility,
  modeLabel,
  normalizeModeReadiness,
  reconcileGenerationDraft,
  resolveEffectiveCapabilities,
  resolveGenerationReadiness,
} from "../src/modelCapabilities.js";
import { createPlatformClient } from "../src/api/platformClient.js";
import {
  filesFromPendingRequest,
  readPendingCreate,
  rememberPendingCreate,
  taskRequestFingerprint,
} from "../src/app/pendingGeneration.js";
import {
  ARK_TEST_COMPANY_ID,
  ARK_VIDEO_CATALOG,
  ARK_VIDEO_CURRENT_MODELS,
  arkDiscoveryResponses,
  arkModelResponse,
} from "./fixtures/ark-video-discovery.mjs";

const PUBLIC_REQUEST_KEYS = new Set([
  "mode", "prompt", "duration_seconds", "aspect_ratio", "resolution",
  "output_count", "face_enabled", "assets",
]);

function maximumDraft(capability) {
  const limits = capability.limits;
  return {
    prompt: "沿着真实参考素材的光线与运动关系创作一段视频。",
    duration: limits.durations.at(-1),
    aspectRatio: limits.aspectRatios.at(-1),
    resolution: limits.resolutions.at(-1),
    outputCount: limits.outputCounts.at(-1),
    faceEnabled: false,
    files: Object.fromEntries([
      ["image", limits.maxImages], ["video", limits.maxVideos], ["audio", limits.maxAudio],
    ].map(([kind, count]) => [kind, Array.from({ length: count }, (_, index) => ({
      asset_id: `${kind}-asset-${index + 1}`,
      media_type: kind,
    }))])),
  };
}

function pendingRequest(model, requestPayload) {
  return {
    version: 5,
    workspaceKey: ARK_TEST_COMPANY_ID,
    companyId: ARK_TEST_COMPANY_ID,
    modelId: model.id,
    capabilityVersion: model.capability_version,
    quoteRevision: model.quote_revision,
    requestPayload,
    fingerprint: taskRequestFingerprint(JSON.stringify({
      modelId: model.id,
      capabilityVersion: model.capability_version,
      quoteRevision: model.quote_revision,
      requestPayload,
    })),
    idempotencyKey: "ark-test-original-request-0001",
    uncertain: true,
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

test("Ark test discovery derives all seven current identities from the single reviewed catalog", () => {
  assert.equal(ARK_VIDEO_CATALOG.schema_version, 1);
  assert.deepEqual(ARK_VIDEO_CURRENT_MODELS.map((model) => model.public_model_id).sort(), [
    "seedance-1.0-pro", "seedance-1.0-pro-fast", "seedance-1.5-pro",
    "seedance-2.0", "seedance-2.0-fast", "seedance-2.0-mini", "seedance-2.5",
  ].sort());
  const responses = arkDiscoveryResponses();
  assert.equal(responses.length, 7);
  for (const response of responses) {
    const source = ARK_VIDEO_CURRENT_MODELS.find((model) => model.public_model_id === response.slug);
    assert.deepEqual(response.effective_capabilities, source.capability);
    assert.equal(response.display_name, source.display_name);
    assert.notEqual(response.effective_capabilities, source.capability);
    assert.ok(!["provider_model_id", "adapter_profile_id", "endpoint", "api_key"].some(
      (key) => Object.hasOwn(response, key),
    ));
  }
});

test("every current Ark mode keeps exact effective limits and request replay without provider options", async (t) => {
  for (const response of arkDiscoveryResponses()) {
    const effective = resolveEffectiveCapabilities(response);
    assert.deepEqual(Object.keys(effective.modes), Object.keys(response.effective_capabilities.modes));
    for (const [mode, capability] of Object.entries(effective.modes)) {
      await t.test(`${response.slug}/${mode}`, () => {
        const source = response.effective_capabilities.modes[mode];
        assert.deepEqual(capability.limits.durations, [...source.limits.duration_seconds].sort((a, b) => a - b));
        assert.deepEqual(capability.limits.resolutions, source.limits.resolutions);
        assert.deepEqual(capability.limits.aspectRatios, source.limits.aspect_ratios);
        assert.deepEqual(capabilityControlVisibility(capability), {
          image: source.limits.max_images > 0,
          video: source.limits.max_videos > 0,
          audio: source.limits.max_audio > 0,
          face: false,
        });
        const draft = maximumDraft(capability);
        const built = buildCapabilityRequestPayload(effective, mode, {
          ...draft,
          generate_audio: true,
          seed: 42,
          camera_fixed: true,
          return_last_frame: true,
          endpoint: "https://provider.invalid/forbidden",
        });
        assert.equal(built.ok, true, built.error);
        assert.ok(Object.keys(built.payload).every((key) => PUBLIC_REQUEST_KEYS.has(key)));
        assert.equal(built.payload.duration_seconds, draft.duration);
        assert.equal(built.payload.resolution, draft.resolution);
        assert.equal(built.payload.assets?.length ?? 0,
          source.limits.max_images + source.limits.max_videos + source.limits.max_audio);

        const storage = memoryStorage();
        const pending = pendingRequest(response, built.payload);
        rememberPendingCreate(ARK_TEST_COMPANY_ID, pending, storage);
        const restored = readPendingCreate(ARK_TEST_COMPANY_ID, storage);
        assert.deepEqual(restored, pending);
        assert.equal(readPendingCreate("another-workspace", storage), null);
        const restoredDraft = {
          prompt: restored.requestPayload.prompt,
          duration: restored.requestPayload.duration_seconds,
          aspectRatio: restored.requestPayload.aspect_ratio,
          resolution: restored.requestPayload.resolution,
          outputCount: restored.requestPayload.output_count,
          faceEnabled: Boolean(restored.requestPayload.face_enabled),
          files: filesFromPendingRequest(restored.requestPayload),
        };
        const rebuilt = buildCapabilityRequestPayload(effective, mode, restoredDraft);
        assert.equal(rebuilt.ok, true);
        assert.deepEqual(rebuilt.payload, built.payload);
      });
    }
  }
});

test("Ark 30-second, 4k, and fast/mini 720p ceilings remain distinct", () => {
  const capability = (id) => resolveEffectiveCapabilities(arkModelResponse(id)).modes.text_to_video;
  const newest = capability("seedance-2.5");
  assert.equal(newest.limits.durations.at(-1), 30);
  assert.equal(newest.limits.resolutions.includes("4k"), false);
  const second = capability("seedance-2.0");
  assert.equal(second.limits.durations.at(-1), 15);
  assert.equal(second.limits.resolutions.includes("4k"), true);
  for (const id of ["seedance-2.0-fast", "seedance-2.0-mini"]) {
    const fast = capability(id);
    assert.equal(fast.limits.durations.at(-1), 15);
    assert.deepEqual(fast.limits.resolutions, ["480p", "720p"]);
  }
});

test("model switches synchronously remove stale duration, resolution, media and face values", () => {
  const full = resolveEffectiveCapabilities(arkModelResponse("seedance-2.5"));
  const draft = maximumDraft(full.modes.video_to_video);
  assert.equal(draft.files.image.length, 9);
  assert.equal(draft.files.video.length, 3);
  assert.equal(draft.files.audio.length, 3);
  const limited = resolveEffectiveCapabilities(arkModelResponse("seedance-1.0-pro-fast"));
  const changed = reconcileGenerationDraft(limited, "image_to_video", {
    ...draft, resolution: "4k", faceEnabled: true,
  });
  assert.equal(changed.ok, true);
  assert.equal(changed.draft.files.image.length, 1);
  assert.equal(changed.draft.files.video.length, 0);
  assert.equal(changed.draft.files.audio.length, 0);
  assert.equal(changed.removedMediaCount, 14);
  assert.ok(changed.capability.limits.durations.includes(changed.draft.duration));
  assert.ok(changed.capability.limits.resolutions.includes(changed.draft.resolution));
  assert.equal(changed.draft.faceEnabled, false);
  const text = reconcileGenerationDraft(limited, "text_to_video", changed.draft);
  assert.deepEqual(text.draft.files, { image: [], video: [], audio: [] });
});

test("unapproved workspace discovery and missing readiness never become enabled by the frontend", () => {
  assert.deepEqual(arkDiscoveryResponses({ publicModelIds: [] }), []);
  for (const state of ["blocked", "missing"]) {
    for (const model of arkDiscoveryResponses({ readiness: state })) {
      for (const mode of Object.keys(model.effective_capabilities.modes)) {
        const readiness = resolveGenerationReadiness(normalizeModeReadiness(model.mode_readiness), mode);
        assert.equal(readiness.ready, false);
        assert.equal(readiness.status, state === "missing" ? "unverified" : "blocked");
      }
    }
  }
  assert.deepEqual(resolveEffectiveCapabilities({ effective_capabilities: { schema_version: 3, modes: {} } }).modes, {});
});

test("pending Ark confirmation rejects unknown options even with a matching recomputed fingerprint", () => {
  const model = arkModelResponse("seedance-2.5");
  const effective = resolveEffectiveCapabilities(model);
  const built = buildCapabilityRequestPayload(effective, "text_to_video", maximumDraft(effective.modes.text_to_video));
  for (const extra of ["generate_audio", "seed", "camera_fixed", "return_last_frame", "provider_model_id"]) {
    const storage = memoryStorage();
    rememberPendingCreate(ARK_TEST_COMPANY_ID, pendingRequest(model, { ...built.payload, [extra]: true }), storage);
    assert.equal(readPendingCreate(ARK_TEST_COMPANY_ID, storage), null, extra);
  }
});

test("Platform discovery and task submission stay provider-neutral with exact revision/idempotency pins", async () => {
  const responses = arkDiscoveryResponses();
  const captured = [];
  const client = createPlatformClient({
    baseUrl: "https://platform.example",
    companyId: ARK_TEST_COMPANY_ID,
    csrfToken: "ark-test-csrf-not-a-real-token",
    fetcher: async (url, options) => {
      captured.push({ url, options });
      return new Response(JSON.stringify(options.method === "POST" ? {
        id: "fixture-task", status: "accepted",
      } : responses), { headers: { "content-type": "application/json" } });
    },
  });
  assert.deepEqual(await client.listModels(), responses);
  const model = responses[0];
  const effective = resolveEffectiveCapabilities(model);
  const mode = Object.keys(effective.modes)[0];
  const built = buildCapabilityRequestPayload(effective, mode, maximumDraft(effective.modes[mode]));
  await client.createTask({
    modelId: model.id,
    requestPayload: built.payload,
    expectedCapabilityVersion: model.capability_version,
    expectedQuoteRevision: model.quote_revision,
  }, { idempotencyKey: "ark-test-exact-key-0001" });
  assert.equal(captured.length, 2);
  assert.equal(captured[0].url, `https://platform.example/api/v1/companies/${ARK_TEST_COMPANY_ID}/models`);
  assert.equal(captured[1].url, `https://platform.example/api/v1/companies/${ARK_TEST_COMPANY_ID}/tasks`);
  assert.deepEqual(JSON.parse(captured[1].options.body), {
    model_id: model.id,
    idempotency_key: "ark-test-exact-key-0001",
    request_payload: built.payload,
    expected_capability_version: model.capability_version,
    expected_quote_revision: model.quote_revision,
  });
  assert.equal(captured[1].options.headers.Authorization, undefined);
});

test("video-to-video UI uses neutral reference semantics rather than promising a redraw", () => {
  assert.equal(modeLabel("video_to_video"), "视频参考");
  const capability = resolveEffectiveCapabilities(arkModelResponse("seedance-2.5"));
  const draft = maximumDraft(capability.modes.video_to_video);
  draft.files.video = [];
  const built = buildCapabilityRequestPayload(capability, "video_to_video", draft);
  assert.equal(built.ok, false);
  assert.equal(built.error, "视频参考至少需要 1 个参考视频。");
});
