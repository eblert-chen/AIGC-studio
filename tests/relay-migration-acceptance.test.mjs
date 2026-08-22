import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  CANDIDATE_UPSTREAM_GIT_REVISION,
  REQUIRED_FAULT_SCENARIOS,
  canonicalJson,
  runAcceptance,
  writeImmutableReport,
} from "../scripts/relay-migration-acceptance.mjs";

const CAPABILITY_REVISION = `sha256:${"a".repeat(64)}`;
const CATALOG_REVISION = `sha256:${"b".repeat(64)}`;
const IMAGE_DIGEST = `sha256:${"c".repeat(64)}`;
const FORK_REVISION = "d".repeat(40);
const CANDIDATE_INSTANCE_ID = "relay-new-api-staging-acceptance-1";
const NOW = "2026-08-06T08:00:00.000Z";

function digest(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function baseConfig(oracleUrl = "http://127.0.0.1:18001", candidateUrl = "http://127.0.0.1:18002") {
  return {
    schemaVersion: 2,
    environment: "relay-acceptance-staging",
    environmentClass: "staging",
    oracle: {
      baseUrl: oracleUrl,
      mode: "isolated_offline_oracle",
      productionAdmissionAllowed: false,
    },
    candidate: {
      baseUrl: candidateUrl,
      upstreamGitRevision: CANDIDATE_UPSTREAM_GIT_REVISION,
      gitRevision: FORK_REVISION,
      imageDigest: IMAGE_DIGEST,
      instanceId: CANDIDATE_INSTANCE_ID,
    },
    tenants: [
      { label: "tenant-a", clientId: "client-a", apiKeyEnv: "TEST_RELAY_KEY_A" },
      { label: "tenant-b", clientId: "client-b", apiKeyEnv: "TEST_RELAY_KEY_B" },
    ],
    testCase: {
      model: "acceptance.video.v1",
      mode: "text_to_video",
      generationRequest: {
        inputs: { prompt: "acceptance" },
        output: { duration_seconds: 5, count: 1 },
      },
    },
    publicModes: ["text_to_video"],
  };
}

function sendJson(response, status, body, headers = {}) {
  const data = body === undefined ? "" : JSON.stringify(body);
  response.writeHead(status, {
    ...(body === undefined ? {} : { "content-type": "application/json" }),
    "content-length": Buffer.byteLength(data),
    ...headers,
  });
  response.end(data);
}

async function readJson(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function relayServer(options = {}) {
  const jobs = new Map();
  const idempotency = new Map();
  let sequence = 1;
  const catalog = {
    api_version: "v1",
    schema_version: 1,
    object: "list",
    catalog_revision: CATALOG_REVISION,
    data: [
      {
        api_version: "v1",
        schema_version: 1,
        id: "acceptance.video.v1",
        object: "model",
        capability_revision: CAPABILITY_REVISION,
        capabilities: {
          schema_version: 1,
          modes: {
            text_to_video: {
              input_media_types: [],
              supports_face: false,
              required_resource_keys: [],
              limits: {
                prompt_max_chars: 2000,
                max_inputs: { image: 0, video: 0, audio: 0 },
                durations_seconds: [5],
                aspect_ratios: ["16:9"],
                resolutions: ["720p"],
                output_counts: [1],
              },
            },
          },
        },
      },
    ],
  };
  const server = createServer(async (request, response) => {
    const requestId = request.headers["x-request-id"] || "generated-request-id";
    const authHeaders = {
      "x-request-id": requestId,
      "x-relay-upstream-revision": options.upstreamGitRevision ?? CANDIDATE_UPSTREAM_GIT_REVISION,
      "x-relay-source-revision": options.sourceGitRevision ?? FORK_REVISION,
      "x-relay-image-digest": options.imageDigest ?? IMAGE_DIGEST,
    };
    const clientId = request.headers["x-client-id"];
    const valid =
      (clientId === "client-a" && request.headers["x-api-key"] === "key-a") ||
      (clientId === "client-b" && request.headers["x-api-key"] === "key-b");
    if (!valid) {
      const code = clientId && request.headers["x-api-key"]
        ? "INVALID_CLIENT_CREDENTIALS"
        : "CLIENT_AUTHENTICATION_REQUIRED";
      sendJson(response, 401, { error: { code } }, authHeaders);
      return;
    }
    if (request.method === "GET" && request.url === "/health/ready") {
      sendJson(
        response,
        options.readinessStatus ?? 200,
        { state: options.readinessState ?? "healthy", dependencies: [] },
        {
          ...authHeaders,
          "x-relay-upstream-revision":
            options.readinessUpstreamGitRevision ?? authHeaders["x-relay-upstream-revision"],
          "x-relay-source-revision":
            options.readinessSourceGitRevision ?? authHeaders["x-relay-source-revision"],
          "x-relay-image-digest": options.readinessImageDigest ?? authHeaders["x-relay-image-digest"],
        },
      );
      return;
    }
    if (request.method === "GET" && request.url === "/v1/models") {
      const etag = `"${CATALOG_REVISION}"`;
      if (request.headers["if-none-match"] === etag) {
        sendJson(response, 304, undefined, { ...authHeaders, etag });
      } else {
        sendJson(response, 200, catalog, { ...authHeaders, etag });
      }
      return;
    }
    if (request.method === "POST" && request.url === "/v1/generations") {
      const body = await readJson(request);
      const allowedRoot = new Set([
        "model",
        "mode",
        "expected_capability_revision",
        "inputs",
        "output",
        "metadata",
        "client_reference_id",
        "callback",
      ]);
      const allowedInputs = new Set(["prompt", "assets"]);
      const allowedOutput = new Set([
        "duration_seconds",
        "aspect_ratio",
        "resolution",
        "count",
        "face_enabled",
      ]);
      if (
        Object.keys(body).some((key) => !allowedRoot.has(key)) ||
        Object.keys(body.inputs || {}).some((key) => !allowedInputs.has(key)) ||
        Object.keys(body.output || {}).some((key) => !allowedOutput.has(key))
      ) {
        sendJson(response, 422, { error: { code: "REQUEST_VALIDATION_FAILED" } }, authHeaders);
        return;
      }
      const idem = `${clientId}:${request.headers["idempotency-key"]}`;
      const bodyHash = digest(canonicalJson(body));
      const existing = idempotency.get(idem);
      if (existing && existing.bodyHash !== bodyHash) {
        sendJson(response, 409, { error: { code: "IDEMPOTENCY_KEY_REUSED" } }, authHeaders);
        return;
      }
      let job = existing?.job;
      if (!job) {
        const tail = String(sequence++).padStart(12, "0");
        const id = `00000000-0000-4000-8000-${tail}`;
        const drift = body.expected_capability_revision !== CAPABILITY_REVISION;
        job = {
          id,
          body,
          status: drift ? "failed" : "queued",
          error: drift
            ? { code: "CAPABILITY_REVISION_MISMATCH", message: "revision changed", retryable: false }
            : null,
          createdAt: NOW,
        };
        jobs.set(`${clientId}:${id}`, job);
        idempotency.set(idem, { bodyHash, job });
      }
      sendJson(
        response,
        202,
        {
          api_version: "v1",
          schema_version: 1,
          object: "generation",
          id: job.id,
          job_id: job.id,
          status: job.status,
          idempotent_replay: Boolean(existing),
          expected_capability_revision: job.body.expected_capability_revision,
          capability_revision: job.body.expected_capability_revision,
          reservation_action: job.status === "failed" ? "release" : "hold",
          created_at: job.createdAt,
        },
        authHeaders,
      );
      return;
    }
    const generation = request.url?.match(/^\/v1\/generations\/([^/]+)$/);
    if (request.method === "GET" && generation) {
      const job = jobs.get(`${clientId}:${generation[1]}`);
      if (!job) {
        sendJson(response, 404, { error: { code: "JOB_NOT_FOUND" } }, authHeaders);
        return;
      }
      sendJson(
        response,
        200,
        {
          api_version: "v1",
          schema_version: 1,
          object: "generation",
          id: job.id,
          model: job.body.model,
          mode: job.body.mode,
          inputs: job.body.inputs,
          output: job.body.output,
          metadata: job.body.metadata,
          status: job.status,
          progress: job.status === "failed" ? 0 : 1,
          outputs: [],
          error: job.error,
          expected_capability_revision: job.body.expected_capability_revision,
          capability_revision: job.body.expected_capability_revision,
          reservation_action: job.status === "failed" ? "release" : "hold",
          created_at: job.createdAt,
          updated_at: NOW,
        },
        authHeaders,
      );
      return;
    }
    sendJson(response, 404, { error: { code: "NOT_FOUND" } }, authHeaders);
  });
  return server;
}

function faultServer(options = {}) {
  const runs = new Map();
  return createServer(async (request, response) => {
    if (request.headers.authorization !== "Bearer fault-token") {
      sendJson(response, 401, { error: "unauthorized" });
      return;
    }
    if (request.method === "POST" && request.url === "/v1/relay-fault-injections") {
      const body = await readJson(request);
      const runId = randomUUID();
      const acceptedAt = new Date().toISOString();
      runs.set(runId, { body, acceptedAt });
      const started = {
        schema_version: 1,
        run_id: runId,
        run_nonce: body.run_nonce,
        scenario: body.scenario,
        target: body.target,
        model: body.model,
        mode: body.mode,
        candidate: body.candidate,
        accepted_at_utc: acceptedAt,
      };
      sendJson(response, 202, options.mutateStart ? options.mutateStart(started, body) : started);
      return;
    }
    const match = request.url?.match(/^\/v1\/relay-fault-injections\/(.+)$/);
    if (request.method === "GET" && match && runs.has(match[1])) {
      const { body, acceptedAt } = runs.get(match[1]);
      const completedAt = new Date().toISOString();
      const rawEvidence = REQUIRED_FAULT_SCENARIOS[body.scenario].map((assertion, index) => {
        const core = {
          id: `evidence-${index + 1}`,
          observed_at_utc: completedAt,
          kind: "test_observation",
          action: assertion,
          data: { observed: assertion, source: "bound-test-fixture" },
        };
        return { ...core, sha256: digest(canonicalJson(core)) };
      });
      const result = {
        schema_version: 1,
        run_id: match[1],
        run_nonce: body.run_nonce,
        scenario: body.scenario,
        target: body.target,
        model: body.model,
        mode: body.mode,
        candidate: body.candidate,
        status: "PASS",
        request_received_at_utc: body.requested_at_utc,
        started_at_utc: acceptedAt,
        completed_at_utc: completedAt,
        assertions: Object.fromEntries(
          REQUIRED_FAULT_SCENARIOS[body.scenario].map((assertion) => [assertion, true]),
        ),
        assertion_evidence: Object.fromEntries(
          REQUIRED_FAULT_SCENARIOS[body.scenario].map((assertion, index) => [assertion, [`evidence-${index + 1}`]]),
        ),
        raw_evidence: rawEvidence,
      };
      sendJson(response, 200, options.mutateResult ? options.mutateResult(result, body) : result);
      return;
    }
    sendJson(response, 404, { error: "not found" });
  });
}

async function listen(server) {
  await new Promise((resolvePromise) => server.listen(0, "127.0.0.1", resolvePromise));
  const address = server.address();
  return `http://127.0.0.1:${address.port}`;
}

async function close(server) {
  await new Promise((resolvePromise, reject) =>
    server.close((error) => (error ? reject(error) : resolvePromise())),
  );
}

async function addRealEvidence(config, directory) {
  const evidenceFiles = [];
  for (const label of [
    "provider_task",
    "provider_bill",
    "obs_head",
    "callback_delivery",
    "wallet_settlement",
    "provider_cost_ledger",
  ]) {
    const contents = Buffer.from(JSON.stringify({ label, fixture: true }));
    const path = `${label}.json`;
    await writeFile(join(directory, path), contents);
    evidenceFiles.push({ label, path, expectedSha256: digest(contents) });
  }
  config.realChannelAcceptance = [
    {
      mode: "text_to_video",
      realProvider: true,
      executedAtUtc: NOW,
      route: {
        routeId: "route-staging-1",
        providerName: "provider-staging",
        channelId: "channel-1",
        channelClass: "official",
        accountId: "account-staging-1",
        keyFingerprint: `sha256:${"d".repeat(64)}`,
      },
      provider: { taskReference: "provider-task-1", billReference: "provider-bill-1" },
      obs: {
        bucket: "private-staging",
        objectKey: "acceptance/task-1.mp4",
        sha256: "e".repeat(64),
        head: {
          verified: true,
          etag: "obs-etag-1",
          sizeBytes: 1024,
          contentType: "video/mp4",
          checkedAtUtc: NOW,
        },
      },
      callback: { eventId: "callback-event-1", signatureVerified: true, deliveredAtUtc: NOW },
      platformWallet: {
        taskId: "platform-task-1",
        reservationReference: "wallet-reserve-1",
        settlementReference: "wallet-settle-1",
        action: "settle",
        amountMinor: 100,
        reconciled: true,
      },
      providerCost: {
        ledgerId: "cost-ledger-1",
        idempotencyKey: "cost-idempotency-1",
        externalReference: "provider-bill-1",
        occurredAtUtc: NOW,
        amountMinor: 30,
        channelId: "channel-1",
        channelClass: "official",
        appendOnlyVerified: true,
        singleEventVerified: true,
        idempotentReplayVerified: true,
        idempotencyConflictRejectedVerified: true,
      },
      evidenceFiles,
    },
  ];
}

test("dry validation is fail-closed and can never authorize Python production admission", async () => {
  const report = await runAcceptance(baseConfig(), {
    now: () => new Date(NOW),
    env: { TEST_RELAY_KEY_A: "key-a", TEST_RELAY_KEY_B: "key-b" },
  });

  assert.equal(report.gates.configuration.status, "PASS");
  assert.equal(report.gates["contract.candidate_readiness"].status, "BLOCKED");
  assert.equal(report.gates["contract.models_etag"].status, "BLOCKED");
  assert.equal(report.gates["fault.worker_kill"].status, "BLOCKED");
  assert.equal(report.gates["real_channel.text_to_video"].status, "BLOCKED");
  assert.equal(report.overall.status, "BLOCKED");
  assert.equal(report.overall.technical_acceptance_passed, false);
  assert.equal(report.overall.decision, "NO-GO");
  assert.equal(report.schema_version, 2);
  assert.equal(report.overall.active_production_relay, "new-api-v1");
  assert.equal(report.overall.python_relay_artifact_mode, "offline_historical_oracle_only");
  assert.equal(report.overall.python_relay_production_admission_allowed, false);
  assert.equal(report.execution.python_relay_changed, false);
  const { integrity, ...unsigned } = report;
  assert.equal(integrity.canonical_sha256, digest(canonicalJson(unsigned)));
});

test("missing credentials and fault control stay BLOCKED even when execution is requested", async () => {
  const report = await runAcceptance(baseConfig(), {
    executeContracts: true,
    executeFaults: true,
    now: () => new Date(NOW),
    env: {},
  });

  assert.equal(report.gates["contract.candidate_readiness"].status, "BLOCKED");
  assert.equal(report.gates["contract.auth"].status, "BLOCKED");
  for (const scenario of Object.keys(REQUIRED_FAULT_SCENARIOS)) {
    assert.equal(report.gates[`fault.${scenario}`].status, "BLOCKED");
  }
  assert.equal(report.overall.decision, "NO-GO");
});

test("configuration rejects a floating candidate identity", async () => {
  const config = baseConfig();
  config.candidate.gitRevision = "main";
  config.candidate.imageDigest = "latest";
  delete config.candidate.instanceId;
  const report = await runAcceptance(config, { now: () => new Date(NOW) });

  assert.equal(report.gates.configuration.status, "FAIL");
  assert.equal(report.overall.status, "FAIL");
  assert.equal(report.overall.python_relay_production_admission_allowed, false);
});

test("offline oracle configuration is explicit and rejected in production", async () => {
  const production = baseConfig(
    "https://python-oracle.isolated.example.test",
    "https://new-api.example.test",
  );
  production.environmentClass = "production";
  const productionReport = await runAcceptance(production, { now: () => new Date(NOW) });
  assert.equal(productionReport.gates.configuration.status, "FAIL");
  assert.equal(productionReport.overall.python_relay_production_admission_allowed, false);

  const ambiguous = baseConfig();
  ambiguous.oracle.mode = "staging_peer";
  ambiguous.oracle.productionAdmissionAllowed = true;
  const ambiguousReport = await runAcceptance(ambiguous, { now: () => new Date(NOW) });
  assert.equal(ambiguousReport.gates.configuration.status, "FAIL");
  assert.equal(ambiguousReport.overall.python_relay_production_admission_allowed, false);
});

test("fault PASS is rejected when raw evidence is absent", async (t) => {
  const faults = faultServer({ mutateResult: (result) => ({ ...result, raw_evidence: [] }) });
  const faultUrl = await listen(faults);
  t.after(() => close(faults));
  const config = baseConfig();
  config.faultInjection = {
    controlBaseUrl: faultUrl,
    tokenEnv: "TEST_FAULT_TOKEN",
    confirmIsolatedEnvironment: true,
    pollIntervalMs: 1,
    timeoutMs: 2_000,
  };
  const report = await runAcceptance(config, {
    executeFaults: true,
    env: {
      TEST_RELAY_KEY_A: "key-a",
      TEST_RELAY_KEY_B: "key-b",
      TEST_FAULT_TOKEN: "fault-token",
    },
  });

  assert.equal(report.gates["fault.lease_expiry"].status, "FAIL");
  assert.match(report.gates["fault.lease_expiry"].summary, /no raw evidence/);
});

test("fault result replay is rejected when nonce or candidate build differs", async (t) => {
  const faults = faultServer({
    mutateResult: (result) => ({
      ...result,
      run_nonce: randomUUID(),
      candidate: { ...result.candidate, image_digest: `sha256:${"e".repeat(64)}` },
    }),
  });
  const faultUrl = await listen(faults);
  t.after(() => close(faults));
  const config = baseConfig();
  config.faultInjection = {
    controlBaseUrl: faultUrl,
    tokenEnv: "TEST_FAULT_TOKEN",
    confirmIsolatedEnvironment: true,
    pollIntervalMs: 1,
    timeoutMs: 2_000,
  };
  const report = await runAcceptance(config, {
    executeFaults: true,
    env: {
      TEST_RELAY_KEY_A: "key-a",
      TEST_RELAY_KEY_B: "key-b",
      TEST_FAULT_TOKEN: "fault-token",
    },
  });

  assert.equal(report.gates["fault.worker_kill"].status, "FAIL");
  assert.match(report.gates["fault.worker_kill"].summary, /nonce mismatch/);
});

test("configuration rejects the upstream baseline, placeholder digest, and same-origin oracle", async () => {
  const config = baseConfig("https://relay.example.com", "https://relay.example.com/candidate");
  config.candidate.gitRevision = CANDIDATE_UPSTREAM_GIT_REVISION;
  config.candidate.imageDigest = `sha256:${"0".repeat(64)}`;
  const report = await runAcceptance(config, { now: () => new Date(NOW) });

  assert.equal(report.gates.configuration.status, "FAIL");
  assert.match(report.gates.configuration.summary, /invalid/);
  assert.equal(report.overall.python_relay_production_admission_allowed, false);
});

test("contract comparison executes every required compatibility gate", async (t) => {
  const oracle = relayServer();
  const candidate = relayServer();
  const oracleUrl = await listen(oracle);
  const candidateUrl = await listen(candidate);
  t.after(async () => {
    await Promise.all([close(oracle), close(candidate)]);
  });
  const report = await runAcceptance(baseConfig(oracleUrl, candidateUrl), {
    executeContracts: true,
    now: () => new Date(NOW),
    env: { TEST_RELAY_KEY_A: "key-a", TEST_RELAY_KEY_B: "key-b" },
  });

  for (const id of [
    "contract.candidate_readiness",
    "contract.auth",
    "contract.models_etag",
    "contract.strict_fields",
    "contract.request_id",
    "contract.idempotency",
    "contract.tenant_non_enumeration",
    "contract.status_reservation",
    "contract.revision_pin_drift",
  ]) {
    assert.equal(report.gates[id].status, "PASS", `${id}: ${report.gates[id].summary}`);
    assert.match(report.gates[id].evidence_sha256, /^sha256:[0-9a-f]{64}$/);
  }
  assert.equal(report.overall.status, "BLOCKED", "fault and real-channel evidence remain mandatory");
});

test("an explicit fault control plane plus complete real receipts can satisfy all gates", async (t) => {
  const oracle = relayServer();
  const candidate = relayServer();
  const faults = faultServer();
  const [oracleUrl, candidateUrl, faultUrl] = await Promise.all([
    listen(oracle),
    listen(candidate),
    listen(faults),
  ]);
  t.after(async () => {
    await Promise.all([close(oracle), close(candidate), close(faults)]);
  });
  const directory = await mkdtemp(join(tmpdir(), "relay-acceptance-test-"));
  const config = baseConfig(oracleUrl, candidateUrl);
  config.faultInjection = {
    controlBaseUrl: faultUrl,
    tokenEnv: "TEST_FAULT_TOKEN",
    confirmIsolatedEnvironment: true,
    pollIntervalMs: 1,
    timeoutMs: 2_000,
  };
  await addRealEvidence(config, directory);

  const report = await runAcceptance(config, {
    executeContracts: true,
    executeFaults: true,
    configDir: directory,
    now: () => new Date(NOW),
    env: {
      TEST_RELAY_KEY_A: "key-a",
      TEST_RELAY_KEY_B: "key-b",
      TEST_FAULT_TOKEN: "fault-token",
    },
  });

  assert.equal(report.overall.status, "PASS");
  assert.equal(report.overall.technical_acceptance_passed, true);
  assert.equal(report.overall.python_relay_production_admission_allowed, false);
  assert.equal(report.overall.decision, "OFFLINE_PARITY_PASSED_REQUIRES_EXTERNAL_RELEASE_GATES");
  assert.equal(report.execution.mutating_service_actions_performed, false);
  assert.match(report.integrity.canonical_sha256, /^sha256:[0-9a-f]{64}$/);
  for (const scenario of Object.keys(REQUIRED_FAULT_SCENARIOS)) {
    assert.equal(report.gates[`fault.${scenario}`].status, "PASS");
  }
});

test("candidate readiness fails closed unless state and live provenance match", async (t) => {
  const oracle = relayServer();
  const degraded = relayServer({ readinessState: "degraded" });
  const mismatched = relayServer({ readinessSourceGitRevision: "e".repeat(40) });
  const [oracleUrl, degradedUrl, mismatchedUrl] = await Promise.all([
    listen(oracle),
    listen(degraded),
    listen(mismatched),
  ]);
  t.after(async () => {
    await Promise.all([close(oracle), close(degraded), close(mismatched)]);
  });

  for (const [label, candidateUrl, expectedSummary] of [
    ["degraded", degradedUrl, /state is not healthy/],
    ["mismatched provenance", mismatchedUrl, /source revision does not match/],
  ]) {
    const report = await runAcceptance(baseConfig(oracleUrl, candidateUrl), {
      executeContracts: true,
      now: () => new Date(NOW),
      env: { TEST_RELAY_KEY_A: "key-a", TEST_RELAY_KEY_B: "key-b" },
    });
    assert.equal(report.gates["contract.candidate_readiness"].status, "FAIL", label);
    assert.match(report.gates["contract.candidate_readiness"].summary, expectedSummary, label);
    assert.equal(report.overall.technical_acceptance_passed, false, label);
    assert.equal(report.overall.python_relay_production_admission_allowed, false, label);
  }
});

test("real channel cost evidence requires occurrence time and conflict rejection proof", async () => {
  const directory = await mkdtemp(join(tmpdir(), "relay-cost-evidence-test-"));
  const config = baseConfig();
  await addRealEvidence(config, directory);
  delete config.realChannelAcceptance[0].providerCost.occurredAtUtc;
  delete config.realChannelAcceptance[0].providerCost.idempotencyConflictRejectedVerified;

  const report = await runAcceptance(config, {
    configDir: directory,
    now: () => new Date(NOW),
    env: { TEST_RELAY_KEY_A: "key-a", TEST_RELAY_KEY_B: "key-b" },
  });

  assert.equal(report.gates["real_channel.text_to_video"].status, "BLOCKED");
  assert.match(report.gates["real_channel.text_to_video"].summary, /providerCost\.occurredAtUtc/);
  assert.match(
    report.gates["real_channel.text_to_video"].summary,
    /providerCost\.idempotencyConflictRejectedVerified=true/,
  );
  assert.equal(report.overall.python_relay_production_admission_allowed, false);
});

test("reports are create-only and never overwritten", async () => {
  const directory = await mkdtemp(join(tmpdir(), "relay-report-write-once-"));
  const path = join(directory, "report.json");
  const report = await runAcceptance(baseConfig(), { now: () => new Date(NOW) });
  await writeImmutableReport(path, report);
  const first = await readFile(path, "utf8");
  await assert.rejects(() => writeImmutableReport(path, { replaced: true }), /EEXIST/);
  assert.equal(await readFile(path, "utf8"), first);
});

test("acceptance tool contains no service shutdown or shell execution path", async () => {
  const source = await readFile(
    new URL("../scripts/relay-migration-acceptance.mjs", import.meta.url),
    "utf8",
  );
  assert.doesNotMatch(source, /node:child_process/);
  assert.doesNotMatch(source, /\b(exec|execFile|spawn|fork|unlink|rmSync)\s*\(/);
  assert.doesNotMatch(source, /docker\s+compose\s+(down|stop|rm|kill)/i);
  assert.doesNotMatch(source, /Stop-Process|Remove-Item|PLATFORM_RELAY_BASE_URL\s*=/i);
  assert.doesNotMatch(source, /method:\s*["']DELETE["']/);
});
