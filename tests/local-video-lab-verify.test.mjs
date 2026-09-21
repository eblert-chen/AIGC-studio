import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { basename, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { labCookieName } from "../scripts/local-video-lab-gateway.mjs";
import { verifyLocalVideoLab } from "../scripts/local-video-lab-verify.mjs";

// These are transport-contract unit tests, not running services, playable
// provider output, paid-provider acceptance, or a substitute for the verifier.
const contracts = (await Promise.all(["seedance_models.v1.json", "minimax_h3_models.v1.json"].map(async (name) =>
  JSON.parse(await readFile(new URL(`../backend/new-api-relay/generationprofile/${name}`, import.meta.url), "utf8")))))
  .flatMap((document) => document.models)
  .filter((model) => model.new_routes_allowed === true && model.lifecycle === "acceptance_candidate"
    && (!model.eos_at || Date.parse(model.eos_at) > Date.now()))
  .sort((left, right) => left.public_model_id.localeCompare(right.public_model_id));
const bootstrapToken = "unit-only-bootstrap-credential-" + "b".repeat(32);
const sessionSecret = "unit-only-session-private";
const csrfSecret = "unit-only-csrf-private";
const signedSecret = "unit-only-private-signed-object";
const callbackSecret = "unit-only-operations-private";
const uuid = (number) => `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
const digest = (value) => createHash("sha256").update(value).digest("hex");
const json = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json", ...headers },
});

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.keys(value).sort().map((key) => JSON.stringify(key) + ":" + canonical(value[key])).join(",")}}`;
  return JSON.stringify(value);
}

function sortedCapability(value) {
  if (Array.isArray(value)) return [...value].sort((a, b) => typeof a === "number" ? a - b : String(a).localeCompare(String(b)));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, sortedCapability(item)]));
  return value;
}

function inputs() {
  const manifest = {
    schema_version: 1, kind: "ai-video-local-video-lab", lab_id: "mock-0123456789ab",
    instance_nonce: "1".repeat(32), provider_mode: "mock", created_at_utc: new Date().toISOString(),
    targets: { gateway_base: "http://127.0.0.1:18480", platform_base: "http://127.0.0.1:18420",
      relay_base: "http://127.0.0.1:18430", frontend_base: "http://127.0.0.1:14178" },
    isolation: { compose_project: "ai-video-lab-mock-0123456789ab",
      platform_database: { host: "platform-db-lab", port: 5432, database: "video_lab_mock_0123456789ab" },
      relay_service_base: "http://relay-lab:3000" },
    budget: { test_points: 2000, unit_price_points_per_second: 1, call_quota: 20, concurrency_limit: 1 },
    paid_probe_approval: null,
  };
  const receipt = {
    schema_version: 1, kind: manifest.kind, lab_id: manifest.lab_id, provider_mode: "mock", test_data_only: true,
    real_provider_acceptance: false, manifest_sha256: "sha256:" + digest(canonical(manifest)),
    company_id: uuid(101), user_id: uuid(102), admin_user_id: uuid(103),
    models: contracts.map((item, index) => {
      const capability = sortedCapability(item.capability);
      const limits = capability.modes.text_to_video.limits;
      return { id: uuid(index + 1), slug: item.public_model_id, status: "ready", expected_capability_version: 3,
        expected_quote_revision: "sha256:" + digest(item.public_model_id), effective_capabilities: capability,
        request_payload: { mode: "text_to_video", prompt: "本地单元合同样本，仅验证请求路径。", duration_seconds: Math.min(...limits.duration_seconds),
          resolution: limits.resolutions[0], aspect_ratio: limits.aspect_ratios[0], output_count: 1, face_enabled: false } };
    }),
  };
  return { manifest, receipt, bootstrapToken };
}

function assertSafe(report) {
  const encoded = JSON.stringify(report);
  for (const secret of [bootstrapToken, sessionSecret, csrfSecret, signedSecret, callbackSecret]) assert.ok(!encoded.includes(secret));
  assert.ok(!encoded.includes("Signature="));
  assert.ok(!encoded.includes("Set-Cookie"));
  assert.equal(report.test_data_only, true);
  assert.equal(report.real_provider_acceptance, false);
}

async function referenceFixtures(t, { imageWidth = 1024, imageHeight = 576, duration = 4, frameRate = 24 } = {}) {
  const base = resolve(fileURLToPath(new URL("../.tmp/local-video-lab/", import.meta.url)));
  await mkdir(base, { recursive: true });
  const directory = await mkdtemp(join(base, "verifier-unit-"));
  t.after(async () => {
    const target = resolve(directory);
    assert.ok(target.startsWith(base + sep) && basename(target).startsWith("verifier-unit-"));
    await rm(target, { recursive: true, force: true });
  });
  // Metadata-only unit bytes. The real runner supplies project-owned imagery
  // and ffmpeg-muxed video; neither production HTTP nor media is replaced here.
  const png = Buffer.alloc(40);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(png);
  png.writeUInt32BE(13, 8); png.write("IHDR", 12); png.writeUInt32BE(imageWidth, 16); png.writeUInt32BE(imageHeight, 20);
  function box(kind, data) {
    const header = Buffer.alloc(8); header.writeUInt32BE(data.length + 8); header.write(kind, 4);
    return Buffer.concat([header, data]);
  }
  const clock = Buffer.alloc(20);
  clock.writeUInt32BE(24_000, 12); clock.writeUInt32BE(duration * 24_000, 16);
  const dimensions = Buffer.alloc(84);
  dimensions.writeUInt32BE(1024 * 65536, 76); dimensions.writeUInt32BE(576 * 65536, 80);
  const handler = Buffer.alloc(12); handler.write("vide", 8);
  const description = Buffer.alloc(8); description.writeUInt32BE(1, 4);
  const timing = Buffer.alloc(16); timing.writeUInt32BE(1, 4); timing.writeUInt32BE(duration * frameRate, 8); timing.writeUInt32BE(24_000 / frameRate, 12);
  const samples = box("stbl", Buffer.concat([box("stsd", Buffer.concat([description, box("avc1", Buffer.alloc(78))])), box("stts", timing)]));
  const media = box("mdia", Buffer.concat([box("mdhd", clock), box("hdlr", handler), box("minf", samples)]));
  const track = box("trak", Buffer.concat([box("tkhd", dimensions), media]));
  const video = Buffer.concat([box("ftyp", Buffer.from("isom0000isom")), box("moov", Buffer.concat([box("mvhd", clock), track])), box("mdat", Buffer.alloc(256, 0x6a))]);
  const imagePath = join(directory, "reference.png");
  const videoPath = join(directory, "reference.mp4");
  await writeFile(imagePath, png);
  await writeFile(videoPath, video);
  return { imagePath, videoPath };
}

function mockTransport(t, input, options = {}) {
  const { manifest, receipt } = input;
  const companyPath = `/api/v1/companies/${receipt.company_id}`;
  // Deliberately not a playable movie: these bytes only test size/hash/Range.
  const bytes = Buffer.concat([Buffer.from([0, 0, 0, 24]), Buffer.from("ftypisom"), Buffer.alloc(148, 0x6c)]);
  const state = { requests: [], tasks: new Map(), keys: new Map(), taskGets: new Map(), providerPosts: 8,
    availablePoints: manifest.budget.test_points, positivePosts: 0, negativePosts: 0, mediaRequests: 0,
    identityReads: 0, loginCalls: 0, evidenceReads: 0, unknownThrown: false, assets: new Map(), assetKeys: new Map(),
    assetBytes: new Map(), uploadPosts: 0, inputFetches: 0, inputBytes: 0 };
  const originalSession = "__Host-ai_video_session";
  const originalCsrf = "__Host-ai_video_csrf";
  const mappedSession = labCookieName(originalSession, manifest.lab_id);
  const mappedCsrf = labCookieName(originalCsrf, manifest.lab_id);

  function assertSession(headers, body) {
    assert.equal(headers.get("cookie"), `${mappedSession}=${sessionSecret}; ${mappedCsrf}=${csrfSecret}`);
    assert.equal(headers.get("authorization"), null);
    assert.equal(headers.get("x-user-id"), null);
    assert.equal(headers.get("x-bootstrap-token"), null);
    if (body !== undefined) {
      assert.equal(headers.get("x-csrf-token"), csrfSecret);
      assert.equal(headers.get("origin"), manifest.targets.frontend_base);
    }
  }

  function responseTask(task) {
    const value = structuredClone(task);
    if (options.payloadDrift) value.request_payload.prompt = "Unexpected changed payload";
    if (options.capabilityDrift) value.capability_snapshot.capability_version += 1;
    if (options.quoteDrift) value.pricing_snapshot.quote_revision = "sha256:" + "f".repeat(64);
    if (options.missingArtifact) value.output_artifacts = [];
    return value;
  }

  function newTask(model, payload = model.request_payload) {
    const number = state.tasks.size + 1;
    const quote = payload.duration_seconds * manifest.budget.unit_price_points_per_second;
    return { id: uuid(1000 + number), company_id: receipt.company_id, user_id: receipt.user_id, model_id: model.id,
      status: "succeeded", request_payload: { ...structuredClone(payload), assets: payload.assets || [] },
      billing_unit: "POINT", billing_version: 2, quote_cents: null, quote_points: quote,
      pricing_snapshot: { quote_revision: model.expected_quote_revision, unit_price_points: manifest.budget.unit_price_points_per_second },
      capability_snapshot: { model_id: model.id, model_slug: model.slug, capability_version: model.expected_capability_version,
        effective_capabilities: structuredClone(model.effective_capabilities) },
      actual_cost_cents: null, actual_cost_points: quote, reserved_cents: 0, reserved_points: 0,
      relay_job_id: uuid(3000 + number), output_artifacts: [{ asset_id: uuid(2000 + number), media_type: "video",
        content_type: "video/mp4", size_bytes: bytes.length, sha256: digest(bytes) }],
    };
  }

  t.mock.method(globalThis, "fetch", async (target, init = {}) => {
    const url = new URL(target);
    const headers = new Headers(init.headers);
    const method = init.method || "GET";
    const body = init.body === undefined ? undefined : init.body instanceof FormData ? init.body : JSON.parse(init.body);
    state.requests.push({ target: String(target), method, body });
    assert.equal(init.redirect, "manual");
    assert.ok(init.signal instanceof AbortSignal);
    assert.ok([manifest.targets.platform_base, manifest.targets.frontend_base].includes(url.origin));

    if (url.origin === manifest.targets.platform_base) {
      assert.equal(headers.get("x-bootstrap-token"), bootstrapToken);
      assert.equal(headers.get("x-local-video-lab-id"), manifest.lab_id);
      assert.equal(headers.get("x-local-video-lab-nonce"), manifest.instance_nonce);
      assert.equal(headers.get("cookie"), null);
      if (url.pathname === "/internal/local-video-lab/identity") {
        assert.equal(method, "GET");
        state.identityReads += 1;
        if (options.redirectIdentity) return new Response(null, { status: 302, headers: { Location: "https://untrusted.invalid/" + signedSecret } });
        return json({ kind: manifest.kind, lab_id: manifest.lab_id, instance_nonce: manifest.instance_nonce,
          provider_mode: "mock", manifest_sha256: options.identityMismatch ? "sha256:" + "0".repeat(64) : receipt.manifest_sha256,
          company_id: receipt.company_id, user_id: receipt.user_id, storage_bound: true, test_data_only: true });
      }
      assert.equal(url.pathname, "/internal/local-video-lab/login");
      assert.equal(method, "POST");
      assert.deepEqual(body, {});
      state.loginCalls += 1;
      const loginHeaders = new Headers({ "Content-Type": "application/json" });
      loginHeaders.append("Set-Cookie", `${originalSession}=${sessionSecret}; HttpOnly; ${options.insecureCookie ? "" : "Secure; "}SameSite=lax; Path=/`);
      loginHeaders.append("Set-Cookie", `${originalCsrf}=${csrfSecret}; Secure; SameSite=lax; Path=/`);
      return new Response(JSON.stringify({ lab_id: manifest.lab_id, company_id: receipt.company_id, user_id: receipt.user_id,
        identity_provenance: "local_lab_bootstrap" }), { status: 200, headers: loginHeaders });
    }
    assert.equal(headers.get("x-bootstrap-token"), null);
    if (url.pathname === "/__local-video-lab__/identity") return json({ kind: manifest.kind, lab_id: manifest.lab_id,
      provider_mode: "mock", frontend_base: manifest.targets.frontend_base });

    const signedInput = url.pathname.match(/^\/api\/v1\/input-assets\/([^/]+)\/content$/);
    if (signedInput) {
      assert.equal(headers.get("cookie"), null);
      const asset = state.assets.get(signedInput[1]);
      if (!asset || asset.status !== "active" || (!options.ignoreInputSignatures
        && (url.searchParams.get("signature") !== "a".repeat(64) || Number(url.searchParams.get("expires")) <= Date.now() / 1000))) return json({ detail: "Not found" }, 404);
      const bytes = Buffer.from(state.assetBytes.get(asset.id));
      if (options.corruptInputBytes) bytes[bytes.length - 1] ^= 1;
      return new Response(bytes, { headers: { "Content-Type": asset.content_type, "Content-Length": String(bytes.length) } });
    }
    if (url.pathname.startsWith("/__local-video-lab__/objects/")) {
      assertSession(headers);
      assert.equal(headers.get("x-csrf-token"), null);
      assert.equal(method, "GET");
      state.mediaRequests += 1;
      if (headers.get("range")) {
        assert.equal(headers.get("range"), "bytes=0-63");
        return new Response(bytes.subarray(0, 64), { status: options.ignoreRange ? 200 : 206,
          headers: { "Content-Type": "video/mp4", "Content-Length": "64",
            "Content-Range": `bytes 0-63/${options.badContentRange ? bytes.length + 1 : bytes.length}` } });
      }
      const result = Buffer.from(bytes);
      if (options.corruptBytes) result[result.length - 1] ^= 1;
      return new Response(result, { headers: { "Content-Type": "video/mp4", "Content-Length": String(result.length) } });
    }
    assertSession(headers, body);
    assert.equal(headers.get("x-company-id"), receipt.company_id);
    if (url.pathname === "/api/v1/auth/session") return json({ authenticated: true, active_product_context: "company", platform_admin: false,
      user: { id: options.wrongSessionOwner ? uuid(999) : receipt.user_id }, companies: [{ company_id: receipt.company_id }], csrf_token: csrfSecret });
    if (url.pathname === companyPath + "/models") return json(receipt.models.map((model) => ({ id: model.id, slug: model.slug,
      capability_version: model.expected_capability_version + (options.discoveryDrift ? 1 : 0), quote_revision: model.expected_quote_revision,
      effective_capabilities: model.effective_capabilities,
      mode_readiness: Object.fromEntries(Object.keys(model.effective_capabilities.modes).map((mode) => [mode,
        { default: { ready: !(options.referenceNotReady && mode !== "text_to_video") } }])),
      billing_unit: "POINT", billing_version: 2, unit_price_points: manifest.budget.unit_price_points_per_second })));
    if (url.pathname === companyPath + "/wallet") return json({ company_id: receipt.company_id, billing_unit: "POINT", billing_version: 2,
      available_cents: null, reserved_cents: null, available_points: state.availablePoints, reserved_points: options.existingReserved ? 1 : 0 });
    if (url.pathname === companyPath + "/assets") {
      assert.equal(method, "POST");
      assert.ok(body instanceof FormData);
      assert.equal(headers.get("content-type"), null, "fetch must supply the multipart boundary");
      const file = body.get("file");
      const bytes = Buffer.from(await file.arrayBuffer());
      const key = headers.get("idempotency-key");
      assert.ok(key.length <= 120);
      state.uploadPosts += 1;
      const replayId = state.assetKeys.get(key);
      let asset = replayId && state.assets.get(replayId);
      if (asset) {
        assert.equal(asset.sha256, digest(bytes));
        assert.equal(asset.original_filename, file.name);
      } else {
        asset = { id: uuid(5000 + state.assets.size + 1), company_id: receipt.company_id, uploaded_by_user_id: receipt.user_id,
          original_filename: file.name, media_type: body.get("media_type"), content_type: file.type,
          size_bytes: bytes.length, sha256: digest(bytes), status: "active" };
        state.assetKeys.set(key, asset.id);
        state.assets.set(asset.id, asset);
        state.assetBytes.set(asset.id, bytes);
      }
      return json({ ...asset, ...(options.uploadHashMismatch ? { sha256: "0".repeat(64) } : {}),
        ...(options.uploadReplayMismatch && replayId ? { id: uuid(5999) } : {}) }, 201);
    }
    const inputPath = url.pathname.match(new RegExp(`^${companyPath}/assets/([^/]+)(/preview)?$`));
    if (inputPath) {
      const asset = state.assets.get(inputPath[1]);
      assert.ok(asset);
      if (method === "DELETE") {
        assert.equal(headers.get("x-csrf-token"), csrfSecret);
        assert.equal(headers.get("origin"), manifest.targets.frontend_base);
        asset.status = "disabled";
        return new Response(null, { status: 204 });
      }
      assert.equal(method, "GET");
      assert.equal(inputPath[2], "/preview");
      if (asset.status !== "active") return json({ detail: "Not found" }, 404);
      return json({ expires_seconds: 300, url: (options.externalInputPreview ? "https://untrusted.invalid" : manifest.targets.frontend_base)
        + `/api/v1/input-assets/${asset.id}/content?expires=${Math.floor(Date.now() / 1000) + 300}&disposition=inline&signature=${"a".repeat(64)}` });
    }
    if (url.pathname === companyPath + "/tasks") {
      if (method === "GET") {
        assert.equal(url.search, "?limit=500");
        return json([...state.tasks.values()]);
      }
      assert.equal(method, "POST");
      assert.equal(headers.get("idempotency-key"), body.idempotency_key);
      assert.ok(body.idempotency_key.length <= 120);
      const model = receipt.models.find((item) => item.id === body.model_id);
      assert.ok(model);
      assert.equal(body.expected_capability_version, model.expected_capability_version);
      if (body.expected_quote_revision !== model.expected_quote_revision || body.request_payload.resolution === "lab-unsupported-resolution") {
        state.negativePosts += 1;
        if (options.negativeProviderPost) state.providerPosts += 1;
        if (options.negativeWalletDebit) state.availablePoints -= 1;
        if (options.negativeCreatesTask) state.tasks.set(uuid(9000), { id: uuid(9000) });
        return json({ error: { code: "CONFLICT", message: signedSecret } }, 409);
      }
      const payload = body.request_payload;
      if (payload.mode === "text_to_video") assert.deepEqual(payload, model.request_payload);
      else {
        const mode = model.effective_capabilities.modes[payload.mode];
        assert.ok(mode);
        assert.equal(payload.prompt, model.request_payload.prompt);
        assert.equal(payload.output_count, 1);
        assert.equal(payload.face_enabled, false);
        assert.ok(mode.limits.duration_seconds.includes(payload.duration_seconds));
        assert.ok(mode.limits.resolutions.includes(payload.resolution));
        assert.ok(mode.limits.aspect_ratios.includes(payload.aspect_ratio));
        assert.equal(payload.assets.length, 1);
        assert.deepEqual(Object.keys(payload.assets[0]).sort(), ["asset_id", "media_type"]);
        const asset = state.assets.get(payload.assets[0].asset_id);
        assert.ok(asset);
        assert.equal(asset.media_type, payload.mode === "image_to_video" ? "image" : "video");
        if (asset.status !== "active" && !state.keys.has(body.idempotency_key)) {
          state.negativePosts += 1;
          if (options.disabledInputProviderPost) state.providerPosts += 1;
          return json({ detail: "Not found" }, 404);
        }
      }
      state.positivePosts += 1;
      if (state.keys.has(body.idempotency_key)) {
        const replay = responseTask(state.tasks.get(state.keys.get(body.idempotency_key)));
        if (options.replayCreatesAnotherId) replay.id = uuid(9999);
        return json(replay, 201);
      }
      const task = newTask(model, payload);
      state.tasks.set(task.id, task);
      state.keys.set(body.idempotency_key, task.id);
      state.providerPosts += options.doubleProviderPost ? 2 : 1;
      if (!options.noProviderInputFetch) {
        state.inputFetches += (payload.assets || []).length;
        state.inputBytes += (payload.assets || []).reduce((sum, asset) => sum + state.assetBytes.get(asset.asset_id).length, 0);
      }
      state.availablePoints -= task.quote_points;
      if (options.unknownFirstCreate && !state.unknownThrown) {
        state.unknownThrown = true;
        throw new Error("Unknown result " + sessionSecret);
      }
      return json(responseTask(task), 201);
    }
    const previewMatch = url.pathname.match(new RegExp(`^${companyPath}/tasks/([^/]+)/artifacts/([^/]+)/preview$`));
    if (previewMatch) {
      const task = state.tasks.get(previewMatch[1]);
      assert.ok(task);
      assert.equal(task.output_artifacts[0].asset_id, previewMatch[2]);
      if (options.previewUnavailable) return json({ message: "storage binding missing " + signedSecret }, 502);
      return json({ preview_status: "issued", media_type: "video", content_type: "video/mp4", expires_seconds: 300,
        url: options.externalPreview ? "https://untrusted.invalid/video.mp4?Signature=" + signedSecret
          : manifest.targets.frontend_base + "/__local-video-lab__/objects/video-mock-" + "f".repeat(20)
            + `/outputs/${receipt.company_id}/${task.id}/${previewMatch[2]}?Signature=${signedSecret}` });
    }
    const taskMatch = url.pathname.match(new RegExp(`^${companyPath}/tasks/([^/]+)$`));
    assert.ok(taskMatch, "Unexpected contract URL");
    assert.equal(method, "GET");
    const count = (state.taskGets.get(taskMatch[1]) || 0) + 1;
    state.taskGets.set(taskMatch[1], count);
    const task = responseTask(state.tasks.get(taskMatch[1]));
    if (options.wrongPollId) task.id = uuid(9998);
    if (options.oneQueuedPoll && task.id === uuid(1001) && count === 1) task.status = "queued";
    return json(task);
  });
  const readRelayEvidence = async () => {
    state.evidenceReads += 1;
    if (options.evidenceThrows) throw new Error(callbackSecret);
    const native = { user_quota: options.initialNativeQuota ?? 0, user_used_quota: 0, user_request_count: 0,
      token_remain_quota: 0, token_used_quota: 0, token_unlimited_quota: true, native_consume_logs: 0, native_refund_logs: 0 };
    if (options.nativeChangeField && state.evidenceReads >= (options.nativeChangePhase === "negative" ? 3 : 2)) {
      const key = options.nativeChangeField;
      native[key] = typeof native[key] === "boolean" ? !native[key] : native[key] + 1;
    }
    if (options.missingNativeField) delete native[options.missingNativeField];
    if (options.invalidNativeField) native[options.invalidNativeField] = "0";
    if (options.extraNativeField) native.unrequested_secret = callbackSecret;
    return { mode: "mock", state_id: options.changedRelayState && state.evidenceReads > 1 ? "changed-state" : "mock-unit-state",
      production_acceptance: false, provider_posts_this_process: state.providerPosts, provider_polls_this_process: state.tasks.size,
      artifact_fetches_this_process: state.tasks.size, jobs: state.tasks.size + 8, native_tasks: state.tasks.size + 8,
      callback_deliveries: state.tasks.size, provider_terminal_outcomes: state.tasks.size + 8, provider_cost_events: state.tasks.size + 8,
      ...(options.missingNativeBilling ? {} : { native_billing: native }),
      ...(options.missingInputCounter ? {} : { input_fetches_this_process: state.inputFetches, input_bytes_this_process: state.inputBytes }),
      ignored_secret_field: callbackSecret };
  };
  return { state, readRelayEvidence };
}

test("mock verifier exercises eight exact contracts, real-session API shape, storage bytes and idempotent admission", async (t) => {
  const input = inputs();
  const { state, readRelayEvidence } = mockTransport(t, input);
  const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
  assert.equal(report.status, "passed", JSON.stringify(report.failure));
  assert.equal(report.created_task_count, 8);
  assert.equal(state.tasks.size, 8);
  assert.equal(state.positivePosts, 16);
  assert.equal(state.negativePosts, 16);
  assert.equal(state.mediaRequests, 16);
  assert.equal(state.taskGets.size, 8);
  assert.equal(report.negative_admission_uncharged, true);
  assert.equal(report.provider_evidence.positive_provider_posts, 8);
  assert.equal(report.provider_evidence.negative_provider_posts, 0);
  assert.equal(report.provider_evidence.before.provider_posts_this_process, 8);
  assert.equal(report.provider_evidence.after_negative.provider_posts_this_process, 16);
  assert.equal(report.provider_evidence.native_billing_unchanged_after_positive, true);
  assert.equal(report.provider_evidence.native_billing_unchanged_after_negative, true);
  assert.deepEqual(report.provider_evidence.before.native_billing, report.provider_evidence.after_negative.native_billing);
  for (const task of report.tasks) {
    assert.equal(task.status, "succeeded");
    assert.equal(task.idempotency_replayed, true);
    assert.equal(task.capability_version, 3);
    assert.equal(task.artifacts.length, 1);
    assert.equal(task.artifacts[0].bound_preview_verified, true);
    assert.equal(task.artifacts[0].bytes_verified, true);
    assert.equal(task.artifacts[0].range_verified, true);
  }
  assertSafe(report);
});

test("re-running the same receipt uses the same task keys and creates or charges nothing", async (t) => {
  const input = inputs();
  const { state, readRelayEvidence } = mockTransport(t, input);
  const first = await verifyLocalVideoLab({ ...input, readRelayEvidence });
  const points = state.availablePoints;
  const second = await verifyLocalVideoLab({ ...input, readRelayEvidence });
  assert.equal(first.status, "passed");
  assert.equal(second.status, "passed", JSON.stringify(second.failure));
  assert.equal(second.created_task_count, 0);
  assert.equal(second.provider_evidence.positive_provider_posts, 0);
  assert.equal(second.provider_evidence.negative_provider_posts, 0);
  assert.deepEqual(second.tasks.map((task) => task.id), first.tasks.map((task) => task.id));
  assert.equal(state.availablePoints, points);
  assert.equal(state.providerPosts, 16);
  assertSafe(second);
});

test("GET polling waits for a queued task and observes a later succeeded response", async (t) => {
  const input = inputs();
  const { state, readRelayEvidence } = mockTransport(t, input, { oneQueuedPoll: true });
  const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
  assert.equal(report.status, "passed", JSON.stringify(report.failure));
  assert.equal(state.taskGets.get(uuid(1001)), 2);
});

test("without Relay reader the report claims admission evidence, never inferred provider counts", async (t) => {
  const input = inputs();
  const { state } = mockTransport(t, input);
  const report = await verifyLocalVideoLab(input);
  assert.equal(report.status, "passed", JSON.stringify(report.failure));
  assert.deepEqual(report.provider_evidence, { observed: false });
  assert.equal(state.evidenceReads, 0);
  assert.equal(report.negative_admission_uncharged, true);
  assertSafe(report);
});

for (const [name, mutate, code] of [
  ["live mode", (input) => { input.manifest.provider_mode = "live"; }, "MOCK_LAB_REQUIRED"],
  ["ordinary service port", (input) => { input.manifest.targets.platform_base = "http://127.0.0.1:8200"; }, "ISOLATED_TARGETS_REQUIRED"],
  ["non-loopback service", (input) => { input.manifest.targets.frontend_base = "https://untrusted.invalid"; }, "ISOLATED_TARGETS_REQUIRED"],
  ["ordinary database", (input) => { input.manifest.isolation.platform_database.database = "platform"; }, "ISOLATED_DATABASE_REQUIRED"],
  ["extra manifest keys", (input) => { input.manifest.arbitrary_base_url = "https://untrusted.invalid"; }, "INVALID_MANIFEST"],
  ["future manifest time", (input) => { input.manifest.created_at_utc = new Date(Date.now() + 900_000).toISOString(); }, "INVALID_MANIFEST_TIME"],
  ["unbound receipt", (input) => { input.receipt.manifest_sha256 = "sha256:" + "0".repeat(64); }, "RECEIPT_BINDING_MISMATCH"],
  ["missing model", (input) => { input.receipt.models.pop(); }, "RECEIPT_MODEL_SET_MISMATCH"],
  ["duplicate model identity", (input) => { input.receipt.models[1].id = input.receipt.models[0].id; }, "DUPLICATE_MODEL_ID"],
  ["media injection", (input) => { input.receipt.models[0].request_payload.assets = [{ url: "https://untrusted.invalid" }]; }, "TEXT_ONLY_RECEIPT_REQUIRED"],
  ["unsupported receipt payload", (input) => { input.receipt.models[0].request_payload.resolution = "9999p"; }, "RECEIPT_PAYLOAD_OUTSIDE_CAPABILITY"],
  ["missing private credential", (input) => { input.bootstrapToken = ""; }, "MISSING_LAB_CREDENTIAL"],
]) {
  test(`binding rejects ${name} before network or task writes`, async (t) => {
    const input = inputs();
    mutate(input);
    let calls = 0;
    t.mock.method(globalThis, "fetch", async () => { calls += 1; throw new Error("Network forbidden in binding test"); });
    const report = await verifyLocalVideoLab(input);
    assert.equal(report.status, "failed");
    assert.equal(report.failure.code, code);
    assert.equal(calls, 0);
    assert.equal(report.tasks.length, 0);
    assertSafe(report);
  });
}

for (const [name, option, code, stage] of [
  ["server binding mismatch", "identityMismatch", "SERVER_IDENTITY_MISMATCH", "identity"],
  ["private endpoint redirect", "redirectIdentity", "REDIRECT_REFUSED", "identity"],
  ["insecure session cookie", "insecureCookie", "SESSION_COOKIE_FLAGS_INVALID", "login"],
  ["different session owner", "wrongSessionOwner", "REAL_COMPANY_SESSION_REQUIRED", "session"],
  ["changed discovery revision", "discoveryDrift", "DISCOVERY_RECEIPT_DRIFT", "discovery"],
  ["concurrent outstanding reservation", "existingReserved", "OTHER_TASKS_ARE_IN_FLIGHT", "accounting"],
]) {
  test(`${name} fails closed before generation`, async (t) => {
    const input = inputs();
    const { state, readRelayEvidence } = mockTransport(t, input, { [option]: true });
    const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
    assert.equal(report.status, "failed");
    assert.deepEqual({ code: report.failure.code, stage: report.failure.stage }, { code, stage });
    assert.equal(state.positivePosts, 0);
    assert.equal(state.providerPosts, 8);
    assertSafe(report);
  });
}

for (const [name, option, code] of [
  ["request payload drift", "payloadDrift", "TASK_PAYLOAD_DRIFT"],
  ["capability snapshot drift", "capabilityDrift", "TASK_CAPABILITY_DRIFT"],
  ["quote snapshot drift", "quoteDrift", "TASK_QUOTE_DRIFT"],
  ["a second task on replay", "replayCreatesAnotherId", "IDEMPOTENCY_CREATED_ANOTHER_TASK"],
  ["polling returns another task identity", "wrongPollId", "TASK_POLL_IDENTITY_DRIFT"],
  ["missing stored artifact", "missingArtifact", "STORED_ARTIFACT_MISSING"],
  ["preview storage proof failure", "previewUnavailable", "HTTP_STATUS_UNEXPECTED"],
  ["off-lab preview URL", "externalPreview", "PREVIEW_URL_OUTSIDE_LAB"],
  ["corrupt artifact bytes", "corruptBytes", "MEDIA_BYTES_DO_NOT_MATCH_STORED_ARTIFACT"],
  ["Range ignored by storage", "ignoreRange", "HTTP_STATUS_UNEXPECTED"],
  ["incorrect Content-Range", "badContentRange", "MEDIA_CONTENT_RANGE_INVALID"],
]) {
  test(`no success proof is invented for ${name}`, async (t) => {
    const input = inputs();
    const { state, readRelayEvidence } = mockTransport(t, input, { [option]: true });
    const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
    assert.equal(report.status, "failed");
    assert.equal(report.failure.code, code, JSON.stringify(report.failure));
    assert.equal(state.tasks.size, 1);
    assert.equal(state.negativePosts, 0);
    if (option === "externalPreview") assert.equal(state.mediaRequests, 0);
    if (option === "previewUnavailable") {
      assert.equal(report.failure.stage, "preview_storage_proof");
      assert.equal(report.failure.http_status, 502);
      assert.equal(report.tasks[0].artifacts[0].bound_preview_verified, false);
    }
    if (["ignoreRange", "badContentRange"].includes(option)) assert.equal(report.tasks[0].artifacts[0].range_verified, false);
    assertSafe(report);
  });
}

for (const [name, option, code] of [
  ["extra provider creation", "doubleProviderPost", "PROVIDER_POST_COUNT_MISMATCH"],
  ["changed Relay process state", "changedRelayState", "RELAY_STATE_CHANGED"],
  ["negative request provider creation", "negativeProviderPost", "NEGATIVE_REQUESTS_CREATED_PROVIDER_POSTS"],
  ["negative request wallet debit", "negativeWalletDebit", "NEGATIVE_REQUESTS_CHANGED_WALLET"],
  ["negative request task insertion", "negativeCreatesTask", "NEGATIVE_REQUESTS_CHANGED_TASK_INVENTORY"],
]) {
  test(`observed ${name} fails the relevant accounting or counter gate`, async (t) => {
    const input = inputs();
    const { readRelayEvidence } = mockTransport(t, input, { [option]: true });
    const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
    assert.equal(report.status, "failed");
    assert.equal(report.failure.code, code, JSON.stringify(report.failure));
    assert.equal(report.provider_evidence.observed, true);
    assertSafe(report);
  });
}

test("unknown submit result is not automatically retried; the next invocation recovers the exact stable key", async (t) => {
  const input = inputs();
  const { state, readRelayEvidence } = mockTransport(t, input, { unknownFirstCreate: true });
  const failed = await verifyLocalVideoLab({ ...input, readRelayEvidence });
  assert.equal(failed.status, "failed");
  assert.deepEqual(failed.failure, { code: "HTTP_RESULT_UNKNOWN", stage: "submit" });
  assert.equal(state.positivePosts, 1);
  assert.equal(state.providerPosts, 9);
  assert.equal(state.tasks.size, 1);
  assertSafe(failed);
  const recovered = await verifyLocalVideoLab({ ...input, readRelayEvidence });
  assert.equal(recovered.status, "passed", JSON.stringify(recovered.failure));
  assert.equal(recovered.created_task_count, 7);
  assert.equal(recovered.provider_evidence.positive_provider_posts, 7);
  assert.equal(state.tasks.size, 8);
  assert.equal(state.providerPosts, 16);
  assert.equal(recovered.tasks[0].id, uuid(1001));
  assertSafe(recovered);
});

test("unavailable private evidence is explicit and its exception text is never returned", async (t) => {
  const input = inputs();
  const { state, readRelayEvidence } = mockTransport(t, input, { evidenceThrows: true });
  const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
  assert.equal(report.status, "failed");
  assert.deepEqual(report.failure, { code: "RELAY_EVIDENCE_UNAVAILABLE", stage: "provider_evidence" });
  assert.equal(state.positivePosts, 0);
  assert.equal(report.provider_evidence.observed, false);
  assertSafe(report);
});

test("reference smoke uses original multipart uploads and four Ark/H3 reference tasks with signed bytes and provider reads", async (t) => {
  const input = inputs();
  const fixtures = await referenceFixtures(t);
  const { state, readRelayEvidence } = mockTransport(t, input);
  const report = await verifyLocalVideoLab({ ...input, referenceFixtures: fixtures, readRelayEvidence });
  assert.equal(report.status, "passed", JSON.stringify(report.failure));
  assert.equal(report.tasks.length, 12);
  assert.equal(report.created_task_count, 12);
  assert.equal(report.reference_coverage, "smoke");
  assert.deepEqual(report.tasks.filter((task) => task.mode !== "text_to_video").map((task) => task.slug + "/" + task.mode).sort(), [
    "minimax-h3/image_to_video", "minimax-h3/video_to_video", "seedance-2.5/image_to_video", "seedance-2.5/video_to_video",
  ]);
  assert.equal(state.assets.size, 3);
  assert.equal(state.uploadPosts, 6);
  assert.equal(report.input_assets.filter((asset) => asset.purpose === "reference").length, 2);
  assert.ok(report.input_assets.every((asset) => asset.idempotency_replayed));
  assert.ok(report.input_assets.filter((asset) => asset.purpose === "reference").every((asset) => asset.status === "active" && asset.signed_bytes_verified));
  assert.equal(report.input_assets.find((asset) => asset.purpose === "disabled_admission_probe").status, "disabled");
  assert.equal(report.input_checks.length, 5);
  assert.ok(report.input_checks.every((check) => check.status === "rejected" && check.http_status === 404));
  assert.equal(report.negative_checks.length, 16);
  assert.equal(state.negativePosts, 17);
  assert.equal(report.provider_evidence.positive_provider_posts, 12);
  assert.equal(report.provider_evidence.positive_input_fetches, 4);
  assert.equal(report.provider_evidence.positive_input_bytes, state.inputBytes);
  assert.equal(report.provider_evidence.negative_provider_posts, 0);
  assert.equal(report.provider_evidence.negative_input_fetches, 0);
  assertSafe(report);
  assert.ok(!JSON.stringify(report).includes(fixtures.imagePath));
  assert.ok(!JSON.stringify(report).includes("signature="));
});

test("reference coverage expands smoke twelve to all twenty and then replays without new tasks, assets, reads or charges", async (t) => {
  const input = inputs();
  const fixtures = await referenceFixtures(t);
  const { state, readRelayEvidence } = mockTransport(t, input);
  const smoke = await verifyLocalVideoLab({ ...input, referenceFixtures: fixtures, readRelayEvidence });
  assert.equal(smoke.status, "passed", JSON.stringify(smoke.failure));
  const all = await verifyLocalVideoLab({ ...input, referenceFixtures: fixtures, referenceCoverage: "all-modes", readRelayEvidence });
  assert.equal(all.status, "passed", JSON.stringify(all.failure));
  assert.equal(all.tasks.length, 20);
  assert.equal(all.created_task_count, 8);
  assert.equal(all.provider_evidence.positive_provider_posts, 8);
  assert.equal(all.provider_evidence.positive_input_fetches, 8);
  assert.equal(state.tasks.size, 20);
  assert.equal(state.assets.size, 3);
  assert.equal(state.inputFetches, 12);
  assert.equal(all.tasks.filter((task) => task.slug === "minimax-h3-max").length, 1);
  for (const prior of smoke.tasks) assert.equal(all.tasks.find((task) => task.slug === prior.slug && task.mode === prior.mode)?.id, prior.id);
  const points = state.availablePoints;
  const replay = await verifyLocalVideoLab({ ...input, referenceFixtures: fixtures, referenceCoverage: "all-modes", readRelayEvidence });
  assert.equal(replay.status, "passed", JSON.stringify(replay.failure));
  assert.equal(replay.created_task_count, 0);
  assert.equal(replay.provider_evidence.positive_provider_posts, 0);
  assert.equal(replay.provider_evidence.positive_input_fetches, 0);
  assert.equal(replay.provider_evidence.positive_input_bytes, 0);
  assert.equal(state.availablePoints, points);
  assert.equal(state.tasks.size, 20);
  assert.equal(state.assets.size, 3);
  assertSafe(replay);
});

test("adding references after the original eight T2V tasks preserves their IDs and only creates four tasks", async (t) => {
  const input = inputs();
  const fixtures = await referenceFixtures(t);
  const { state, readRelayEvidence } = mockTransport(t, input);
  const t2v = await verifyLocalVideoLab({ ...input, readRelayEvidence });
  const smoke = await verifyLocalVideoLab({ ...input, referenceFixtures: fixtures, readRelayEvidence });
  assert.equal(smoke.status, "passed", JSON.stringify(smoke.failure));
  assert.equal(smoke.created_task_count, 4);
  assert.equal(smoke.provider_evidence.positive_provider_posts, 4);
  assert.equal(smoke.provider_evidence.positive_input_fetches, 4);
  assert.deepEqual(smoke.tasks.slice(0, 8).map((task) => task.id), t2v.tasks.map((task) => task.id));
  assert.equal(state.tasks.size, 12);
});

for (const [name, setup, code] of [
  ["undersized image", { imageWidth: 128 }, "REFERENCE_DIMENSIONS_UNSUPPORTED"],
  ["short source video", { duration: 1 }, "REFERENCE_VIDEO_DURATION_UNSUPPORTED"],
  ["unsupported source frame rate", { frameRate: 12 }, "REFERENCE_VIDEO_FRAME_RATE_UNSUPPORTED"],
]) {
  test(`${name} is rejected from file metadata before any HTTP request`, async (t) => {
    const input = inputs();
    const fixtures = await referenceFixtures(t, setup);
    const { state, readRelayEvidence } = mockTransport(t, input);
    const report = await verifyLocalVideoLab({ ...input, referenceFixtures: fixtures, readRelayEvidence });
    assert.equal(report.status, "failed");
    assert.equal(report.failure.code, code, JSON.stringify(report.failure));
    assert.equal(state.requests.length, 0);
    assertSafe(report);
  });
}

for (const [name, option, code] of [
  ["missing reference readiness", "referenceNotReady", "REFERENCE_MODE_NOT_READY"],
  ["missing actual provider-read counter", "missingInputCounter", "REFERENCE_FETCH_COUNTER_MISSING"],
  ["upload hash mismatch", "uploadHashMismatch", "INPUT_UPLOAD_PROOF_MISMATCH"],
  ["upload replay creates another identity", "uploadReplayMismatch", "INPUT_UPLOAD_IDEMPOTENCY_FAILED"],
  ["off-lab input preview", "externalInputPreview", "INPUT_PREVIEW_URL_OUTSIDE_LAB"],
  ["input byte corruption", "corruptInputBytes", "INPUT_BYTES_MISMATCH"],
  ["provider failed to fetch reference bytes", "noProviderInputFetch", "REFERENCE_FETCH_COUNT_MISMATCH"],
  ["tampered input signature accepted", "ignoreInputSignatures", "HTTP_STATUS_UNEXPECTED"],
  ["disabled input creates an upstream request", "disabledInputProviderPost", "NEGATIVE_REQUESTS_CREATED_PROVIDER_POSTS"],
]) {
  test(`reference verification fails closed on ${name}`, async (t) => {
    const input = inputs();
    const fixtures = await referenceFixtures(t);
    const { state, readRelayEvidence } = mockTransport(t, input, { [option]: true });
    const report = await verifyLocalVideoLab({ ...input, referenceFixtures: fixtures, readRelayEvidence });
    assert.equal(report.status, "failed");
    assert.equal(report.failure.code, code, JSON.stringify(report.failure));
    if (["referenceNotReady", "missingInputCounter", "uploadHashMismatch", "uploadReplayMismatch", "externalInputPreview", "corruptInputBytes"].includes(option)) assert.equal(state.positivePosts, 0);
    assertSafe(report);
  });
}

for (const field of ["call_quota", "test_points"]) {
  test(`reference plan refuses an insufficient ${field} before submitting any tasks`, async (t) => {
    const input = inputs();
    input.manifest.budget[field] = field === "call_quota" ? 11 : 1;
    input.receipt.manifest_sha256 = "sha256:" + digest(canonical(input.manifest));
    const fixtures = await referenceFixtures(t);
    const { state, readRelayEvidence } = mockTransport(t, input);
    const report = await verifyLocalVideoLab({ ...input, referenceFixtures: fixtures, readRelayEvidence });
    assert.equal(report.status, "failed");
    assert.equal(report.failure.code, field === "call_quota" ? "REFERENCE_TASK_BUDGET_EXCEEDED" : "REFERENCE_POINT_BUDGET_EXCEEDED");
    assert.equal(state.positivePosts, 0);
    assert.equal(state.providerPosts, 8);
    assertSafe(report);
  });
}

test("reference fixture paths outside the workspace are refused before filesystem content or HTTP access", async (t) => {
  const input = inputs();
  const fixtures = await referenceFixtures(t);
  fixtures.imagePath = resolve(WORKSPACE_TEST_ROOT(), "../unrelated-reference.png");
  const { state } = mockTransport(t, input);
  const report = await verifyLocalVideoLab({ ...input, referenceFixtures: fixtures });
  assert.equal(report.status, "failed");
  assert.equal(report.failure.code, "REFERENCE_PATH_REFUSED");
  assert.equal(state.requests.length, 0);
});

function WORKSPACE_TEST_ROOT() { return fileURLToPath(new URL("../", import.meta.url)); }

test("all-modes never silently runs text-only when fixture paths are omitted", async (t) => {
  const input = inputs();
  const { state } = mockTransport(t, input);
  const report = await verifyLocalVideoLab({ ...input, referenceCoverage: "all-modes" });
  assert.equal(report.status, "failed");
  assert.equal(report.failure.code, "REFERENCE_FIXTURES_REQUIRED");
  assert.equal(state.requests.length, 0);
});

const nativeBillingFields = ["user_quota", "user_used_quota", "user_request_count", "token_remain_quota",
  "token_used_quota", "token_unlimited_quota", "native_consume_logs", "native_refund_logs"];

for (const phase of ["positive", "negative"]) {
  for (const field of nativeBillingFields) {
    test(`native billing ${field} change during ${phase} is not hidden by otherwise successful generation`, async (t) => {
      const input = inputs();
      const { state, readRelayEvidence } = mockTransport(t, input, { nativeChangeField: field, nativeChangePhase: phase });
      const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
      assert.equal(report.status, "failed");
      assert.deepEqual(report.failure, { code: `NATIVE_BILLING_CHANGED_DURING_${phase.toUpperCase()}`, stage: "native_billing" });
      assert.equal(state.tasks.size, 8);
      assert.equal(report.provider_evidence[`native_billing_unchanged_after_${phase}`], undefined);
      assertSafe(report);
    });
  }
}

for (const field of nativeBillingFields) {
  test(`missing native billing ${field} prevents the first task submission`, async (t) => {
    const input = inputs();
    const { state, readRelayEvidence } = mockTransport(t, input, { missingNativeField: field });
    const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
    assert.equal(report.status, "failed");
    assert.equal(report.failure.code, "NATIVE_BILLING_EVIDENCE_INCOMPLETE");
    assert.equal(state.tasks.size, 0);
    assert.equal(report.provider_evidence.observed, false);
    assertSafe(report);
  });
}

for (const [option, value, code] of [
  ["missingNativeBilling", true, "NATIVE_BILLING_EVIDENCE_INCOMPLETE"],
  ["invalidNativeField", "user_quota", "NATIVE_BILLING_EVIDENCE_INVALID"],
  ["invalidNativeField", "token_unlimited_quota", "NATIVE_BILLING_EVIDENCE_INVALID"],
  ["extraNativeField", true, "NATIVE_BILLING_EVIDENCE_INCOMPLETE"],
]) {
  test(`native evidence ${option}/${value} is rejected without exposing unrecognized fields`, async (t) => {
    const input = inputs();
    const { state, readRelayEvidence } = mockTransport(t, input, { [option]: value });
    const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
    assert.equal(report.status, "failed");
    assert.equal(report.failure.code, code);
    assert.equal(state.tasks.size, 0);
    assertSafe(report);
  });
}

test("native raw quota remains unconverted while provider cost events may legitimately increase", async (t) => {
  const input = inputs();
  const { readRelayEvidence } = mockTransport(t, input, { initialNativeQuota: -17 });
  const report = await verifyLocalVideoLab({ ...input, readRelayEvidence });
  assert.equal(report.status, "passed", JSON.stringify(report.failure));
  assert.equal(report.provider_evidence.before.native_billing.user_quota, -17);
  assert.equal(report.provider_evidence.after_negative.native_billing.user_quota, -17);
  assert.equal(report.provider_evidence.before.provider_cost_events, 8);
  assert.equal(report.provider_evidence.after_positive.provider_cost_events, 16);
  assert.equal(report.provider_evidence.native_billing_unchanged_after_positive, true);
  assert.equal(report.provider_evidence.native_billing_unchanged_after_negative, true);
  assert.equal("identity_bound" in report.provider_evidence.before.native_billing, false);
  assert.equal("unknown_provider_rows" in report.provider_evidence.before, false);
  assert.equal("unknownProviderRows" in report.provider_evidence.before, false);
  assertSafe(report);
});
