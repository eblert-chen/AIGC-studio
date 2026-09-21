import assert from "node:assert/strict";
import test from "node:test";
import {
  buildCapabilityRequestPayload, capabilityControlVisibility, normalizeModeReadiness,
  generationPromptLength, truncateGenerationPrompt,
  reconcileGenerationDraft, resolveEffectiveCapabilities, resolveGenerationReadiness,
} from "../src/modelCapabilities.js";
import { createPlatformClient } from "../src/api/platformClient.js";
import { filesFromPendingRequest, readPendingCreate, rememberPendingCreate, taskRequestFingerprint } from "../src/app/pendingGeneration.js";
import { ARK_TEST_COMPANY_ID, arkModelResponse } from "./fixtures/ark-video-discovery.mjs";
import { MINIMAX_H3_CATALOG, minimaxH3DiscoveryResponses, minimaxH3ModelResponse } from "./fixtures/minimax-h3-discovery.mjs";

const PUBLIC_KEYS = ["mode", "prompt", "duration_seconds", "aspect_ratio", "resolution", "output_count", "face_enabled", "assets"];

function fullDraft(capability) {
  const limits = capability.limits;
  return {
    prompt: "按参考素材的光线与动作创作，不改变原始制作意图。",
    duration: limits.durations.at(-1), aspectRatio: limits.aspectRatios[0],
    resolution: limits.resolutions.at(-1), outputCount: 1, faceEnabled: false,
    files: Object.fromEntries([["image", limits.maxImages], ["video", limits.maxVideos], ["audio", limits.maxAudio]]
      .map(([kind, count]) => [kind, Array.from({ length: count }, (_, index) => ({
        asset_id: `${kind}-fixture-${index + 1}`, media_type: kind,
      }))])),
  };
}

function pending(model, requestPayload) {
  const identity = { modelId: model.id, capabilityVersion: model.capability_version, quoteRevision: model.quote_revision, requestPayload };
  return { version: 5, workspaceKey: ARK_TEST_COMPANY_ID, companyId: ARK_TEST_COMPANY_ID,
    ...identity, fingerprint: taskRequestFingerprint(JSON.stringify(identity)),
    idempotencyKey: "minimax-original-request-0001", uncertain: true };
}

function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

test("MiniMax fixture identities/capabilities come from one reviewed manifest, never live authorization", () => {
  assert.equal(MINIMAX_H3_CATALOG.schema_version, 1);
  assert.deepEqual(MINIMAX_H3_CATALOG.models.map((model) => model.public_model_id).sort(), ["minimax-h3", "minimax-h3-max"]);
  for (const model of minimaxH3DiscoveryResponses()) {
    const source = MINIMAX_H3_CATALOG.models.find((entry) => entry.public_model_id === model.slug);
    assert.deepEqual(model.effective_capabilities, source.capability);
    assert.notEqual(model.effective_capabilities, source.capability);
    assert.equal(model.display_name, source.display_name);
    for (const field of ["provider_model_id", "adapter_profile_id", "api_key", "endpoint"]) assert.equal(Object.hasOwn(model, field), false);
  }
});

test("all H3 / Max modes retain exact resolution, prompt, capacity and replay contracts", async (t) => {
  for (const model of minimaxH3DiscoveryResponses()) {
    const capabilities = resolveEffectiveCapabilities(model);
    for (const [mode, capability] of Object.entries(capabilities.modes)) {
      await t.test(`${model.slug}/${mode}`, () => {
        const source = model.effective_capabilities.modes[mode];
        assert.equal(capability.limits.maxPromptLength, 7000);
        assert.deepEqual(capability.limits.resolutions, source.limits.resolutions);
        assert.equal(capability.limits.resolutions.includes("720p"), false);
        assert.deepEqual(capability.limits.durations, source.limits.duration_seconds);
        assert.deepEqual(capabilityControlVisibility(capability), {
          image: source.limits.max_images > 0, video: source.limits.max_videos > 0,
          audio: source.limits.max_audio > 0, face: false,
        });
        const draft = fullDraft(capability);
        draft.prompt = "中".repeat(7000);
        const built = buildCapabilityRequestPayload(capabilities, mode, {
          ...draft, prompt_optimizer: true, generate_audio: false, provider_model_id: "wrong-model",
        });
        assert.equal(built.ok, true, built.error);
        assert.equal(built.payload.prompt, draft.prompt);
        assert.equal(built.payload.resolution, draft.resolution);
        assert.equal(built.payload.assets?.length ?? 0, source.limits.max_images + source.limits.max_videos + source.limits.max_audio);
        assert.ok((built.payload.assets?.length ?? 0) <= 12);
        assert.ok(Object.keys(built.payload).every((key) => PUBLIC_KEYS.includes(key)));
        const cache = storage();
        const original = pending(model, built.payload);
        rememberPendingCreate(ARK_TEST_COMPANY_ID, original, cache);
        assert.deepEqual(readPendingCreate(ARK_TEST_COMPANY_ID, cache), original);
        assert.equal(readPendingCreate("other-workspace", cache), null);
        assert.deepEqual(filesFromPendingRequest(original.requestPayload).video.map((file) => file.asset_id), draft.files.video.map((file) => file.asset_id));
      });
    }
  }
});

test("Ark -> H3 -> Max removes 30s/4k/extra references without translating 768p to 720p", () => {
  const ark = resolveEffectiveCapabilities(arkModelResponse("seedance-2.5"));
  const h3 = resolveEffectiveCapabilities(minimaxH3ModelResponse("minimax-h3"));
  const maximum = resolveEffectiveCapabilities(minimaxH3ModelResponse("minimax-h3-max"));
  const draft = { ...fullDraft(ark.modes.video_to_video), resolution: "4k", faceEnabled: true };
  assert.equal(draft.duration, 30);
  const changed = reconcileGenerationDraft(h3, "video_to_video", draft);
  assert.equal(changed.ok, true);
  assert.equal(changed.removedMediaCount, 3);
  assert.deepEqual(Object.values(changed.draft.files).map((files) => files.length), [6, 3, 3]);
  assert.equal(changed.draft.duration, 4);
  assert.equal(changed.draft.resolution, "768p");
  assert.equal(changed.draft.faceEnabled, false);
  const twoK = reconcileGenerationDraft(h3, "video_to_video", { ...changed.draft, resolution: "2k" });
  assert.equal(twoK.draft.resolution, "2k");
  const onlyText = reconcileGenerationDraft(maximum, "video_to_video", twoK.draft);
  assert.equal(onlyText.mode, "text_to_video");
  assert.equal(onlyText.draft.duration, 5);
  assert.deepEqual(onlyText.draft.files, { image: [], video: [], audio: [] });
  assert.equal(onlyText.removedMediaCount, 12);
  assert.equal(onlyText.draft.resolution, "480p");
  assert.equal(buildCapabilityRequestPayload(maximum, "video_to_video", twoK.draft).ok, false);
  const compatible = reconcileGenerationDraft(maximum, "text_to_video", { ...onlyText.draft, resolution: "768p" });
  assert.equal(compatible.draft.resolution, "768p");
  const backToArk = reconcileGenerationDraft(ark, "text_to_video", compatible.draft);
  assert.equal(backToArk.draft.resolution, "480p");
});

test("7000-code-point H3 prompts preserve emoji and pending replay without lone surrogates", () => {
  const model = minimaxH3ModelResponse("minimax-h3");
  const capabilities = resolveEffectiveCapabilities(model);
  for (const prompt of ["中".repeat(6999) + "🎬", "🎬".repeat(7000), "镜头🎬".repeat(2333) + "中"]) {
    assert.equal(generationPromptLength(prompt), 7000);
    const built = buildCapabilityRequestPayload(capabilities, "text_to_video", {
      ...fullDraft(capabilities.modes.text_to_video), prompt,
    });
    assert.equal(built.ok, true);
    assert.equal(built.payload.prompt, prompt);
    assert.equal(built.reconciled.changes.includes("prompt"), false);
    assert.equal(truncateGenerationPrompt(prompt + "🚀", 7000), prompt);
    const cache = storage();
    const original = pending(model, built.payload);
    rememberPendingCreate(ARK_TEST_COMPANY_ID, original, cache);
    assert.deepEqual(readPendingCreate(ARK_TEST_COMPANY_ID, cache), original);
  }
  const capped = reconcileGenerationDraft(capabilities, "text_to_video", {
    ...fullDraft(capabilities.modes.text_to_video), prompt: "🎬".repeat(7001),
  });
  assert.equal(capped.draft.prompt, "🎬".repeat(7000));
  const cache = storage();
  const base = buildCapabilityRequestPayload(capabilities, "text_to_video", fullDraft(capabilities.modes.text_to_video)).payload;
  rememberPendingCreate(ARK_TEST_COMPANY_ID, pending(model, { ...base, prompt: "🎬".repeat(10001) }), cache);
  assert.equal(readPendingCreate(ARK_TEST_COMPANY_ID, cache), null);
});

test("H3 i2v excludes videos and audio-only requests cannot masquerade as t2v/v2v", () => {
  const h3 = resolveEffectiveCapabilities(minimaxH3ModelResponse("minimax-h3"));
  const draft = fullDraft(h3.modes.video_to_video);
  const image = reconcileGenerationDraft(h3, "image_to_video", draft);
  assert.equal(image.draft.files.video.length, 0);
  assert.equal(image.draft.files.audio.length, 3);
  image.draft.files.image = [];
  assert.equal(buildCapabilityRequestPayload(h3, "image_to_video", image.draft).ok, false);
  draft.files.video = [];
  assert.equal(buildCapabilityRequestPayload(h3, "video_to_video", draft).ok, false);
  const text = buildCapabilityRequestPayload(h3, "text_to_video", draft);
  assert.equal(text.ok, true);
  assert.equal(Object.hasOwn(text.payload, "assets"), false);
});

test("unknown H3 submissions preserve original payload/idempotency/quote despite newer discovery", async () => {
  const model = minimaxH3ModelResponse("minimax-h3");
  const capability = resolveEffectiveCapabilities(model);
  const built = buildCapabilityRequestPayload(capability, "video_to_video", fullDraft(capability.modes.video_to_video));
  const cache = storage();
  const original = pending(model, built.payload);
  rememberPendingCreate(ARK_TEST_COMPANY_ID, original, cache);
  model.quote_revision = `sha256:${"f".repeat(64)}`;
  model.capability_version += 1;
  model.unit_price_points = 999;
  const restored = readPendingCreate(ARK_TEST_COMPANY_ID, cache);
  assert.deepEqual(restored, original);
  const requests = [];
  const client = createPlatformClient({ baseUrl: "https://platform.example.test", companyId: ARK_TEST_COMPANY_ID,
    fetcher: async (url, options) => {
      requests.push({ url, options });
      return new Response(JSON.stringify({ id: "synthetic-task", status: "accepted" }), { headers: { "content-type": "application/json" } });
    } });
  await client.createTask({ modelId: restored.modelId, requestPayload: restored.requestPayload,
    expectedCapabilityVersion: restored.capabilityVersion, expectedQuoteRevision: restored.quoteRevision },
  { idempotencyKey: restored.idempotencyKey });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, `https://platform.example.test/api/v1/companies/${ARK_TEST_COMPANY_ID}/tasks`);
  assert.deepEqual(JSON.parse(requests[0].options.body), { model_id: original.modelId, request_payload: original.requestPayload,
    idempotency_key: original.idempotencyKey, expected_capability_version: original.capabilityVersion, expected_quote_revision: original.quoteRevision });
  assert.equal(requests[0].options.headers.Authorization, undefined);
});

test("pending H3 requests reject provider passthrough even with a recomputed fingerprint", () => {
  const model = minimaxH3ModelResponse("minimax-h3");
  const capabilities = resolveEffectiveCapabilities(model);
  const built = buildCapabilityRequestPayload(capabilities, "text_to_video", fullDraft(capabilities.modes.text_to_video));
  for (const field of ["model", "prompt_optimizer", "generate_audio", "callback_url", "provider_model_id", "seed"]) {
    const cache = storage();
    rememberPendingCreate(ARK_TEST_COMPANY_ID, pending(model, { ...built.payload, [field]: true }), cache);
    assert.equal(readPendingCreate(ARK_TEST_COMPANY_ID, cache), null, field);
  }
});

test("no H3 workspace grants or readiness evidence means no enabled creation path", () => {
  assert.deepEqual(minimaxH3DiscoveryResponses({ publicModelIds: [] }), []);
  for (const status of ["missing", "blocked"]) {
    for (const model of minimaxH3DiscoveryResponses({ readiness: status })) {
      for (const mode of Object.keys(model.effective_capabilities.modes)) {
        const readiness = resolveGenerationReadiness(normalizeModeReadiness(model.mode_readiness), mode);
        assert.equal(readiness.ready, false);
        assert.equal(readiness.status, status === "missing" ? "unverified" : "blocked");
      }
    }
  }
});
