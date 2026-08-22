#!/usr/bin/env node

import { createHash, randomBytes, randomUUID } from "node:crypto";
import { open, mkdir } from "node:fs/promises";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import { CANDIDATE_UPSTREAM_GIT_REVISION, canonicalJson } from "./relay-migration-acceptance.mjs";
import {
  assertSecretFreeEvidence,
  candidateImageBuildLabelArgs,
  harnessSourceSnapshot,
  relaySourceSnapshot,
  validateCandidateImageLabels,
} from "./relay-fault-source-snapshot.mjs";

const REQUIRED = {
  live_redis_outage: ["api_accepted_during_redis_outage", "postgres_outbox_durable", "recovery_submitted_once"],
  live_provider_response_loss: ["provider_side_effect_observed", "reconciliation_required", "sticky_route_and_slot_retained", "automatic_resubmit_absent"],
  live_candidate_worker_kill: ["provider_side_effect_observed", "candidate_process_replaced", "lease_recovered_without_resubmit", "old_worker_token_fenced"],
};
const CLOCK_SKEW_MS = 30_000;

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const workspace = resolve(scriptDirectory, "..");
const harnessDirectory = resolve(workspace, "tests", "relay-fault-harness");
const composeFile = resolve(harnessDirectory, "docker-compose.yml");
const controlDockerfile = resolve(harnessDirectory, "Dockerfile");
const relayDirectory = resolve(workspace, "backend", "new-api-relay");

function digest(value) { return `sha256:${createHash("sha256").update(value).digest("hex")}`; }

function runCommand(command, args, options = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      cwd: workspace, env: options.env ?? process.env, shell: false, windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [], stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      const result = { code, stdout: Buffer.concat(stdout).toString("utf8").trim(), stderr: Buffer.concat(stderr).toString("utf8").trim() };
      if (code === 0 || options.allowFailure) resolvePromise(result);
      else reject(new Error(`${command} ${args.join(" ")} failed (${code}): ${result.stderr.slice(-3000)}`));
    });
  });
}

async function inspectCandidateImage(image, snapshot) {
  const [identity, labelResult] = await Promise.all([
    runCommand("docker", ["image", "inspect", image, "--format", "{{.Id}}"]),
    runCommand("docker", ["image", "inspect", image, "--format", "{{json .Config.Labels}}"]),
  ]);
  const imageDigest = identity.stdout.trim();
  if (!/^sha256:[0-9a-f]{64}$/.test(imageDigest)) throw new Error("candidate image id is not immutable");
  let labels;
  try { labels = JSON.parse(labelResult.stdout); }
  catch { throw new Error("candidate image labels are not valid JSON"); }
  const labelErrors = validateCandidateImageLabels(labels, snapshot, CANDIDATE_UPSTREAM_GIT_REVISION);
  if (labelErrors.length > 0) throw new Error(labelErrors.join("; "));
  return { imageDigest, labels };
}

async function verifyComposeProjectRemoved(project) {
  const filters = ["--filter", `label=com.docker.compose.project=${project}`, "--quiet"];
  const [containers, volumes, networks] = await Promise.all([
    runCommand("docker", ["container", "ls", "--all", ...filters], { allowFailure: true }),
    runCommand("docker", ["volume", "ls", ...filters], { allowFailure: true }),
    runCommand("docker", ["network", "ls", ...filters], { allowFailure: true }),
  ]);
  return {
    containers_removed: containers.code === 0 && containers.stdout === "",
    volumes_removed: volumes.code === 0 && volumes.stdout === "",
    networks_removed: networks.code === 0 && networks.stdout === "",
  };
}

async function freePort() {
  const server = createServer();
  await new Promise((resolvePromise, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolvePromise); });
  const port = server.address().port;
  await new Promise((resolvePromise) => server.close(resolvePromise));
  return port;
}

async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? 15_000);
  try {
    const response = await fetch(url, {
      method: options.method ?? "GET", headers: options.headers,
      body: options.body === undefined ? undefined : canonicalJson(options.body), signal: controller.signal,
    });
    const text = await response.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch {}
    return { status: response.status, text, json };
  } finally { clearTimeout(timer); }
}

async function writeCreateOnly(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`); } finally { await handle.close(); }
}

function evidenceDigest(entry) {
  return digest(canonicalJson({ id: entry.id, observed_at_utc: entry.observed_at_utc, kind: entry.kind, action: entry.action, data: entry.data }));
}

function parsedTime(value, label, errors) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) errors.push(`${label} is not a valid timestamp`);
  return timestamp;
}

function validateLiveStart(start, expected) {
  const errors = [];
  const check = (condition, message) => { if (!condition) errors.push(message); };
  check(start?.schema_version === 1, "start schema mismatch");
  check(typeof start?.run_id === "string" && /^[0-9a-f-]{36}$/i.test(start.run_id), "start run id is invalid");
  check(start?.run_nonce === expected.runNonce, "start run nonce mismatch");
  check(start?.scenario === expected.scenario, "start scenario mismatch");
  check(canonicalJson(start?.candidate) === canonicalJson(expected.candidate), "start candidate binding mismatch");
  check(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(start?.candidate?.instance_id || ""), "start candidate instance is not a process UUID");
  const acceptedAt = parsedTime(start?.accepted_at_utc, "start accepted_at_utc", errors);
  if (Number.isFinite(acceptedAt)) {
    check(acceptedAt >= expected.requestSentAtMs - CLOCK_SKEW_MS, "start accepted before request time window");
    check(acceptedAt <= expected.responseReceivedAtMs + CLOCK_SKEW_MS, "start accepted after response time window");
  }
  return errors;
}

function validateLiveResult(result, expected) {
  const errors = [];
  const check = (condition, message) => { if (!condition) errors.push(message); };
  check(result?.schema_version === 1, "schema mismatch");
  check(result?.run_id === expected.runId, "run id mismatch");
  check(result?.run_nonce === expected.runNonce, "run nonce mismatch");
  check(result?.scenario === expected.scenario, "scenario mismatch");
  check(canonicalJson(result?.candidate) === canonicalJson(expected.candidate), "candidate binding mismatch");
  check(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(result?.candidate?.instance_id || ""), "candidate instance is not a process UUID");
  check(result?.execution_scope === "live_candidate_api_and_generation_worker", "execution scope is not live candidate");
  check(result?.status === "PASS", `result status is ${result?.status}`);
  check(result?.request_received_at_utc === expected.acceptedAtUTC, "result request time does not match accepted run");
  const requestReceivedAt = parsedTime(result?.request_received_at_utc, "result request_received_at_utc", errors);
  const startedAt = parsedTime(result?.started_at_utc, "result started_at_utc", errors);
  const completedAt = parsedTime(result?.completed_at_utc, "result completed_at_utc", errors);
  if ([requestReceivedAt, startedAt, completedAt].every(Number.isFinite)) {
    check(startedAt >= requestReceivedAt - CLOCK_SKEW_MS, "result started before accepted time window");
    check(completedAt >= startedAt - CLOCK_SKEW_MS, "result completed before start time window");
    check(completedAt <= expected.resultReceivedAtMs + CLOCK_SKEW_MS, "result completed after collection time window");
  }
  const evidence = new Map();
  for (const entry of result?.raw_evidence || []) {
    check(typeof entry.id === "string" && /^[A-Za-z0-9._-]+$/.test(entry.id) && !evidence.has(entry.id), `invalid/duplicate evidence ${entry.id}`);
    check(typeof entry.kind === "string" && entry.kind.length > 0, `evidence ${entry.id} kind is invalid`);
    check(typeof entry.action === "string" && entry.action.length > 0, `evidence ${entry.id} action is invalid`);
    check(entry.data !== null && typeof entry.data === "object" && !Array.isArray(entry.data), `evidence ${entry.id} data is invalid`);
    check(entry.sha256 === evidenceDigest(entry), `evidence digest mismatch ${entry.id}`);
    const observedAt = parsedTime(entry.observed_at_utc, `evidence ${entry.id} observed_at_utc`, errors);
    if ([observedAt, requestReceivedAt, completedAt].every(Number.isFinite)) {
      check(observedAt >= requestReceivedAt - CLOCK_SKEW_MS, `evidence ${entry.id} predates run time window`);
      check(observedAt <= completedAt + CLOCK_SKEW_MS, `evidence ${entry.id} exceeds completion time window`);
    }
    evidence.set(entry.id, entry);
  }
  for (const assertion of REQUIRED[expected.scenario]) {
    check(result?.assertions?.[assertion] === true, `assertion failed ${assertion}`);
    const ids = result?.assertion_evidence?.[assertion];
    check(Array.isArray(ids) && ids.length > 0 && new Set(ids).size === ids.length && ids.every((id) => evidence.has(id)), `assertion lacks evidence ${assertion}`);
  }
  check(Object.keys(result?.assertions || {}).sort().join("\0") === [...REQUIRED[expected.scenario]].sort().join("\0"), "assertion set mismatch");
  check(Object.keys(result?.assertion_evidence || {}).sort().join("\0") === [...REQUIRED[expected.scenario]].sort().join("\0"), "assertion evidence set mismatch");
  if (expected.scenario === "live_candidate_worker_kill") {
    const replacementEvidence = (result?.assertion_evidence?.candidate_process_replaced || [])
      .map((id) => evidence.get(id))
      .find((entry) => entry?.kind === "docker_process_fault");
    check(Boolean(replacementEvidence), "candidate_process_replaced lacks Docker process evidence");
    const oldContainerID = replacementEvidence?.data?.old_container_id;
    const newContainerID = replacementEvidence?.data?.new_container_id;
    check(typeof oldContainerID === "string" && /^[0-9a-f]{64}$/.test(oldContainerID), "old candidate container id is invalid");
    check(typeof newContainerID === "string" && /^[0-9a-f]{64}$/.test(newContainerID), "replacement candidate container id is invalid");
    check(oldContainerID !== newContainerID, "candidate container was not replaced");
    check(replacementEvidence?.data?.image_digest === expected.candidate.image_digest, "replacement evidence image digest mismatch");
    const oldProcessInstanceID = replacementEvidence?.data?.old_process_instance_id;
    const newProcessInstanceID = replacementEvidence?.data?.new_process_instance_id;
    check(oldProcessInstanceID === expected.candidate.instance_id, "killed process instance does not match the bound candidate");
    check(typeof newProcessInstanceID === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(newProcessInstanceID), "replacement process instance id is invalid");
    check(oldProcessInstanceID !== newProcessInstanceID, "candidate process instance was not replaced");
  }
  return errors;
}

async function pollLive(controlURL, token, runID, timeoutMs, stopAtAwaiting = false) {
  const deadline = Date.now() + timeoutMs;
  let response;
  do {
    response = await requestJson(`${controlURL}/v1/live-fault-runs/${runID}`, { headers: { Authorization: `Bearer ${token}` } });
    if (["PASS", "FAIL"].includes(response.json?.status) || (stopAtAwaiting && response.json?.status === "AWAITING_CANDIDATE_KILL")) return response;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 200));
  } while (Date.now() < deadline);
  throw new Error(`live run ${runID} timed out`);
}

function routeDeclarations(providerKey) {
  const keyFingerprint = createHash("sha256").update(providerKey).digest("hex");
  return {
    "fault.video.v1": [{
      route_id: "live-native-bridge-route", provider_name: "live-native-bridge",
      account_id: "live-account-1", channel_id: 91001, key_index: 0,
      key_fingerprint: keyFingerprint, channel_class: "official",
      upstream_model: "fault-upstream-video", production_ready: true,
      rpm_limit: 100, active_task_limit: 10,
      capabilities: {
        schema_version: 1,
        modes: {
          text_to_video: {
            input_media_types: [], supports_face: false, required_resource_keys: [],
            limits: {
              max_prompt_length: 2000, max_images: 0, max_videos: 0, max_audio: 0,
              duration_seconds: [5], aspect_ratios: ["16:9"], resolutions: ["720p"], output_counts: [1],
            },
          },
        },
      },
    }],
  };
}

function parseArgs(argv) {
  const parsed = { candidateImage: null, buildCandidateImage: false };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--candidate-image") parsed.candidateImage = argv[++index];
    else if (argv[index] === "--build-candidate-image") parsed.buildCandidateImage = true;
    else throw new Error(`unknown argument ${argv[index]}`);
  }
  if (parsed.candidateImage !== null && !parsed.candidateImage) throw new Error("--candidate-image requires a value");
  if (parsed.buildCandidateImage && !parsed.candidateImage) throw new Error("--build-candidate-image requires --candidate-image");
  return parsed;
}

function summarizeEvidence(result) {
  const raw = result?.raw_evidence || [];
  const sideEffects = raw.filter((entry) => entry?.data && (
    Object.hasOwn(entry.data, "provider_effect_count") ||
    Object.hasOwn(entry.data, "effect_count") ||
    Object.hasOwn(entry.data, "provider_task_id")
  ));
  const databaseRowsAndTokens = raw.filter((entry) => entry?.data && (
    Object.hasOwn(entry.data, "outbox_id") ||
    Object.hasOwn(entry.data, "route_admission_id") ||
    Object.hasOwn(entry.data, "worker_lease_token_sha256") ||
    Object.hasOwn(entry.data, "old_worker_token_sha256") ||
    Object.hasOwn(entry.data, "persisted_submission_token_hash")
  ));
  const summary = (entries) => ({
    evidence_ids: entries.map((entry) => entry.id),
    evidence_hashes: entries.map((entry) => entry.sha256),
    canonical_sha256: digest(canonicalJson(entries)),
  });
  return {
    side_effect_service: summary(sideEffects),
    database_rows_and_tokens: summary(databaseRowsAndTokens),
  };
}

async function runIsolatedScenario({ scenario, ordinal, baseSuffix, candidateImage, imageDigest, sourceRevision, controlImage, controlImageDigest }) {
  const isolationSuffix = `${baseSuffix}-${ordinal}`;
  const project = `relay-live-${isolationSuffix}`.toLowerCase();
  if (!/^relay-live-[a-z0-9-]+$/.test(project)) throw new Error("unsafe Compose project name");
  const instanceID = randomUUID();
  const providerKey = "relay-live-provider-key-" + randomBytes(16).toString("hex");
  const admissionToken = "relay-live-admission-" + randomBytes(24).toString("hex");
  const controlToken = randomBytes(32).toString("hex");
  const port = await freePort();
  const environment = {
    ...process.env,
    RELAY_FAULT_CANDIDATE_IMAGE: candidateImage,
    RELAY_FAULT_CANDIDATE_IMAGE_DIGEST: imageDigest,
    RELAY_FAULT_CANDIDATE_SOURCE_REVISION: sourceRevision,
    RELAY_FAULT_CANDIDATE_INSTANCE_ID: instanceID,
    RELAY_FAULT_CANDIDATE_UPSTREAM_REVISION: CANDIDATE_UPSTREAM_GIT_REVISION,
    RELAY_FAULT_CONTROL_TOKEN: controlToken,
    RELAY_FAULT_CONTROL_PORT: String(port),
    RELAY_FAULT_COMPAT_ENABLED: "true",
    RELAY_FAULT_WORKER_ENABLED: "true",
    // Docker Compose on Windows may collapse an empty process environment
    // value to "unset" and apply the compose-file `{}` fallback. A single
    // space survives interpolation and is intentionally empty after the
    // Relay's strings.TrimSpace, leaving routes as the sole capability source.
    RELAY_FAULT_MODEL_CAPABILITIES_JSON: " ",
    RELAY_FAULT_MODEL_ROUTES_JSON: canonicalJson(routeDeclarations(providerKey)),
    RELAY_FAULT_INTERNAL_ADMISSION_TOKEN: admissionToken,
    RELAY_FAULT_INTERNAL_BASE_URL: "http://control:8080",
    RELAY_FAULT_DELAY_QUEUE_NAMESPACE: `relay-live-${isolationSuffix}`,
    RELAY_FAULT_PROVIDER_KEY: providerKey,
    RELAY_FAULT_CONTROL_IMAGE: controlImage,
  };
  const compose = ["compose", "--profile", "live", "-p", project, "-f", composeFile];
  const controlURL = `http://127.0.0.1:${port}`;
  let candidate = null;
  const infrastructure = {
    isolation_scope: "single_scenario_fresh_postgresql_redis_and_candidate",
    compose_project: project,
    candidate_image: candidateImage,
    candidate_image_digest: imageDigest,
  };
  let start = null;
  let result = null;
  let killEvidence = null;
  let executionError = null;
  const orchestrationTimeWindow = {
    allowed_clock_skew_ms: CLOCK_SKEW_MS,
    request_sent_at_utc: null,
    start_response_received_at_utc: null,
    result_response_received_at_utc: null,
  };
  const validationErrors = [];
  try {
    // Serialize schema owners. Starting the upstream bootstrap and the control
    // harness together can race their independent migrations on a fresh DB.
    await runCommand("docker", [...compose, "up", "-d", "--wait", "postgres", "redis"], { env: environment });
    await runCommand("docker", [...compose, "up", "-d", "--wait", "candidate-bootstrap"], { env: environment });
    await runCommand("docker", [...compose, "stop", "candidate-bootstrap"], { env: environment });
    await runCommand("docker", [...compose, "rm", "-f", "candidate-bootstrap"], { env: environment });
    await runCommand("docker", [...compose, "up", "-d", "--no-build", "--wait", "control"], { env: environment });
    const setup = await requestJson(`${controlURL}/v1/live-setup`, {
      method: "POST", headers: { Authorization: `Bearer ${controlToken}`, "Content-Type": "application/json" }, body: {},
    });
    if (setup.status !== 200) throw new Error(`live channel seed failed: ${setup.status} ${setup.text}`);
    await runCommand("docker", [...compose, "up", "-d", "--wait", "candidate"], { env: environment });
    const initialContainerID = (await runCommand("docker", [...compose, "ps", "-q", "candidate"], { env: environment })).stdout.trim();
    if (!/^[0-9a-f]{64}$/.test(initialContainerID)) throw new Error("candidate container id is invalid");
    const controlContainerID = (await runCommand("docker", [...compose, "ps", "-q", "control"], { env: environment })).stdout.trim();
    if (!/^[0-9a-f]{64}$/.test(controlContainerID)) throw new Error("control container id is invalid");
    const [initialImageResult, controlImageResult] = await Promise.all([
      runCommand("docker", ["container", "inspect", initialContainerID, "--format", "{{.Image}}"], { env: environment }),
      runCommand("docker", ["container", "inspect", controlContainerID, "--format", "{{.Image}}"], { env: environment }),
    ]);
    const initialContainerImage = initialImageResult.stdout.trim();
    const controlContainerImage = controlImageResult.stdout.trim();
    if (initialContainerImage !== imageDigest) throw new Error("candidate container does not use the bound image digest");
    if (controlContainerImage !== controlImageDigest) throw new Error("control container does not use the prebuilt frozen image digest");
    infrastructure.initial_candidate_container_id = initialContainerID;
    infrastructure.initial_candidate_container_image_digest = initialContainerImage;
    infrastructure.control_container_id = controlContainerID;
    infrastructure.control_container_image_digest = controlContainerImage;
    const provenance = await requestJson(`${controlURL}/v1/candidate-provenance`, {
      headers: { Authorization: `Bearer ${controlToken}` },
    });
    candidate = provenance.json?.candidate;
    if (provenance.status !== 200 || !candidate ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(candidate.instance_id) ||
        candidate.upstream_git_revision !== CANDIDATE_UPSTREAM_GIT_REVISION ||
        candidate.source_git_revision !== sourceRevision || candidate.image_digest !== imageDigest) {
      throw new Error("candidate process provenance does not match the bound image and source snapshot");
    }
    infrastructure.candidate_process_instance_id = candidate.instance_id;

    const runNonce = randomUUID();
    const requestSentAt = new Date();
    orchestrationTimeWindow.request_sent_at_utc = requestSentAt.toISOString();
    const startResponse = await requestJson(`${controlURL}/v1/live-fault-runs`, {
      method: "POST", headers: { Authorization: `Bearer ${controlToken}`, "Content-Type": "application/json" },
      body: { schema_version: 1, scenario, run_nonce: runNonce, candidate },
    });
    const startResponseReceivedAt = new Date();
    orchestrationTimeWindow.start_response_received_at_utc = startResponseReceivedAt.toISOString();
    start = startResponse.json;
    if (startResponse.status !== 202) throw new Error(`${scenario} start failed: ${startResponse.status} ${startResponse.text}`);
    validationErrors.push(...validateLiveStart(start, {
      runNonce,
      scenario,
      candidate,
      requestSentAtMs: requestSentAt.getTime(),
      responseReceivedAtMs: startResponseReceivedAt.getTime(),
    }));
    let resultResponse;
    if (scenario === "live_candidate_worker_kill") {
      const awaiting = await pollLive(controlURL, controlToken, start.run_id, 30_000, true);
      if (awaiting.json?.status !== "AWAITING_CANDIDATE_KILL") throw new Error(`worker kill did not reach provider side effect: ${awaiting.text}`);
      const oldContainerID = (await runCommand("docker", [...compose, "ps", "-q", "candidate"], { env: environment })).stdout.trim();
      if (!/^[0-9a-f]{64}$/.test(oldContainerID) || oldContainerID !== initialContainerID) throw new Error("candidate kill target is not the bound initial container");
      const killedAtUTC = new Date().toISOString();
      await runCommand("docker", [...compose, "kill", "-s", "SIGKILL", "candidate"], { env: environment });
      await runCommand("docker", [...compose, "rm", "-f", "candidate"], { env: environment });
      const resumed = await requestJson(`${controlURL}/v1/live-fault-runs/${start.run_id}/resume-after-kill`, {
        method: "POST", headers: { Authorization: `Bearer ${controlToken}`, "Content-Type": "application/json" }, body: {},
      });
      if (resumed.status !== 200) throw new Error(`lease-expiry injection rejected: ${resumed.status} ${resumed.text}`);
      await runCommand("docker", [...compose, "up", "-d", "--wait", "candidate"], { env: environment });
      const newContainerID = (await runCommand("docker", [...compose, "ps", "-q", "candidate"], { env: environment })).stdout.trim();
      const replacementImage = (await runCommand("docker", ["container", "inspect", newContainerID, "--format", "{{.Image}}"], { env: environment })).stdout.trim();
      if (!/^[0-9a-f]{64}$/.test(newContainerID) || oldContainerID === newContainerID) throw new Error("candidate container was not replaced");
      if (replacementImage !== imageDigest) throw new Error("replacement candidate image digest changed");
      const replacementProvenance = await requestJson(`${controlURL}/v1/candidate-provenance`, {
        headers: { Authorization: `Bearer ${controlToken}` },
      });
      const replacementCandidate = replacementProvenance.json?.candidate;
      if (replacementProvenance.status !== 200 || !replacementCandidate ||
          !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(replacementCandidate.instance_id) ||
          replacementCandidate.instance_id === candidate.instance_id ||
          replacementCandidate.upstream_git_revision !== candidate.upstream_git_revision ||
          replacementCandidate.source_git_revision !== candidate.source_git_revision ||
          replacementCandidate.image_digest !== candidate.image_digest) {
        throw new Error("replacement candidate process provenance is not a new instance of the bound image");
      }
      killEvidence = {
        id: "docker-candidate-replacement", observed_at_utc: new Date().toISOString(),
        kind: "docker_process_fault", action: "SIGKILL old candidate and create replacement from identical image",
        data: {
          old_container_id: oldContainerID, new_container_id: newContainerID,
          old_process_instance_id: candidate.instance_id, new_process_instance_id: replacementCandidate.instance_id,
          image_digest: imageDigest, killed_at_utc: killedAtUTC, lease_expiry_injection: resumed.json,
        },
      };
      killEvidence.sha256 = evidenceDigest(killEvidence);
      infrastructure.replacement_candidate_container_id = newContainerID;
      infrastructure.replacement_candidate_container_image_digest = replacementImage;
      infrastructure.replacement_candidate_process_instance_id = replacementCandidate.instance_id;
      resultResponse = await pollLive(controlURL, controlToken, start.run_id, 45_000);
    } else {
      resultResponse = await pollLive(controlURL, controlToken, start.run_id, 30_000);
    }
    const resultResponseReceivedAt = new Date();
    orchestrationTimeWindow.result_response_received_at_utc = resultResponseReceivedAt.toISOString();
    result = resultResponse.json;
    if (killEvidence) {
      if ((result?.raw_evidence || []).some((entry) => entry?.id === killEvidence.id)) throw new Error("Docker replacement evidence id collides with candidate evidence");
      result = {
        ...result,
        raw_evidence: [...(result?.raw_evidence || []), killEvidence],
        assertion_evidence: {
          ...(result?.assertion_evidence || {}),
          candidate_process_replaced: [
            ...(result?.assertion_evidence?.candidate_process_replaced || []),
            killEvidence.id,
          ],
        },
      };
    }
    validationErrors.push(...validateLiveResult(result, {
      runId: start.run_id,
      runNonce,
      scenario,
      candidate,
      acceptedAtUTC: start.accepted_at_utc,
      resultReceivedAtMs: resultResponseReceivedAt.getTime(),
    }));
    if (scenario === "live_candidate_worker_kill" && !killEvidence) validationErrors.push("missing Docker kill/replacement evidence");
    if (killEvidence) {
      if (killEvidence.sha256 !== evidenceDigest(killEvidence)) validationErrors.push("Docker replacement evidence digest mismatch");
      const killedAt = parsedTime(killEvidence.data?.killed_at_utc, "candidate killed_at_utc", validationErrors);
      const observedAt = parsedTime(killEvidence.observed_at_utc, "candidate replacement observed_at_utc", validationErrors);
      const acceptedAt = Date.parse(start.accepted_at_utc);
      const completedAt = Date.parse(result?.completed_at_utc);
      if ([killedAt, observedAt, acceptedAt, completedAt].every(Number.isFinite)) {
        if (killedAt < acceptedAt - CLOCK_SKEW_MS || killedAt > completedAt + CLOCK_SKEW_MS) validationErrors.push("candidate kill is outside run time window");
        if (observedAt < killedAt - CLOCK_SKEW_MS || observedAt > resultResponseReceivedAt.getTime() + CLOCK_SKEW_MS) validationErrors.push("candidate replacement is outside collection time window");
      }
    }
  } catch (error) {
    executionError = error.message;
    validationErrors.push(`execution failed: ${error.message}`);
    const logs = await runCommand("docker", [...compose, "logs", "--no-color", "--tail", "300", "candidate", "control"], { env: environment, allowFailure: true });
    const logMaterial = logs.stdout + "\n" + logs.stderr;
    infrastructure.failure_logs_sha256 = digest(logMaterial);
    infrastructure.failure_logs_bytes = Buffer.byteLength(logMaterial);
  } finally {
    const teardown = await runCommand("docker", [...compose, "down", "--volumes", "--remove-orphans"], { env: environment, allowFailure: true });
    const cleanup = await verifyComposeProjectRemoved(project);
    infrastructure.cleanup_verification = cleanup;
    infrastructure.teardown_performed = teardown.code === 0 && Object.values(cleanup).every(Boolean);
    infrastructure.postgresql_redis_volumes_removed = cleanup.volumes_removed;
    if (!infrastructure.teardown_performed) validationErrors.push("isolated Compose teardown or resource removal verification failed");
  }
  const scenarioReport = {
    scenario,
    target_scope: "live_candidate_api_and_generation_worker",
    candidate,
    infrastructure,
    start,
    result,
    orchestration_evidence: killEvidence,
    orchestration_time_window: orchestrationTimeWindow,
    evidence_summary: summarizeEvidence(result),
    execution_error: executionError,
    validation_errors: validationErrors,
  };
  assertSecretFreeEvidence(scenarioReport, [
    controlToken,
    providerKey,
    admissionToken,
    "fault-platform-api-key",
    "fault-native-token",
    "relay-fault-postgres-password",
    "relay-fault-session-secret-32-bytes-minimum",
    "relay-fault-crypto-secret-32-bytes-minimum",
    "relay-live-artifact-signing-secret-32-bytes",
  ]);
  return scenarioReport;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const suffix = `${Date.now()}-${randomBytes(3).toString("hex")}`;
  const candidateImage = args.candidateImage || `ai-video/new-api-relay:live-fault-${suffix}`;
  const sourceSnapshot = await relaySourceSnapshot();
  const harnessSnapshot = await harnessSourceSnapshot();
  if (!args.candidateImage || args.buildCandidateImage) {
    await runCommand("docker", [
      "build", ...candidateImageBuildLabelArgs(
        sourceSnapshot,
        CANDIDATE_UPSTREAM_GIT_REVISION,
        process.env.NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256,
      ),
      "--tag", candidateImage, resolve(workspace, "backend", "new-api-relay"),
    ]);
  }
  const inspected = await inspectCandidateImage(candidateImage, sourceSnapshot);
  const imageDigest = inspected.imageDigest;
  const sourceRevision = sourceSnapshot.sha1;
  const controlImage = `ai-video/relay-fault-control:live-${suffix}`;
  await runCommand("docker", [
    "build", "--file", controlDockerfile,
    "--build-context", `harness=${harnessDirectory}`,
    "--tag", controlImage,
    relayDirectory,
  ]);
  const controlImageDigest = (await runCommand("docker", ["image", "inspect", controlImage, "--format", "{{.Id}}"]))
    .stdout.trim();
  if (!/^sha256:[0-9a-f]{64}$/.test(controlImageDigest)) throw new Error("prebuilt control image digest is invalid");
  const startedAt = new Date().toISOString();
  const scenarios = [];
  let controlImageRemoved = false;
  try {
    for (const [ordinal, scenario] of Object.keys(REQUIRED).entries()) {
      scenarios.push(await runIsolatedScenario({
        scenario,
        ordinal: ordinal + 1,
        baseSuffix: suffix,
        candidateImage,
        imageDigest,
        sourceRevision,
        controlImage,
        controlImageDigest,
      }));
    }
  } finally {
    const removal = await runCommand("docker", ["image", "rm", controlImage], { allowFailure: true });
    const remaining = await runCommand("docker", ["image", "inspect", controlImage], { allowFailure: true });
    controlImageRemoved = removal.code === 0 && remaining.code !== 0;
  }
  const [postSourceSnapshot, postHarnessSnapshot] = await Promise.all([relaySourceSnapshot(), harnessSourceSnapshot()]);
  const sourceFrozen = sourceSnapshot.sha256 === postSourceSnapshot.sha256;
  const harnessFrozen = harnessSnapshot.sha256 === postHarnessSnapshot.sha256;
  const controlImageDigests = scenarios.map((entry) => entry.infrastructure.control_container_image_digest);
  const controlImageFrozen = controlImageDigests.length === Object.keys(REQUIRED).length &&
    controlImageDigests.every((value) => /^sha256:[0-9a-f]{64}$/.test(value)) &&
    controlImageDigests.every((value) => value === controlImageDigest);
  const liveCovered = sourceFrozen && harnessFrozen && controlImageFrozen && controlImageRemoved && scenarios.length === Object.keys(REQUIRED).length && scenarios.every((entry) => (
    entry.result?.status === "PASS" &&
    entry.validation_errors.length === 0 &&
    entry.infrastructure.teardown_performed === true
  ));
  const unsigned = {
    schema_version: 1,
    kind: "relay_new_api_live_candidate_fault_injection_acceptance",
    status: liveCovered ? "PARTIAL_PASS" : "FAIL",
    live_candidate_covered_scenarios_status: liveCovered ? "PASS" : "FAIL",
    full_fault_matrix_status: "BLOCKED",
    production_cutover_gate_satisfied: false,
    started_at_utc: startedAt,
    completed_at_utc: new Date().toISOString(),
    candidate_build: {
      upstream_git_revision: CANDIDATE_UPSTREAM_GIT_REVISION,
      source_git_revision: sourceRevision,
      image_digest: imageDigest,
      image_tag: candidateImage,
      source_snapshot_sha256: sourceSnapshot.sha256,
      source_snapshot_format: sourceSnapshot.format,
      source_snapshot_file_count: sourceSnapshot.file_count,
      source_snapshot_files: sourceSnapshot.files,
      image_source_labels_verified: true,
    },
    evidence_generator: {
      harness_source_snapshot_sha256: harnessSnapshot.sha256,
      harness_source_snapshot_format: harnessSnapshot.format,
      harness_source_snapshot_file_count: harnessSnapshot.file_count,
      harness_source_snapshot_files: harnessSnapshot.files,
      source_unchanged_during_run: sourceFrozen,
      harness_unchanged_during_run: harnessFrozen,
      post_run_source_snapshot_sha256: postSourceSnapshot.sha256,
      post_run_harness_snapshot_sha256: postHarnessSnapshot.sha256,
      control_image_frozen_across_scenarios: controlImageFrozen,
      control_container_image_digest: controlImageFrozen ? controlImageDigests[0] : null,
      control_image_built_once: true,
      control_image_removed_after_run: controlImageRemoved,
    },
    isolation: {
      strategy: "one fresh Compose project and fresh PostgreSQL/Redis volumes per scenario",
      compose_projects: scenarios.map((entry) => entry.infrastructure.compose_project),
      all_teardowns_performed: scenarios.every((entry) => entry.infrastructure.teardown_performed === true),
    },
    scenarios,
    coverage_note: "Only the three named live candidate API/worker scenarios are covered; every other migration fault gate remains blocked.",
  };
  assertSecretFreeEvidence(unsigned);
  const report = { ...unsigned, integrity: { canonical_sha256: digest(canonicalJson(unsigned)) } };
  const path = resolve(workspace, "artifacts", "relay-live-fault-acceptance", `${report.completed_at_utc.replace(/[:.]/g, "-")}.json`);
  await writeCreateOnly(path, report);
  process.stdout.write(`${JSON.stringify({
    status: report.status,
    report: path,
    scenarios: scenarios.map((entry) => ({
      scenario: entry.scenario,
      status: entry.result?.status || "NOT_RUN",
      compose_project: entry.infrastructure.compose_project,
      validation_errors: entry.validation_errors,
    })),
  }, null, 2)}\n`);
  if (!liveCovered) process.exitCode = 1;
}

if (resolve(process.argv[1] || "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => { process.stderr.write(`${error.stack || error.message}\n`); process.exitCode = 1; });
}

export { CLOCK_SKEW_MS, REQUIRED, evidenceDigest, validateLiveResult, validateLiveStart };
