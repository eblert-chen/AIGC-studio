import assert from "node:assert/strict";
import test from "node:test";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { LAB_TARGETS, makeLabManifest, validateLocalManifest, platformLabEnvironment, makeLabCompose, labRelayArguments, validatePaidApproval, parseLabArguments, selectVerificationRequests, validateLiveInputStorage, bindInitialPaidApproval, routeProbeDisposition, renewedProbe, labFrontendDirectory, labFrontendBuildEnvironment } from "../scripts/local-video-lab.mjs";

const root = fileURLToPath(new URL("../", import.meta.url));
const manifest = makeLabManifest("mock", "0123456789ab", new Date("2026-08-31T00:00:00Z"));
const privateConfig = Object.fromEntries(["API_KEY", "CLIENT_ID", "TENANT_ID", "INTERNAL_ADMISSION_TOKEN", "CALLBACK_SIGNING_SECRET", "OPERATIONS_TOKEN", "RECONCILIATION_KEY_ID", "RECONCILIATION_SECRET"].map((key) => ["PLATFORM_LAB_RELAY_" + key, "test-value-for-" + key]));
const secrets = { database_password: "a".repeat(64), bootstrap_token: "b".repeat(64), internal_service_token: "c".repeat(64), input_signing_secret: "d".repeat(64) };
const inputStorage = { schema_version: 1, kind: "local-video-lab-private-input-obs", endpoint: "https://obs.cn-north-4.myhuaweicloud.com", bucket: "isolated-input-test-bucket", access_key_id: "TESTACCESSKEY12345678", secret_access_key: "test-only-not-real-credential-".repeat(2) };
function objectConfig(mode) {
  return { PLATFORM_LAB_RELAY_STATE_ID: mode + "-reviewed-state", PLATFORM_LAB_OBJECT_STORE_STATE_ID: mode + "-reviewed-state",
    PLATFORM_LAB_OBJECT_STORE_MODE: mode, PLATFORM_LAB_OBJECT_STORE_KIND: "local-persistent-object-store-not-production-obs",
    PLATFORM_LAB_OBJECT_STORE_ENDPOINT_HOST: "lab-objects.local.test", PLATFORM_LAB_OBJECT_STORE_BUCKET: "video-" + mode + "-" + "a".repeat(20),
    PLATFORM_LAB_OBJECT_STORE_ACCESS_KEY_ID: "LAB" + "B".repeat(20), PLATFORM_LAB_OBJECT_STORE_SECRET_ACCESS_KEY: "c".repeat(64) };
}

test("local video manifests bind different mock/live databases and identities without authorizing paid operations", () => {
  const live = makeLabManifest("live", "0123456789ab");
  assert.notEqual(live.lab_id, manifest.lab_id);
  assert.notEqual(live.isolation.compose_project, manifest.isolation.compose_project);
  assert.notEqual(live.isolation.platform_database.database, manifest.isolation.platform_database.database);
  assert.equal(live.paid_probe_approval, null);
  assert.equal(manifest.budget.unit_price_points_per_second, 1);
  assert.equal(validateLocalManifest(structuredClone(manifest)).provider_mode, "mock");
  assert.throws(() => makeLabManifest("production", "0123456789ab"));
  assert.throws(() => makeLabManifest("mock", "../../production"));
});

test("ordinary local ports, databases, callback origins and identity mismatches are rejected", () => {
  for (const mutation of [
    (m) => { m.targets.platform_base = "http://127.0.0.1:8200"; },
    (m) => { m.targets.relay_base = "http://localhost:18430"; },
    (m) => { m.targets.injected = "http://example.com"; },
    (m) => { m.isolation.platform_database.host = "platform-db"; },
    (m) => { m.isolation.platform_database.database = "platform"; },
    (m) => { m.isolation.relay_service_base = "http://relay:3000"; },
    (m) => { m.isolation.compose_project = "ai-video"; },
    (m) => { m.provider_mode = "live"; },
    (m) => { m.instance_nonce = "not-a-nonce"; },
  ]) {
    const candidate = structuredClone(manifest); mutation(candidate);
    assert.throws(() => validateLocalManifest(candidate));
  }
});

test("Platform runtime binds every Relay credential/backend and isolates local bootstrap from Auth0", () => {
  const env = platformLabEnvironment(manifest, privateConfig, secrets);
  const backends = JSON.parse(env.RELAY_BACKENDS);
  assert.equal(env.RELAY_BASE_URL, undefined);
  assert.equal(env.RELAY_CLIENT_ID, undefined);
  assert.equal(env.RELAY_API_KEY, undefined);
  assert.equal(backends["new-api-v1"].base_url, "http://relay-lab:3000");
  assert.equal(backends["new-api-v1"].api_key, privateConfig.PLATFORM_LAB_RELAY_API_KEY);
  assert.equal(backends["new-api-v1"].client_id, privateConfig.PLATFORM_LAB_RELAY_CLIENT_ID);
  assert.equal(env.RELAY_CALLBACK_PUBLIC_URL, "http://platform-lab:8000/internal/relay-callbacks");
  assert.equal(env.LOCAL_VIDEO_LAB_BROWSER_ORIGIN, LAB_TARGETS.frontend_base);
  assert.equal(env.OIDC_ENABLED, "false");
  assert.equal(env.FRONTEND_ORIGIN, undefined);
  assert.equal(env.ENVIRONMENT, "development");
  assert.equal(env.PUBLISHING_WORKER_ENABLED, "false");
  assert.equal(env.INPUT_ASSET_RELAY_BASE_URL, "https://lab-objects.local.test");
  assert.match(env.DATABASE_URL, /@platform-db-lab:5432\/video_lab_mock_0123456789ab$/);
  assert.throws(() => platformLabEnvironment(manifest, {}, secrets));
});

test("generated Compose is isolated, loopback-only, and marks the database before workers start", () => {
  const env = platformLabEnvironment(manifest, privateConfig, secrets);
  const compose = makeLabCompose(manifest, resolve(root, ".tmp/local-video-lab", manifest.lab_id), env, { objectConfig: objectConfig("mock") });
  assert.equal(compose.name, manifest.isolation.compose_project);
  assert.equal(compose.networks.lab.internal, true);
  assert.equal(compose.services["platform-lab"].depends_on["platform-db-lab"].condition, "service_healthy");
  for (const [name, service] of Object.entries(compose.services)) {
    assert.equal(service.privileged, undefined);
    assert.equal(service.network_mode, undefined);
    for (const port of service.ports || []) assert.match(port, /^127\.0\.0\.1:184\d\d:/);
    for (const volume of service.volumes || []) {
      if (typeof volume === "object") {
        assert.notEqual(resolve(volume.source), resolve(root));
        assert.ok(!String(volume.source).endsWith(".env"));
      }
    }
    if (/dispatcher|relay-sync|catalog-sync|timeout/.test(name)) assert.equal(service.depends_on["platform-lab"].condition, "service_healthy");
  }
  assert.throws(() => makeLabCompose(manifest, root, env));
});

test("mock CLI has no key files/paid flag and live has no fixture or mock network", () => {
  const mockArgs = labRelayArguments(manifest);
  assert.ok(mockArgs.includes("--fixture-manifest"));
  assert.ok(!mockArgs.includes("--ark-key-file"));
  assert.ok(!mockArgs.includes("--allow-paid-probe"));
  const live = makeLabManifest("live", "0123456789ab");
  const args = labRelayArguments(live);
  assert.ok(!args.includes("--fixture-manifest"));
  assert.ok(!args.includes("--allow-paid-probe"));
  assert.ok(labRelayArguments(live, { paidApproval: true }).includes("--paid-probe-approval"));
  const compose = makeLabCompose(live, resolve(root, ".tmp/local-video-lab", live.lab_id), platformLabEnvironment(live, privateConfig, secrets, inputStorage), { objectConfig: objectConfig("live") });
  assert.equal(compose.networks.lab.internal, false);
  assert.ok(compose.services["relay-lab"].extra_hosts.every((host) => host.includes("lab-objects.local.test:11.254.94.40")));
});

test("live input media uses new private OBS configuration in every Platform worker and never falls back to an internal HTTP URL", () => {
  const live = makeLabManifest("live", "0123456789ab");
  assert.throws(() => platformLabEnvironment(live, privateConfig, secrets), /fresh private input-storage/);
  const env = platformLabEnvironment(live, privateConfig, secrets, inputStorage);
  assert.equal(env.INPUT_ASSET_STORE, "huawei_obs");
  assert.equal(env.HUAWEI_OBS_BUCKET, inputStorage.bucket);
  assert.equal(env.HUAWEI_OBS_SECRET_ACCESS_KEY, inputStorage.secret_access_key);
  const compose = makeLabCompose(live, resolve(root, ".tmp/local-video-lab", live.lab_id), env, { objectConfig: objectConfig("live") });
  for (const name of ["platform-lab", "platform-dispatcher-lab", "platform-relay-sync-lab", "platform-catalog-sync-lab", "platform-timeout-lab"]) {
    assert.equal(compose.services[name].environment.INPUT_ASSET_STORE, "huawei_obs");
    assert.equal(compose.services[name].environment.HUAWEI_OBS_SECRET_ACCESS_KEY, inputStorage.secret_access_key);
  }
  for (const mutation of [
    (value) => { value.endpoint = "http://obs.cn-north-4.myhuaweicloud.com"; },
    (value) => { value.endpoint = "https://obs.cn-north-4.myhuaweicloud.com.attacker.test"; },
    (value) => { value.endpoint = "https://user:secret@obs.cn-north-4.myhuaweicloud.com"; },
    (value) => { value.endpoint += "/unexpected-path"; },
    (value) => { value.secret_access_key = ""; },
    (value) => { value.runtime_secret_file = "deploy/secrets/retired-source.env"; },
  ]) {
    const invalid = structuredClone(inputStorage); mutation(invalid); assert.throws(() => validateLiveInputStorage(invalid));
  }
});

test("renewing paid approval does not change the immutable Platform database identity", () => {
  const live = makeLabManifest("live", "0123456789ab");
  const initial = { actor: "test-operator", reason: "first batch", operation_id: "batch-one", approved_at_utc: "2026-08-31T00:00:00Z" };
  const bound = bindInitialPaidApproval(live, initial);
  assert.equal(live.paid_probe_approval, null);
  assert.deepEqual(bound.paid_probe_approval, initial);
  const renewed = bindInitialPaidApproval(bound, { ...initial, operation_id: "batch-two", reason: "fresh probe evidence" });
  assert.deepEqual(renewed, bound);
  assert.throws(() => bindInitialPaidApproval(manifest, initial));
});

test("probe readiness reads the outer artifact state, preserves real completion time and cannot replay unknown outcomes", () => {
  const now = new Date("2026-08-31T12:00:00Z");
  const ready = { state: "succeeded", result: { success: true }, provider_submission_state: "artifact_verified", completed_at: "2026-08-31T11:00:00Z" };
  assert.equal(routeProbeDisposition(ready, now), "ready");
  assert.equal(routeProbeDisposition({ ...ready, completed_at: "2026-08-30T12:00:00Z" }, now), "stale");
  assert.equal(routeProbeDisposition({ state: "running" }, now), "pending");
  for (const candidate of [
    { ...ready, provider_submission_state: undefined },
    { ...ready, result: { success: true, provider_submission_state: "artifact_verified" }, provider_submission_state: undefined },
    { ...ready, completed_at: undefined }, { ...ready, completed_at: "2026-09-01T00:00:00Z" },
    { state: "failed" }, { state: "reconciliation_unknown" },
  ]) assert.throws(() => routeProbeDisposition(candidate, now), (error) => error.terminal === true);
});

test("fresh evidence gets separate bounded IDs and a fresh live approval, never a reused paid batch", () => {
  const probe = { request_id: "original", body: { operation_id: "old-op", public_model_id: "minimax-h3" } };
  const first = renewedProbe(probe, { mode: "mock", attempt: 1 });
  assert.ok(first.request_id.length <= 80);
  assert.equal(first.request_id, first.body.operation_id);
  assert.notEqual(first.request_id, renewedProbe(probe, { mode: "mock", attempt: 2 }).request_id);
  assert.deepEqual(first, renewedProbe(probe, { mode: "mock", attempt: 1 }));
  assert.throws(() => renewedProbe(probe, { mode: "live", attempt: 1 }));
  assert.throws(() => renewedProbe(probe, { mode: "live", approval: { operation_id: "old-batch" }, previousRecord: { approval_operation_id: "old-batch" }, attempt: 1 }));
  const live = renewedProbe(probe, { mode: "live", approval: { operation_id: "new-batch" }, previousRecord: { approval_operation_id: "old-batch" }, attempt: 1 });
  assert.notEqual(live.request_id, first.request_id);
  assert.equal(live.body.public_model_id, probe.body.public_model_id);
});

test("lab frontend output is isolated from ordinary dist and from the other provider mode", () => {
  const mockDirectory = labFrontendDirectory(manifest);
  assert.equal(mockDirectory, resolve(root, ".tmp/local-video-lab", manifest.lab_id, "frontend"));
  assert.notEqual(mockDirectory, resolve(root, "dist/client"));
  assert.notEqual(mockDirectory, labFrontendDirectory(makeLabManifest("live", "0123456789ab")));
  assert.throws(() => labFrontendDirectory({ ...manifest, lab_id: "../../dist" }));
});

test("only the explicit local development build permits loopback media and it cannot inherit demo mode", () => {
  const original = { NODE_ENV: "production", VITE_ENABLE_DEMO: "true", VITE_PLATFORM_API_URL: "https://ordinary-api.example", KEEP_VALUE: "unchanged" };
  const env = labFrontendBuildEnvironment(original);
  assert.equal(env.NODE_ENV, "development");
  assert.equal(env.VITE_ENABLE_DEMO, "false");
  assert.equal(env.VITE_PLATFORM_API_URL, LAB_TARGETS.frontend_base);
  assert.equal(env.KEEP_VALUE, "unchanged");
  assert.equal(original.NODE_ENV, "production");
  assert.equal(original.VITE_ENABLE_DEMO, "true");
});

test("live approval cannot reuse mock evidence, stale state, wider models or an unbounded budget", () => {
  const now = new Date("2026-08-31T01:00:00Z");
  const summary = { mode: "live", state_id: "live-state-bound-to-real-key", models: [{ provider_model_id: "MiniMax-H3" }, { provider_model_id: "doubao-seedance-2-5-260628" }] };
  const approval = { schema_version: 1, state_id: summary.state_id, operation_id: "operator-reviewed-operation-1",
    provider_model_ids: summary.models.map((m) => m.provider_model_id), max_provider_creates: 4, expires_at: "2026-08-31T02:00:00Z",
    approved_at_utc: "2026-08-31T00:59:00Z", actor: "local-operator", reason: "Two probes and two explicitly approved creation checks" };
  assert.equal(validatePaidApproval(approval, summary, now), approval);
  for (const mutation of [
    (a) => { a.state_id = "mock-state"; }, (a) => { a.max_provider_creates = 101; }, (a) => { a.max_provider_creates = null; },
    (a) => { a.expires_at = "2026-08-31T00:00:00Z"; }, (a) => { a.expires_at = "2026-09-02T00:00:00Z"; },
    (a) => { a.actor = ""; }, (a) => { a.provider_model_ids.push("unreviewed-provider-model"); },
    (a) => { a.approved_at_utc = "2026-09-01T00:00:00Z"; },
  ]) {
    const candidate = structuredClone(approval); mutation(candidate);
    assert.throws(() => validatePaidApproval(candidate, summary, now));
  }
  assert.throws(() => validatePaidApproval(approval, { ...summary, mode: "mock" }, now));
});

test("argument parsing rejects implicit live authorization and unknown flags", () => {
  assert.equal(parseLabArguments(["status"]).mode, "mock");
  assert.equal(parseLabArguments(["prepare-live"]).mode, "live");
  assert.throws(() => parseLabArguments(["start", "--mode", "production"]));
  assert.throws(() => parseLabArguments(["start", "--key", "do-not-paste-keys"]));
  assert.throws(() => parseLabArguments(["verify", "--paid-probe-approval", "approval.json"]));
  assert.throws(() => parseLabArguments(["prepare-live", "--mode", "mock"]));
});

test("verification payload selection only uses advertised mode limits and keeps capability/quote revisions", () => {
  const model = { id: "actual-platform-model-id", slug: "minimax-h3", expected_capability_version: 7, expected_quote_revision: "sha256:" + "a".repeat(64),
    request_payload: { mode: "text_to_video", prompt: "actual request" },
    effective_capabilities: { modes: { text_to_video: { limits: { resolutions: ["2k", "768p"], duration_seconds: [4, 5, 6], aspect_ratios: ["1:1", "16:9"] } } } } };
  const result = selectVerificationRequests({ models: [model] }).models[0];
  assert.deepEqual(result.request_payload, { mode: "text_to_video", prompt: "actual request", resolution: "768p", duration_seconds: 4, aspect_ratio: "16:9" });
  assert.equal(result.expected_quote_revision, model.expected_quote_revision);
  assert.equal(result.expected_capability_version, 7);
  assert.deepEqual(result.effective_capabilities, model.effective_capabilities);
  const incompatible = structuredClone(model);
  incompatible.effective_capabilities.modes.text_to_video.limits.resolutions = ["1080p"];
  assert.throws(() => selectVerificationRequests({ models: [incompatible] }));
});
