#!/usr/bin/env node

import { createHash, randomBytes, randomUUID } from "node:crypto";
import { open, mkdir } from "node:fs/promises";
import { createServer } from "node:net";
import { dirname, resolve } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

import {
  CANDIDATE_UPSTREAM_GIT_REVISION,
  REQUIRED_FAULT_SCENARIOS,
  canonicalJson,
  validateFaultRunResult,
  validateFaultStart,
} from "./relay-migration-acceptance.mjs";
import {
  assertSecretFreeEvidence,
  candidateImageBuildLabelArgs,
  harnessSourceSnapshot,
  relaySourceSnapshot,
  validateCandidateImageLabels,
} from "./relay-fault-source-snapshot.mjs";

const SCENARIOS = Object.freeze(Object.keys(REQUIRED_FAULT_SCENARIOS));

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const workspace = resolve(scriptDirectory, "..");
const composeFile = resolve(workspace, "tests", "relay-fault-harness", "docker-compose.yml");

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function runCommand(command, args, options = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd ?? workspace,
      env: options.env ?? process.env,
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => {
      const result = {
        code,
        stdout: Buffer.concat(stdout).toString("utf8").trim(),
        stderr: Buffer.concat(stderr).toString("utf8").trim(),
      };
      if (code === 0 || options.allowFailure) resolvePromise(result);
      else reject(new Error(`${command} ${args.join(" ")} failed (${code}): ${result.stderr.slice(-2000)}`));
    });
  });
}

async function inspectCandidateImage(image, snapshot) {
  const [identity, labelResult] = await Promise.all([
    runCommand("docker", ["image", "inspect", image, "--format", "{{.Id}}"]),
    runCommand("docker", ["image", "inspect", image, "--format", "{{json .Config.Labels}}"]),
  ]);
  const imageDigest = identity.stdout.trim();
  if (!/^sha256:[0-9a-f]{64}$/.test(imageDigest)) throw new Error("candidate image has no immutable Docker image id");
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
  await new Promise((resolvePromise, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolvePromise);
  });
  const port = server.address().port;
  await new Promise((resolvePromise) => server.close(resolvePromise));
  return port;
}

async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 15_000);
  try {
    const response = await fetch(url, {
      method: options.method ?? "GET",
      headers: options.headers,
      body: options.body === undefined ? undefined : canonicalJson(options.body),
      signal: controller.signal,
    });
    const text = await response.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch {}
    return { status: response.status, json, text };
  } finally {
    clearTimeout(timeout);
  }
}

async function pollResult(controlURL, token, runID, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let response;
  do {
    response = await requestJson(`${controlURL}/v1/relay-fault-injections/${encodeURIComponent(runID)}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (["PASS", "FAIL"].includes(response.json?.status)) return response;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 200));
  } while (Date.now() < deadline);
  throw new Error(`fault run ${runID} timed out`);
}

async function startFaultRun(controlURL, token, body, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  let response;
  do {
    response = await requestJson(`${controlURL}/v1/relay-fault-injections`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body,
    });
    if (response.status !== 409) return response;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 50));
  } while (Date.now() < deadline);
  return response;
}

async function writeCreateOnly(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`); }
  finally { await handle.close(); }
}

function parseArguments(argv) {
  const parsed = { out: null, keep: false, candidateImage: null, buildCandidateImage: false };
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === "--out") parsed.out = argv[++index];
    else if (argv[index] === "--candidate-image") parsed.candidateImage = argv[++index];
    else if (argv[index] === "--build-candidate-image") parsed.buildCandidateImage = true;
    else if (argv[index] === "--keep") parsed.keep = true;
    else throw new Error(`unknown argument: ${argv[index]}`);
  }
  if (parsed.buildCandidateImage && !parsed.candidateImage) throw new Error("--build-candidate-image requires --candidate-image");
  return parsed;
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  const runSuffix = `${Date.now()}-${randomBytes(3).toString("hex")}`;
  const project = `relay-fault-${runSuffix}`.toLowerCase();
  if (!/^relay-fault-[a-z0-9-]+$/.test(project)) throw new Error("unsafe Compose project name");
  const port = await freePort();
  const sourceSnapshot = await relaySourceSnapshot();
  const harnessSnapshot = await harnessSourceSnapshot();
  const shouldBuildCandidate = !args.candidateImage || args.buildCandidateImage;
  if (!args.candidateImage) args.candidateImage = `ai-video/new-api-relay:fault-${runSuffix}`;
  if (shouldBuildCandidate) {
    await runCommand("docker", [
      "build", ...candidateImageBuildLabelArgs(
        sourceSnapshot,
        CANDIDATE_UPSTREAM_GIT_REVISION,
        process.env.NEW_API_RELAY_ROUTE_ACCEPTANCE_KEYS_SHA256,
      ),
      "--tag", args.candidateImage, resolve(workspace, "backend", "new-api-relay"),
    ]);
  }
  const inspected = await inspectCandidateImage(args.candidateImage, sourceSnapshot);
  const imageDigest = inspected.imageDigest;
  const sourceRevision = sourceSnapshot.sha1;
  const instanceID = randomUUID();
  const token = randomBytes(32).toString("hex");
  const controlImage = `ai-video/relay-fault-control:package-${runSuffix}`;
  const composeEnvironment = {
    ...process.env,
    RELAY_FAULT_CANDIDATE_IMAGE: args.candidateImage,
    RELAY_FAULT_CANDIDATE_IMAGE_DIGEST: imageDigest,
    RELAY_FAULT_CANDIDATE_SOURCE_REVISION: sourceRevision,
    RELAY_FAULT_CANDIDATE_INSTANCE_ID: instanceID,
    RELAY_FAULT_CANDIDATE_UPSTREAM_REVISION: CANDIDATE_UPSTREAM_GIT_REVISION,
    RELAY_FAULT_CONTROL_TOKEN: token,
    RELAY_FAULT_CONTROL_PORT: String(port),
    RELAY_FAULT_CONTROL_IMAGE: controlImage,
    // The package harness imports and faults the production packages in its
    // control process. The candidate container is bound only for immutable
    // provenance and liveness here; the live harness enables its real workers.
    RELAY_FAULT_COMPAT_ENABLED: "false",
  };
  const compose = ["compose", "-p", project, "-f", composeFile];
  const startedAt = new Date().toISOString();
  const results = [];
  let infrastructure = {};
  let executionError = null;
  let teardownPerformed = false;
  let candidate = null;
  try {
    // Serialize the two schema-owning processes on a fresh database. The
    // candidate finishes its migrations first; only then is the control image
    // built and started against that schema.
    await runCommand("docker", [...compose, "up", "-d", "--wait", "postgres", "redis"], { env: composeEnvironment });
    await runCommand("docker", [...compose, "up", "-d", "--wait", "candidate"], { env: composeEnvironment });
    await runCommand("docker", [...compose, "up", "-d", "--build", "--wait", "control"], { env: composeEnvironment });
    const [candidateContainer, controlContainer] = await Promise.all([
      runCommand("docker", [...compose, "ps", "-q", "candidate"], { env: composeEnvironment }),
      runCommand("docker", [...compose, "ps", "-q", "control"], { env: composeEnvironment }),
    ]);
    const candidateContainerID = candidateContainer.stdout.trim();
    const controlContainerID = controlContainer.stdout.trim();
    if (!/^[0-9a-f]{64}$/.test(candidateContainerID)) throw new Error("candidate container id is invalid");
    if (!/^[0-9a-f]{64}$/.test(controlContainerID)) throw new Error("control container id is invalid");
    const [candidateContainerImageResult, controlContainerImageResult] = await Promise.all([
      runCommand("docker", ["container", "inspect", candidateContainerID, "--format", "{{.Image}}"]),
      runCommand("docker", ["container", "inspect", controlContainerID, "--format", "{{.Image}}"]),
    ]);
    const candidateContainerImage = candidateContainerImageResult.stdout.trim();
    const controlContainerImage = controlContainerImageResult.stdout.trim();
    if (candidateContainerImage !== imageDigest) throw new Error("candidate container does not use the bound image digest");
    if (!/^sha256:[0-9a-f]{64}$/.test(controlContainerImage)) throw new Error("control container image digest is invalid");
    infrastructure = {
      compose_project: project,
      candidate_container_id: candidateContainerID,
      control_container_id: controlContainerID,
      candidate_image: args.candidateImage,
      candidate_image_digest: imageDigest,
      candidate_container_image_digest: candidateContainerImage,
      control_container_image_digest: controlContainerImage,
    };
    const controlURL = `http://127.0.0.1:${port}`;
    const health = await requestJson(`${controlURL}/health`);
    if (health.status !== 200) throw new Error(`fault control health returned ${health.status}`);
    const provenance = await requestJson(`${controlURL}/v1/candidate-provenance`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (provenance.status !== 200) throw new Error(`candidate provenance returned ${provenance.status}`);
    candidate = provenance.json?.candidate;
    if (!candidate || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(candidate.instance_id) ||
        candidate.upstream_git_revision !== CANDIDATE_UPSTREAM_GIT_REVISION || candidate.source_git_revision !== sourceRevision || candidate.image_digest !== imageDigest) {
      throw new Error("candidate process provenance does not match the bound image and source snapshot");
    }
    infrastructure.candidate_process_instance_id = candidate.instance_id;
    for (const scenario of SCENARIOS) {
      const requestedAtUtc = new Date().toISOString();
      const runNonce = randomUUID();
      const started = await startFaultRun(controlURL, token, {
          schema_version: 1, scenario, target: "new_api_candidate",
          model: "fault.video.v1", mode: "text_to_video", run_nonce: runNonce,
          requested_at_utc: requestedAtUtc, candidate,
      });
      const expected = {
        scenario, runNonce, target: "new_api_candidate", model: "fault.video.v1", mode: "text_to_video",
        candidate, requestedAtUtc, nowMs: Date.now(), clockSkewMs: 30_000,
      };
      let validationError = null;
      try {
        if (started.status !== 202) throw new Error(`start returned HTTP ${started.status}: ${started.text}`);
        try { validateFaultStart(started.json, expected); }
        catch (error) { validationError = error.message; }
        const completed = await pollResult(controlURL, token, started.json.run_id, 90_000);
        if (completed.status !== 200) throw new Error(`result returned HTTP ${completed.status}`);
        try {
          validateFaultRunResult(completed.json, {
            ...expected, runId: started.json.run_id, acceptedAtUtc: started.json.accepted_at_utc,
            nowMs: Date.now(), timeoutMs: 90_000,
          });
        } catch (error) {
          validationError = validationError ? `${validationError}; ${error.message}` : error.message;
        }
        results.push({ scenario, start: started.json, result: completed.json, validation_error: validationError });
      } catch (error) {
        results.push({ scenario, start: started.json, result: null, validation_error: error.message });
      }
    }
  } catch (error) {
    executionError = error.message;
  } finally {
    if (!args.keep) {
      const teardown = await runCommand("docker", [...compose, "down", "--volumes", "--remove-orphans"], {
        env: composeEnvironment,
        allowFailure: true,
      });
      const cleanup = await verifyComposeProjectRemoved(project);
      const imageRemoval = await runCommand("docker", ["image", "rm", controlImage], { allowFailure: true });
      const remainingImage = await runCommand("docker", ["image", "inspect", controlImage], { allowFailure: true });
      const controlImageRemoved = imageRemoval.code === 0 && remainingImage.code !== 0;
      teardownPerformed = teardown.code === 0 && Object.values(cleanup).every(Boolean) && controlImageRemoved;
      infrastructure.cleanup_verification = cleanup;
      infrastructure.postgresql_redis_volumes_removed = cleanup.volumes_removed;
      infrastructure.control_image_removed_after_run = controlImageRemoved;
    }
  }
  const completedAt = new Date().toISOString();
  const [postSourceSnapshot, postHarnessSnapshot] = await Promise.all([relaySourceSnapshot(), harnessSourceSnapshot()]);
  const sourceFrozen = sourceSnapshot.sha256 === postSourceSnapshot.sha256;
  const harnessFrozen = harnessSnapshot.sha256 === postHarnessSnapshot.sha256;
  const packageLevelPassed = sourceFrozen && harnessFrozen && !args.keep && teardownPerformed && !executionError && results.length === SCENARIOS.length && results.every((entry) => entry.result?.status === "PASS" && !entry.validation_error);
  const unsigned = {
    schema_version: 1,
    kind: "relay_new_api_package_level_fault_injection_acceptance",
    status: packageLevelPassed ? "PARTIAL_PASS" : "FAIL",
    package_level_status: packageLevelPassed ? "PASS" : "FAIL",
    live_candidate_process_fault_injection_status: "BLOCKED",
    production_cutover_gate_satisfied: false,
    execution_scope: {
      faulted_process: "test-only control process importing current new-api production model/service packages",
      dependencies: ["isolated PostgreSQL 16", "isolated Redis 7 AOF"],
      live_candidate_process: "provenance and liveness attested only; its API/worker process was not faulted",
    },
    started_at_utc: startedAt,
    completed_at_utc: completedAt,
    candidate: {
      instance_id: candidate?.instance_id || null,
      image_digest: imageDigest,
      image_tag: args.candidateImage,
      source_revision: sourceRevision,
      upstream_revision: CANDIDATE_UPSTREAM_GIT_REVISION,
      source_revision_attestation: "candidate-image-label-bound-complete-runtime-build-input-snapshot-sha1",
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
    },
    infrastructure,
    scenarios: results,
    execution_error: executionError,
    teardown_performed: teardownPerformed,
  };
  assertSecretFreeEvidence(unsigned, [
    token,
    "fault-platform-api-key",
    "fault-native-token",
    "relay-fault-postgres-password",
    "relay-fault-session-secret-32-bytes-minimum",
    "relay-fault-crypto-secret-32-bytes-minimum",
  ]);
  const report = { ...unsigned, integrity: { canonical_sha256: sha256(canonicalJson(unsigned)) } };
  const defaultName = completedAt.replace(/[:.]/g, "-") + ".json";
  const output = resolve(args.out || resolve(workspace, "artifacts", "relay-fault-acceptance", defaultName));
  await writeCreateOnly(output, report);
  process.stdout.write(`${JSON.stringify({ status: report.status, report: output, scenarios: results.map((entry) => ({ scenario: entry.scenario, status: entry.result?.status || "NOT_RUN", validation_error: entry.validation_error })) }, null, 2)}\n`);
  if (!packageLevelPassed) process.exitCode = 1;
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
