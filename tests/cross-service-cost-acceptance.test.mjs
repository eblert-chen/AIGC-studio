import assert from "node:assert/strict";
import { readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";

import {
  buildEvidenceEnvelope,
  evaluatePlatformProbeBinding,
  formatAcceptanceFailure,
  parseCanonicalPythonExecutable,
  platformSourceSnapshot,
  platformSnapshotDirectoryIgnored,
  parseCompiledBuildIdentity,
  redactAcceptanceDiagnostic,
  resolveCanonicalPythonExecutable,
  sensitiveEnvironmentValues,
  validateEvidenceEnvelope,
  validateRunnerOwnedVolumeBinding,
  validateRuntimeIdentity,
  writeCreateOnlyEvidence,
} from "../scripts/run-cross-service-cost-acceptance.mjs";
import {
  expectedCandidateImageLabels,
  relaySourceSnapshot,
} from "../scripts/relay-fault-source-snapshot.mjs";
import { CANDIDATE_UPSTREAM_GIT_REVISION } from "../scripts/relay-migration-acceptance.mjs";

test("failed pytest diagnostics are bounded and redact exact and structured credentials", () => {
  const exactSecret = "runtime-secret-value-123456789";
  const databaseUrl = "postgresql://runner:database-password@127.0.0.1:5432/acceptance";
  const diagnostic = redactAcceptanceDiagnostic(
    `\u001b[31mFAILED\u001b[0m test_cost_contract\n${databaseUrl}\n${"context-line\n".repeat(80)}tail assertion`,
    `Authorization: Bearer bearer-token-value\nVENDOR_API_KEY=visible-api-key\npostgresql://other:other-password@example.invalid/db\nprotected=${exactSecret}`,
    [exactSecret, databaseUrl],
    320,
  );

  assert.match(diagnostic, /^\[output truncated; final 320 characters follow\]/);
  assert.match(diagnostic, /tail assertion/);
  assert.match(diagnostic, /\[REDACTED\]/);
  assert.doesNotMatch(diagnostic, /runtime-secret-value|database-password|visible-api-key|bearer-token-value/);
  assert.doesNotMatch(diagnostic, /\u001b\[/);
  assert.doesNotMatch(diagnostic, /:\/\/[^/@\s:]+:[^/@\s]+@/);
});

test("ambient sensitive environment values join the pytest redaction set", () => {
  assert.deepEqual(
    sensitiveEnvironmentValues({
      PATH: "not-sensitive-for-this-filter",
      DATABASE_URL: "postgresql://user:pass@example.invalid/db",
      VENDOR_API_KEY: "vendor-key-value",
      SESSION_SECRET: "session-secret-value",
      SHORT_TOKEN: "tiny",
    }).sort(),
    [
      "postgresql://user:pass@example.invalid/db",
      "session-secret-value",
      "vendor-key-value",
    ].sort(),
  );
});

test("pytest diagnostic is printable only after the runner marks cleanup verified", () => {
  const failure = new Error("cross-service channel-cost integration test failed");
  Object.defineProperty(failure, "redactedAcceptanceDiagnostic", {
    value: "FAILED test_cost_contract\nassert 503 == 201",
  });
  assert.doesNotMatch(formatAcceptanceFailure(failure), /assert 503/);

  Object.defineProperty(failure, "redactedDiagnosticCleanupVerified", { value: true });
  const report = formatAcceptanceFailure(failure);
  assert.match(report, /runner cleanup verified/);
  assert.match(report, /FAILED test_cost_contract/);
  assert.match(report, /assert 503 == 201/);
});

test("runner releases a captured pytest diagnostic only after exact cleanup verification", async () => {
  const source = await readFile(
    new URL("../scripts/run-cross-service-cost-acceptance.mjs", import.meta.url),
    "utf8",
  );
  const pytestCapture = source.indexOf("diagnosticForbiddenValues:");
  const cleanupVerification = source.lastIndexOf("await assertResourcesRemoved(containers, volumes, network)");
  const diagnosticRelease = source.indexOf(
    "Object.defineProperty(acceptanceError, diagnosticCleanupProperty",
  );
  assert(pytestCapture > 0);
  assert(cleanupVerification > pytestCapture);
  assert(diagnosticRelease > cleanupVerification);
});

test("Platform startup diagnostics are bounded, redacted, and cleanup-gated", async () => {
  const source = await readFile(
    new URL("../scripts/run-cross-service-cost-acceptance.mjs", import.meta.url),
    "utf8",
  );
  assert.match(
    source,
    /waitForPlatformProbe[\s\S]*?redactAcceptanceDiagnostic\([\s\S]*?readPlatformOutput\(\)[\s\S]*?sensitiveEnvironmentValues\(platformEnv\)[\s\S]*?redactedDiagnosticProperty/,
  );
  assert.match(source, /diagnosticCleanupProperty[\s\S]*?cleanupVerified/);
  assert.match(
    source,
    /waitForPlatformProbe\(url, nonce, pid, child, timeoutMs = 120_000\)/,
  );
  assert.match(
    source,
    /nonce_bound: safeEqual\(nonceHeader, nonce\)/,
  );
  assert.match(source, /pid_bound: safeEqual\(pidHeader, String\(pid\)\)/);
  assert.match(source, /fetch_ok=\$\{observation\.fetch_ok\}/);
  assert.match(source, /pid_bound=\$\{observation\.pid_bound\}/);
  assert.match(source, /platformProcess\.stdout\?\.resume\(\)/);
  assert.match(source, /platformProcess\.stderr\?\.resume\(\)/);
  assert.match(source, /const bootstrapValue = randomBytes\(32\)\.toString\("base64url"\)/);
  assert.match(source, /ENABLE_BOOTSTRAP:\s*"true",\s*BOOTSTRAP_TOKEN:\s*bootstrapValue/);
  assert.match(
    source,
    /const platformEnv = \{\s*\.\.\.process\.env,[\s\S]*?ENVIRONMENT:\s*"development",\s*DEVELOPMENT_HEADER_AUTH_ENABLED:\s*"true",[\s\S]*?BOOTSTRAP_TOKEN:\s*bootstrapValue/,
  );
  assert.equal(source.match(/DEVELOPMENT_HEADER_AUTH_ENABLED/g)?.length, 1);
  assert.match(
    source,
    /"tests\/integration\/test_new_api_channel_cost_delivery\.py",[\s\S]*?env:\s*\{\s*\.\.\.platformEnv,/,
  );
  assert.match(source, /forbiddenValues = \[[\s\S]*?bootstrapValue/);
});

test("cost acceptance probe explicitly authenticates every Platform bootstrap", async () => {
  const source = await readFile(
    new URL(
      "../backend/platform/tests/integration/test_new_api_channel_cost_delivery.py",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(source, /BOOTSTRAP_TOKEN = os\.getenv\("BOOTSTRAP_TOKEN", ""\)/);
  assert.match(source, /\("BOOTSTRAP_TOKEN", BOOTSTRAP_TOKEN\)/);
  const bootstrapHeaders = source.match(
    /headers=\{"X-Bootstrap-Token": BOOTSTRAP_TOKEN\}/g,
  );
  assert.equal(bootstrapHeaders?.length, 2);
});

test("canonical Python executable parsing is strict and platform-aware", () => {
  const windowsExecutable = "C:\\Python 3.14\\python.exe";
  assert.equal(
    parseCanonicalPythonExecutable(`${windowsExecutable}\r\n`, "win32"),
    windowsExecutable,
  );
  assert.equal(
    parseCanonicalPythonExecutable("/opt/python/bin/python3\n", "linux"),
    "/opt/python/bin/python3",
  );
  for (const invalid of [
    "",
    "python.exe\n",
    `${windowsExecutable}\r\nextra\r\n`,
    `${windowsExecutable}\r\n\r\n`,
    `${windowsExecutable}\0\r\n`,
  ]) {
    assert.throws(
      () => parseCanonicalPythonExecutable(invalid, "win32"),
      /Python executable resolution/,
    );
  }
});

test("canonical Python resolution replaces a Windows alias before any runner resource", async () => {
  const canonical = "C:\\Users\\runner\\Python\\pythoncore-3.14-64\\python.exe";
  const calls = [];
  const resolved = await resolveCanonicalPythonExecutable("python", {
    platform: "win32",
    run: async (command, args, options) => {
      calls.push({ command, args, options });
      return { stdout: `${canonical}\r\n`, stderr: "" };
    },
    inspect: async (path) => {
      assert.equal(path, canonical);
      return { isFile: () => true };
    },
  });
  assert.equal(resolved, canonical);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, "python");
  assert.deepEqual(calls[0].args.slice(0, 3), ["-I", "-S", "-c"]);
  assert.match(calls[0].args[3], /sys\.platform == 'win32'/);
  assert.match(calls[0].args[3], /os\.path\.realpath\(sys\.executable\)/);
  assert.match(calls[0].args[3], /os\.path\.abspath\(sys\.executable\)/);
  assert.equal(calls[0].options.failure, "Python executable could not be resolved");

  const source = await readFile(
    new URL("../scripts/run-cross-service-cost-acceptance.mjs", import.meta.url),
    "utf8",
  );
  const resolution = source.indexOf(
    "const pythonExecutable = await resolveCanonicalPythonExecutable(args.python)",
  );
  const firstRunnerResource = source.indexOf("const tempRoot = await mkdtemp", resolution);
  assert(resolution > 0);
  assert(firstRunnerResource > resolution);
  assert.equal(source.match(/\bargs\.python\b/g)?.length, 1);
  for (const required of [
    "declaredMigrationHeads(pythonExecutable, platformEnv)",
    "runCommand(pythonExecutable, [\"-m\", \"alembic\"",
    "spawn(pythonExecutable, [",
    "seedRelayNativeChannel(pythonExecutable, relayDatabaseUrl",
    "postgresFingerprints(\n      pythonExecutable",
    "runCommand(pythonExecutable, [\n      \"-m\", \"pytest\"",
  ]) assert(source.includes(required), `runner did not use canonical Python for ${required}`);
});

test("canonical Python resolution preserves a POSIX virtual-environment executable", async () => {
  const virtualEnvironmentPython = "/workspace/.venv-cost-ci/bin/python";
  const resolved = await resolveCanonicalPythonExecutable(virtualEnvironmentPython, {
    platform: "linux",
    run: async (command, args) => {
      assert.equal(command, virtualEnvironmentPython);
      assert.match(args[3], /sys\.platform == 'win32'/);
      assert.match(args[3], /os\.path\.abspath\(sys\.executable\)/);
      return { stdout: `${virtualEnvironmentPython}\n`, stderr: "" };
    },
    inspect: async (path) => {
      assert.equal(path, virtualEnvironmentPython);
      return { isFile: () => true };
    },
  });
  assert.equal(resolved, virtualEnvironmentPython);
});

test("Platform probe rejects a Windows launcher PID and accepts the canonical Python PID", () => {
  const nonce = "acceptance-process-nonce-value-123456789";
  const launcherPid = 32848;
  const pythonPid = 33656;
  const response = {
    httpStatus: 200,
    nonceHeader: nonce,
    pidHeader: String(pythonPid),
    body: { status: "ok", service: "customer-platform" },
  };

  assert.deepEqual(
    evaluatePlatformProbeBinding(response, { nonce, pid: launcherPid }),
    {
      fetch_ok: true,
      http_status: 200,
      nonce_bound: true,
      pid_bound: false,
      body_bound: true,
    },
  );
  assert.deepEqual(
    evaluatePlatformProbeBinding(response, { nonce, pid: pythonPid }),
    {
      fetch_ok: true,
      http_status: 200,
      nonce_bound: true,
      pid_bound: true,
      body_bound: true,
    },
  );
});

test("Platform source snapshot binds runtime, migrations, and cost harness", async () => {
  const snapshot = await platformSourceSnapshot();
  assert.equal(snapshot.format, "sorted-portable-path-nul-content-nul-v1");
  assert.equal(snapshot.file_count, snapshot.files.length);
  assert(snapshot.file_count > 100);
  for (const required of [
    "backend/platform/platform_api/main.py",
    "backend/platform/platform_api/services/channel_costs.py",
    "backend/platform/platform_api/services/provider_alerts.py",
    "backend/platform/migrations/env.py",
    "backend/platform/migrations/versions/0023_download_gateway_registration_attempts.py",
    "backend/platform/migrations/versions/0028_provider_alert_bridge.py",
    "backend/platform/migrations/versions/0029_artifact_input_promotion.py",
    "backend/platform/Dockerfile",
    "backend/platform/alembic.ini",
    "backend/platform/requirements.txt",
    "backend/platform/tests/test_provider_alert_bridge.py",
    "backend/platform/tests/integration/cost_acceptance_server.py",
    "backend/platform/tests/integration/test_new_api_channel_cost_delivery.py",
  ]) assert(snapshot.files.includes(required), `Platform snapshot is missing ${required}`);
  for (const file of snapshot.files) {
    const segments = file.toLowerCase().split("/");
    const testsIndex = segments.indexOf("tests");
    const testSegments = testsIndex === -1 ? [] : segments.slice(testsIndex + 1);
    assert(
      !testSegments.some((segment) => segment.startsWith("_tmp")),
      `Platform snapshot contains generated pytest state: ${file}`,
    );
    assert(
      !testSegments.some((segment) => ["artifact", "artifacts", "evidence", "temp", "tmp"].includes(segment)),
      `Platform snapshot contains generated evidence/temp state: ${file}`,
    );
    assert(!segments.includes("__pycache__"), `Platform snapshot contains Python cache state: ${file}`);
  }
  for (const excluded of [
    "backend/platform/tests/integration/evidence/new_api_channel_cost_final_0020_20260807.json",
    "backend/platform/tests/integration/evidence/new_api_channel_cost_final_20260807.json",
  ]) assert(!snapshot.files.includes(excluded), `Platform snapshot contains old evidence ${excluded}`);
  assert.match(snapshot.sha1, /^[0-9a-f]{40}$/);
  assert.match(snapshot.sha256, /^sha256:[0-9a-f]{64}$/);
});

test("Platform snapshot ignores generated test directories without hiding source trees", () => {
  for (const generated of [
    "_tmp_pytest_new_run",
    "_tmp_provider_adapter_run",
    "evidence",
    "artifacts",
    "tmp",
    "temp",
  ]) assert.equal(platformSnapshotDirectoryIgnored(generated, "tests"), true);

  for (const source of ["integration", "fixtures", "services", "versions"]) {
    assert.equal(platformSnapshotDirectoryIgnored(source, "tests"), false);
  }
  assert.equal(platformSnapshotDirectoryIgnored("evidence", "platform_api"), false);
  assert.equal(platformSnapshotDirectoryIgnored("__pycache__", "platform_api"), true);
});

test("runtime identity must agree with compiled snapshot, image ID, and OCI labels", async () => {
  const snapshot = await relaySourceSnapshot();
  const imageId = `sha256:${"9".repeat(64)}`;
  const labels = expectedCandidateImageLabels(snapshot, CANDIDATE_UPSTREAM_GIT_REVISION);
  const identity = {
    schema_version: 1,
    kind: "relay_runtime_build_identity",
    candidate: {
      instance_id: "11111111-2222-4333-8444-555555555555",
      upstream_git_revision: CANDIDATE_UPSTREAM_GIT_REVISION,
      source_git_revision: snapshot.sha1,
      source_snapshot_sha256: snapshot.sha256,
      source_snapshot_file_count: snapshot.file_count,
      image_digest: imageId,
    },
  };
  assert.deepEqual(validateRuntimeIdentity(identity, snapshot, imageId, labels), []);
  const forged = structuredClone(identity);
  forged.candidate.source_snapshot_sha256 = `sha256:${"8".repeat(64)}`;
  assert(validateRuntimeIdentity(forged, snapshot, imageId, labels).length >= 2);
});

test("offline compiled identity parser is strict and diagnostic-free", () => {
  const value = {
    schema_version: 1,
    kind: "relay_compiled_build_identity",
    upstream_git_revision: "1".repeat(40),
    source_revision: "2".repeat(40),
    source_snapshot_sha256: `sha256:${"3".repeat(64)}`,
    source_snapshot_file_count: 123,
    route_acceptance_trust_keys_sha256: `sha256:${"4".repeat(64)}`,
  };
  assert.deepEqual(parseCompiledBuildIdentity(JSON.stringify(value)), value);
  assert.throws(
    () => parseCompiledBuildIdentity(JSON.stringify({ ...value, extra: true })),
    /schema is invalid/,
  );
  assert.throws(() => parseCompiledBuildIdentity(JSON.stringify(value), "warning"), /diagnostics/);
  assert.throws(
    () => parseCompiledBuildIdentity(JSON.stringify({
      ...value,
      route_acceptance_trust_keys_sha256: `sha256:${"0".repeat(64)}`,
    })),
    /values are invalid/,
  );
});

test("evidence is create-only and its canonical payload digest detects tampering", async () => {
  const directory = resolve(tmpdir(), `cost-evidence-${process.pid}-${Date.now()}`);
  const path = resolve(directory, "acceptance.json");
  const envelope = buildEvidenceEnvelope({
    schema_version: 1,
    kind: "relay_platform_cross_service_cost_acceptance",
    status: "PASS",
    verified: true,
  });
  try {
    assert(validateEvidenceEnvelope(envelope));
    await writeCreateOnlyEvidence(path, envelope);
    const persisted = JSON.parse(await readFile(path, "utf8"));
    assert(validateEvidenceEnvelope(persisted));
    persisted.payload.status = "FAIL";
    assert.equal(validateEvidenceEnvelope(persisted), false);
    await assert.rejects(() => writeCreateOnlyEvidence(path, envelope), /EEXIST/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("runner owns unique resources and has no broad Docker cleanup path", async () => {
  const source = await readFile(
    new URL("../scripts/run-cross-service-cost-acceptance.mjs", import.meta.url),
    "utf8",
  );
  assert.match(source, /ai-video-cost-\$\{runSuffix\}/);
  assert.match(source, /assertResourcesRemoved/);
  assert.match(source, /"volume",\s*"create"/);
  assert.match(source, /"volume",\s*"rm"/);
  assert.match(source, /assertResourcesRemoved\(containers, volumes, network\)/);
  assert.doesNotMatch(source, /docker\s+system\s+prune/i);
  assert.doesNotMatch(source, /docker\s+container\s+prune/i);
  assert.doesNotMatch(source, /docker\s+network\s+prune/i);
  assert.doesNotMatch(source, /docker\s+volume\s+prune/i);
  assert.doesNotMatch(source, /compose[^\n]+down/i);
});

test("runner-owned data volumes require exact labels and exclusive bindings", () => {
  const expected = {
    name: "ai-video-cost-123-platform-pg-data",
    role: "platform-postgresql",
    destination: "/var/lib/postgresql/data",
    runSuffix: "123",
  };
  const volume = {
    Name: expected.name,
    Driver: "local",
    Scope: "local",
    Labels: {
      "ai.video.acceptance-run": expected.runSuffix,
      "ai.video.acceptance-role": expected.role,
    },
  };
  const container = {
    State: { Running: true },
    Mounts: [{
      Type: "volume",
      Name: expected.name,
      Destination: expected.destination,
      RW: true,
    }],
  };
  assert.deepEqual(validateRunnerOwnedVolumeBinding(container, volume, expected), []);

  const anonymousExtra = structuredClone(container);
  anonymousExtra.Mounts.push({
    Type: "volume",
    Name: "untracked-anonymous-volume",
    Destination: "/unexpected",
    RW: true,
  });
  assert.match(
    validateRunnerOwnedVolumeBinding(anonymousExtra, volume, expected).join("; "),
    /exactly one Docker volume/,
  );

  const wrongLabels = structuredClone(volume);
  wrongLabels.Labels["ai.video.acceptance-run"] = "another-run";
  assert.match(
    validateRunnerOwnedVolumeBinding(container, wrongLabels, expected).join("; "),
    /run label does not match/,
  );
});

test("cross-service cost acceptance exercises the Relay runtime materializer", async () => {
  const [runner, integration] = await Promise.all([
    readFile(
      new URL("../scripts/run-cross-service-cost-acceptance.mjs", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL(
        "../backend/platform/tests/integration/test_new_api_channel_cost_delivery.py",
        import.meta.url,
      ),
      "utf8",
    ),
  ]);

  for (const required of [
    "seedRelayNativeChannel",
    "waitForRelayPublicStatus",
    "relayBootstrap",
    "RELAY_COMPAT_MODEL_ROUTES_JSON",
    "RELAY_PROVIDER_CONTRACT_RATES_JSON",
    "NEW_API_CHANNEL_COST_IT_URL",
    "NEW_API_CHANNEL_COST_IT_CLIENT_ID",
    "NEW_API_CHANNEL_COST_IT_API_KEY",
    "NEW_API_CHANNEL_COST_IT_SERVICE_TENANT_ID",
    "NEW_API_CHANNEL_COST_IT_CONTRACT_RATE_ID",
  ]) assert(runner.includes(required), `runtime materializer runner is missing ${required}`);

  for (const required of [
    "/v1/generations",
    "platform_generation_jobs",
    "platform_provider_terminal_outcomes",
    "platform_provider_contract_rates",
    "platform_company_id",
    "platform_task_id",
    "job[\"tenant_id\"] != company_id",
    "_wait_for_materialized_cost",
    "TaskStatus.PROCESSING",
  ]) assert(integration.includes(required), `runtime materializer test is missing ${required}`);

  assert(!integration.includes("def _insert_new_api_event"));
  assert(integration.includes("def _insert_rejection_delivery_fixture"));
  const outcomeHelper = integration.slice(
    integration.indexOf("def _record_provider_success_outcomes"),
    integration.indexOf("def _wait_for_materialized_cost"),
  );
  assert(!outcomeHelper.includes("platform_channel_cost_events"));
  const positiveFlow = integration.slice(
    integration.indexOf("def test_real_new_api_runtime_materializes_and_delivers"),
    integration.indexOf("wrong_company_payload ="),
  );
  assert(!positiveFlow.includes("_insert_rejection_delivery_fixture"));
});
